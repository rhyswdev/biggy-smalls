"""Splunk: splunk/saved_searches.json and splunk/alerts/*.json (the fixed webhook body: result (first row),
sid, results_link, search_name, owner, app). The result row's _raw is the indexed line, byte for byte."""

from __future__ import annotations

import json
from datetime import timedelta

from smalls.contracts.handles import slugify, splunk_alert_handle, splunk_search_handle
from smalls_data.gen.core import Ctx, content, epoch_s, hexid, rng


class Splunk:
    def __init__(self, ctx: Ctx):
        self.ctx, self.c = ctx, ctx.clock
        self.C = content("splunk.yaml")

    def first_line(self, sig_id: str) -> tuple[str, str, str]:
        """(raw first line, bundle-relative path, host) of the manifest record carrying sig_id."""
        row = next(r for r in self.ctx.rows if r.sig_id == sig_id and r.path.startswith("splunk/raw/"))
        lines = (self.ctx.out / row.path).read_text().split("\n")
        return lines[row.locator - 1], row.path, row.path.rsplit("/", 1)[1].rsplit(".", 1)[0]

    def render(self) -> None:
        ctx = self.ctx
        S = ctx.scenario["splunk"]
        out = []
        for s in S["saved_searches"]:
            c = self.C["searches"][s["name"]]
            d = {"name": s["name"], "search": c["search"], "description": c["description"], "cron_schedule": c["cron"],
                 "dispatch.earliest_time": c["earliest"], "dispatch.latest_time": "now"}
            if "condition" in c:       # a stats search alerts on a condition over its result rows
                d.update({"alert_type": "custom", "alert_condition": c["condition"]})
            else:                      # an event search alerts on its event count
                d.update({"alert_type": "number of events", "alert_comparator": c["comparator"], "alert_threshold": c["threshold"]})
            # Throttled for an hour: one alert per incident, then quiet until the condition clears.
            d.update({"alert.suppress": "1", "alert.suppress.period": "1h", "actions": "webhook",
                      "action.webhook.param.url": self.C["webhook_url"], "is_scheduled": "1", "disabled": "1" if c.get("disabled") else "0",
                      "owner": self.C["owner"], "app": self.C["app"], "updated": self.C["updated"]})
            out.append(d)
            ctx.row(splunk_search_handle(s["name"]), "splunk/saved_searches.json", f"/{len(out) - 1}", self.c.t(self.C["updated"]),
                    "spl.search", "signal" if s["name"] == "Payments pool exhaustion" else "noise")
        ctx.write_json("splunk/saved_searches.json", out)
        sigs = {s["id"]: s for s in ctx.scenario["gateway_signals"] + ctx.scenario["postgres_signals"]
                + [f for f in ctx.scenario["f5"]["flaps"] if f.get("id")]}
        bodies = {}
        n = 0
        for a in S["alerts"]:
            if not a.get("slice", True):
                continue
            t = self.c.t(a["at"])
            key = a.get("duplicate_of") or a["at"]
            if a.get("duplicate_of"):
                body = bodies[a["duplicate_of"]]
            else:
                raw, path, host = self.first_line(sigs[a["first_row"]]["sig_id"])
                st = self.C["sourcetypes"]["/".join(path.split("/")[:3])]
                host = host if st["host"] == "file_stem" else st["host"]
                row_t = next(r for r in ctx.rows if r.sig_id == sigs[a["first_row"]]["sig_id"]).t_utc
                from datetime import datetime
                rt = datetime.fromisoformat(row_t.replace("Z", "+00:00"))
                r = rng(ctx.seed, "sid", a["at"])
                sid = f"scheduler__{self.C['owner'].replace('.', '_')}__{self.C['app']}__RMD5{hexid(r, 16)}_at_{epoch_s(t)}_{r.randint(100, 999)}"
                result = {"_raw": raw, "_time": f"{rt.timestamp():.3f}", "host": host, "source": st["source"].format(host=host),
                          "sourcetype": st["sourcetype"], "index": st["index"], "linecount": "1", "splunk_server": "idx-02.larkspur.internal"}
                body = {"result": result, "sid": sid,
                        "results_link": f"https://splunk.larkspur.internal:8000/app/{self.C['app']}/search?q=%7Cloadjob%20{sid}%20%7C%20head%201&earliest=0&latest=now",
                        "search_name": a["search"], "owner": self.C["owner"], "app": self.C["app"]}
                bodies[a["at"]] = body
            n += 1
            # The receiver names each delivery by when it arrived (data fix 9), so a re-POSTed duplicate is
            # visible: same body and sid, a later file name. The search fired at the sid's time.
            received = t + timedelta(milliseconds=rng(ctx.seed, "receipt", a["at"]).randint(400, 1600))
            rel = f"splunk/alerts/{received.strftime('%Y%m%dT%H%M%S')}.{received.microsecond // 1000:03d}Z-{slugify(a['search'])}.json"
            text = json.dumps(body, indent=2) + "\n"
            ctx.write_text(rel, text)
            fired = self.c.t(a.get("duplicate_of") or a["at"])
            ctx.row(splunk_alert_handle(rel, text), rel, "/", fired, "spl.alert", a["kind"], a.get("sig_id"), t_known=received)
        ctx.count("records:splunk_alerts", n)
