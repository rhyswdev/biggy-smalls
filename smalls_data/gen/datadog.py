"""Datadog: datadog/monitors.json (v1 Monitor list), datadog/events.jsonl (v1 events, one {"event": ...}
per line, the stream export), datadog/metrics/*.json (v1 query responses) and datadog/synthetics.json."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from smalls.contracts.handles import dd_event_handle, dd_monitor_handle, dd_synthetic_handle, metric_handle
from smalls_data.gen.core import Ctx, content, epoch_s, iso_z, ms, rng
from smalls_data.gen.model import HOSTS, Model

TITLE = {"Alert": "Triggered", "Warn": "Warn", "OK": "Recovered"}
ALERT_TYPE = {"Alert": "error", "Warn": "warning", "OK": "success"}
SLACK_CHANNELS = {"@slack-sre-oncall": "sre-oncall"}


def render_message(msg: str, state: str, **v) -> str:
    keep = {"Alert": "is_alert", "Warn": "is_warning", "OK": "is_recovery"}[state]
    def block(m):
        return m.group(2) if m.group(1) == keep else ""
    nl = lambda m: "\n" if m.group(0).endswith("\n") else ""
    out = re.sub(r"\{\{#(is_\w+)\}\}(.*?)\{\{/\1\}\}\n?", lambda m: (block(m) + nl(m)) if block(m) else "", msg, flags=re.S)
    out = re.sub(r"\n +", "\n", re.sub(r" +\n", "\n", out))
    for k, val in v.items():
        out = out.replace("{{" + k + "}}", str(val))
    return out.strip()


class Datadog:
    def __init__(self, ctx: Ctx, model: Model):
        self.ctx, self.m, self.c = ctx, model, ctx.clock
        self.dd = ctx.scenario["datadog"]
        self.content = content("datadog.yaml")
        self.export_from = self.c.t(self.dd["events_export_from"])
        self.events: list[tuple[datetime, dict, dict]] = []   # (t, event, meta)
        self.synced = self.c.t("13:45")
        self.cache: dict[str, tuple[list, list]] = {}         # metric file -> (times, groups), as served

    def monitors(self) -> list[dict]:
        return [m for m in self.dd["monitors"] if m.get("slice", True)]

    def transitions(self, mon: dict, since: datetime | None = None) -> list[dict]:
        if "flap" in mon:
            f = mon["flap"]
            last = self.c.t(f["last_alert_at"])
            out = [{"at": last, "to": "Alert", "kind": "trap", "sig_id": mon.get("sig_id")},
                   {"at": self.c.t(f["recovered_at"]), "to": "OK", "kind": "trap"}]
            t = last - timedelta(minutes=f["every_minutes"])
            while t >= (since or self.export_from):
                out += [{"at": t, "to": "Alert", "kind": "trap"}, {"at": t + timedelta(minutes=f["alert_for_minutes"]), "to": "OK", "kind": "trap"}]
                t -= timedelta(minutes=f["every_minutes"])
            return sorted(out, key=lambda x: x["at"])
        return [dict(x, at=self.c.t(x["at"])) for x in mon.get("transitions", [])]

    def served(self, file: str, t0: datetime, t1: datetime) -> list[float]:
        """Per-minute values of a served series over [t0, t1), groups summed (None and gaps skipped)."""
        times, groups = self.cache[file]
        out = []
        for i, tt in enumerate(times):
            if t0 <= tt < t1:
                vals = [g[1][i] for g in groups if g[1][i] not in (None, ...)]
                if vals:
                    out.append(sum(vals))
        return out

    def error_ratio(self, t0: datetime, t1: datetime) -> float:
        errs = sum(self.served("checkout-api.errors.by_version.json", t0, t1))
        hits = sum(self.served("checkout-api.hits.by_resource.json", t0, t1))
        return 100 * errs / hits if hits else 0.0

    def window(self, mon: dict, t: datetime) -> tuple[datetime, datetime]:
        """The minutes a monitor's query evaluates at t: `last_Nm`, shifted by its evaluation delay."""
        n = int(re.search(r"last_(\d+)m", mon.get("query", "last_5m")).group(1))
        end = t.replace(second=0, microsecond=0) - timedelta(seconds=mon.get("evaluation_delay_s", 0))
        return end - timedelta(minutes=n), end

    def value(self, mon: dict, t: datetime) -> str:
        """{{value}} in a notification: the monitor's query over the served series at t."""
        k = mon["key"]
        if k in ("p90_gw", "p99_gw"):
            vals = self.served(f"payments-gw.{k[:3]}.json", *self.window(mon, t))
            return f"{max(vals):.2f}" if vals else ""
        if k == "co_errors":
            return f"{self.error_ratio(*self.window(mon, t)):.1f}"
        if k == "success_rate":
            vals = self.served("payments.authorize.success_rate.json", *self.window(mon, t))
            return f"{sum(vals) / len(vals):.1f}" if vals else ""
        if k == "co_log_volume":
            return "21483"
        if k == "redis_frag":
            return f"{self.ctx.scenario['model']['redis_fragmentation_ratio']['flap_peak']:.2f}"
        if k == "node_cpu":
            return "96.4"
        if k == "ord_disk":
            return "0.934"
        if k == "search_indexer":
            return "1"
        return ""

    def check_transitions(self, mon: dict) -> None:
        """A metric monitor's scenario transitions must be what its query gives over the served series
        (evaluated each minute). Fails the build otherwise, so a monitor never contradicts its graph."""
        if mon["key"] not in ("co_errors", "success_rate"):
            return
        th = mon["thresholds"]
        below = mon["key"] == "success_rate"
        def state(t):
            if mon["key"] == "co_errors":
                v = self.error_ratio(*self.window(mon, t))
            else:
                vals = self.served("payments.authorize.success_rate.json", *self.window(mon, t))
                v = sum(vals) / len(vals)
            crit = v < th["critical"] if below else v >= th["critical"]
            warn = v < th["warning"] if below else v >= th["warning"]
            return "Alert" if crit else ("Warn" if warn else "OK")
        got, prev = [], "OK"
        t = self.c.t("13:50")
        while t < self.c.t("15:30"):
            st = state(t)
            if st != prev:
                got.append((t.strftime("%H:%M:%S"), st))
                prev = st
            t += timedelta(minutes=1)
        want = [(self.c.t(x["at"]).strftime("%H:%M:%S"), x["to"]) for x in mon["transitions"]]
        if got != want:
            raise ValueError(f"monitor {mon['id']} transitions {want} disagree with its query over the served series: {got}")

    def render(self) -> None:
        ctx = self.ctx
        C = self.content
        self.metrics()
        mons_out = []
        for mon in self.monitors():
            mc = C["monitors"][mon["key"]]
            self.check_transitions(mon)
            creator = ctx.person(mc["creator"])
            trs = self.transitions(mon)
            # The monitor list is the configuration as synced at 13:45, like the Dynatrace entity list: its
            # overall_state is the state then, never a later one (tools derive state at now from events).
            state = "OK"
            for tr in trs:
                if tr["at"] < self.synced:
                    state = tr["to"]
            opts = {"notify_no_data": mon["type"] == "service check", "renotify_interval": 0, "include_tags": True,
                    "require_full_window": False, "new_group_delay": 60, "notify_audit": False}
            if "thresholds" in mon:
                opts["thresholds"] = mon["thresholds"]
            if mon.get("evaluation_delay_s"):
                opts["evaluation_delay"] = mon["evaluation_delay_s"]
            if mon["type"] == "service check":
                opts["thresholds"] = {"critical": 1, "warning": 1, "ok": 1}
                opts["no_data_timeframe"] = 2
            if mon["type"] == "synthetics alert":
                opts["synthetics_check_id"] = mon["synthetic"]
            obj = {"id": mon["id"], "name": mon["name"], "type": mon["type"], "query": mon.get("query", f"\"synthetics.test_runs\".over(\"check_id:{mon.get('synthetic')}\").last(2).count_by_status()"),
                   "message": mc["message"].replace("{runbook}", C["runbook"]), "tags": mc["tags"], "options": opts,
                   "overall_state": state, "priority": mc["priority"], "multi": "by {host}" in mon.get("query", ""),
                   "created": mc["created"].replace("Z", "+00:00"), "modified": mc["modified"].replace("Z", "+00:00"),
                   "creator": {"email": f"{creator['handle']}@larkspur.com", "handle": f"{creator['handle']}@larkspur.com", "name": creator["name"]},
                   "restricted_roles": None, "deleted": None}
            mons_out.append(obj)
            ctx.row(dd_monitor_handle(mon["id"]), "datadog/monitors.json", f"/{len(mons_out) - 1}", ctx.clock.t(mc["created"]),
                    "dd.monitor", mon.get("kind", "signal") if "kind" in mon else "signal", None)
            for tr in trs:
                self.event(mon, mc, tr)
        ctx.write_json("datadog/monitors.json", mons_out)
        self.write_events()
        self.synthetics()

    def event(self, mon: dict, mc: dict, tr: dict) -> None:
        t, to = tr["at"], tr["to"]
        host = tr.get("host") or {"pg_can_connect": "pay-db-01"}.get(mon["key"])
        name = mon["name"].replace("{{host.name}}", host or "")
        scope = f" on host:{host}" if host and "{{host.name}}" in mon["name"] else ""
        title = f"[{TITLE[to]}{scope}] {name}"
        msg = render_message(self.content["monitors"][mon["key"]]["message"].replace("{runbook}", self.content["runbook"]), to,
                             value=self.value(mon, t), threshold=mon.get("thresholds", {}).get("critical", ""),
                             **{"host.name": host or "", "device.name": "/data", "check_message": self.content["can_connect_check_message"],
                                "locations": "aws:eu-west-1, aws:us-east-1"})
        stamp = t.strftime("%a %b %d %Y %H:%M:%S UTC")
        verb = "recovered" if to == "OK" else "triggered"
        body = (f"%%% \n{msg}\n\n"
                + (f"`{mon.get('query', '')}`\n\n" if mon.get("query") else "")
                + f"The monitor was last {verb} at {stamp}.\n\n- - -\n\n"
                f"[[Monitor Status](https://app.datadoghq.com/monitors/{mon['id']}?from_ts={ms(t) - 1800000}&to_ts={ms(t)}&eval_ts={ms(t)})] · "
                f"[[Edit Monitor](https://app.datadoghq.com/monitors/{mon['id']}/edit)]\n%%%")
        tags = sorted(set(self.content["monitors"][mon["key"]]["tags"] + ["monitor", f"monitor_id:{mon['id']}"] + ([f"host:{host}"] if host else [])))
        ev = {"id": 0, "id_str": "", "title": title, "text": body, "date_happened": epoch_s(t), "alert_type": ALERT_TYPE[to],
              "priority": "normal", "source_type_name": "Monitor Alert", "tags": tags, "url": "", "monitor_id": mon["id"]}
        if host:
            ev["host"] = host
        self.events.append((t, ev, {"kind": tr.get("kind", "signal"), "sig_id": tr.get("sig_id"), "cls": "dd.event." + mon["key"]}))

    def write_events(self) -> None:
        ctx = self.ctx
        evs = sorted((e for e in self.events if self.export_from <= e[0] < self.c.t("15:30")), key=lambda e: e[0])
        lines = []
        import json
        for i, (t, ev, meta) in enumerate(evs):
            eid = 7820000000 + (epoch_s(t) - epoch_s(self.export_from)) * 7 + i % 7
            ev["id"], ev["id_str"] = eid, str(eid)
            ev["url"] = f"https://app.datadoghq.com/event/event?id={eid}"
            lines.append(json.dumps({"event": ev}, separators=(",", ":"), ensure_ascii=False))
            ctx.row(dd_event_handle(eid), "datadog/events.jsonl", i + 1, t, meta["cls"], meta["kind"], meta["sig_id"])
        ctx.write_text("datadog/events.jsonl", "\n".join(lines) + "\n")
        ctx.count("records:datadog_events", len(lines))
        ctx.datadog_events = evs        # (t, event, meta) for the Slack bot posts
        ctx.count("records:datadog_monitors", len(self.monitors()))
        self.posted = evs

    # -- metrics ----------------------------------------------------------------------------------

    def metrics(self) -> None:
        M = self.dd["metrics"]
        for q in M["queries"]:
            if not q.get("slice", True):
                continue
            w0, w1 = (self.c.t(x) for x in q.get("window", M["window"]))
            step = q.get("interval_s", M["interval_s"])
            times = []
            t = w0
            while t < w1:
                times.append(t)
                t += timedelta(seconds=step)
            groups = self.series_values(q, times)
            self.cache[q["file"]] = (times, groups)
            series = []
            for gi, (tagset, vals) in enumerate(groups):
                metric = re.search(r":([a-z_.]+)\{", q["query"]).group(1)
                scope_tags = re.search(r"\{([^}]*)\}", q["query"]).group(1).split(",")
                scope = ",".join(sorted(set(tagset + scope_tags)))
                pts = [[ms(tt), v] for tt, v in zip(times, vals) if v is not ...]
                series.append({"metric": metric, "display_name": metric, "scope": scope, "expression": q["query"].split(" by ")[0].replace(
                    "{" + ",".join(scope_tags) + "}", "{" + scope + "}"), "aggr": q["query"].split(":")[0], "interval": step,
                    "length": len(pts), "start": pts[0][0] if pts else ms(w0), "end": pts[-1][0] + step * 1000 - 1 if pts else ms(w1),
                    "tag_set": tagset, "query_index": 0, "pointlist": pts})
            gb = re.findall(r" by \{([^}]*)\}", q["query"])
            resp = {"status": "ok", "res_type": "time_series", "query": q["query"], "from_date": ms(w0), "to_date": ms(w1),
                    "group_by": gb[0].split(",") if gb else [], "message": "", "error": "", "series": series}
            rel = f"datadog/metrics/{q['file']}"
            self.ctx.write_json(rel, resp)
            handle = metric_handle(q["query"])   # the series identity; tools mint windows clamped to now
            for si, s in enumerate(series):
                for pi, (pt, _) in enumerate(s["pointlist"]):
                    tt = times[0] + timedelta(milliseconds=pt - ms(times[0]))
                    self.ctx.row(handle, rel, f"/series/{si}/pointlist/{pi}", tt, "metric." + q["file"].rsplit(".json", 1)[0],
                                 q.get("kind", "signal"), None, t_known=tt + timedelta(seconds=step))
            self.ctx.count("metric_series", len(series))

    def series_values(self, q: dict, times: list[datetime]) -> list[tuple[list[str], list]]:
        m = self.m
        r = rng(self.ctx.seed, "metric", q["file"])
        f = q["file"]
        jit = lambda v, p=0.03: v * (1 + r.uniform(-p, p))
        if f.startswith("checkout-api.hits"):
            out = []
            for ep in ["GET /api/cart", "GET /api/checkout/summary", "POST /api/checkout/address", "POST /api/checkout/submit"]:
                tag = "resource_name:" + ep.lower().replace(" ", "_")
                out.append(([tag], [round(jit(m.traffic[ep](t) * 60)) * 1.0 for t in times]))
            return out
        if f.startswith("checkout-api.errors"):
            v142, v143 = [], []
            for t in times:
                total = sum(m.traffic[e](t) for e in m.traffic if e.startswith(("GET", "POST"))) * 60
                errs = jit(total * m.err_rate(t) / 100, 0.05)
                pods = m.active_pods(t + timedelta(seconds=30))
                share = sum(1 for _, v in pods if v == "2.14.3") / len(pods)
                v143.append(round(errs * share) * 1.0 if share > 0 else ...)
                v142.append(round(errs * (1 - share)) * 1.0 if share < 1 else ...)
            return [(["version:2.14.2"], v142), (["version:2.14.3"], v143)]
        if f.startswith("payments-gw.p90"):
            # p90 never exceeds the same minute's p99 (drawn from the same requests).
            p99 = self.series_values({"file": "payments-gw.p99.json"}, times)[0][1]
            return [([], [round(min(jit(m.p90(t)), hi * 0.985), 3) for t, hi in zip(times, p99)])]
        if f.startswith("payments-gw.p99"):
            return [([], [round(min(10.02, jit(m.p99(t))), 3) for t in times])]
        if f.startswith("postgresql.connections"):
            n0, n1 = (self.c.t(x) for x in q["nulls"])
            pay, orders, pg = [], [], []
            for t in times:
                row = m.conn_row(t)
                if n0 <= t <= n1:
                    pay.append(None), orders.append(None), pg.append(None)
                else:
                    pay.append(float(sum(row["gateway"]) + row["recon"]))
                    orders.append(float(row["order"]))
                    pg.append(float(row["other"]))
            return [(["db:orders"], orders), (["db:payments"], pay), (["db:postgres"], pg)]
        if f.startswith("payments.authorize.success_rate"):
            # From the request stream: authorizations that completed at the gateway, less the baseline share
            # of ordinary card declines. Retries that gave up, pool timeouts and fast failures all count.
            base = self.ctx.facts["parameters"]["payment_success_rate"]["baseline"]
            ok, tot = {}, {}
            for q_ in m.requests():
                k = q_.t.replace(second=0, microsecond=0)
                tot[k] = tot.get(k, 0) + 1
                ok[k] = ok.get(k, 0) + (q_.outcome in ("ok", "slow_ok"))
            return [([], [round(base * ok[t] / tot[t], 2) if tot.get(t) else None for t in times])]
        if f.startswith("storefront-web.hits"):
            return [([], [round(jit(m.traffic["storefront_browse"](t) * 60)) * 1.0 for t in times])]
        if f.startswith("redis-sessions.fragmentation"):
            # Hourly averages of the flap the monitor alerts on: at the peak while the monitor is in Alert,
            # at the baseline otherwise (one source for the series and the events).
            R = self.ctx.scenario["model"]["redis_fragmentation_ratio"]
            mon = next(x for x in self.dd["monitors"] if x["key"] == "redis_frag")
            alerts = []
            trs = self.transitions(dict(mon), since=times[0])
            start = None
            for tr in trs:
                if tr["to"] == "Alert":
                    start = tr["at"]
                elif start is not None:
                    alerts.append((start, tr["at"]))
                    start = None
            vals = []
            for t in times:
                t1 = t + timedelta(seconds=q.get("interval_s", 3600))
                over = sum(max(0.0, (min(t1, b) - max(t, a)).total_seconds()) for a, b in alerts) / (t1 - t).total_seconds()
                vals.append(round(jit(R["baseline"] + (R["flap_peak"] - R["baseline"]) * over, 0.01), 3))
            return [([], vals)]
        raise KeyError(f)

    def synthetics(self) -> None:
        out = []
        for s in self.dd["synthetics"]:
            mon = next((m for m in self.dd["monitors"] if m["id"] == s["monitor"]), None)
            if not mon or not mon.get("slice", True):
                continue
            sc = self.content["synthetics"][s["public_id"]]
            creator = self.ctx.person(sc["creator"])
            out.append({"public_id": s["public_id"], "name": s["name"], "type": sc["type"], "status": "live",
                        "message": self.content["monitors"][mon["key"]]["message"].replace("{runbook}", self.content["runbook"]),
                        "locations": sc["locations"], "monitor_id": s["monitor"], "tags": self.content["monitors"][mon["key"]]["tags"],
                        "config": {"assertions": [], "request": {"method": "GET", "url": s["url"]}},
                        "options": {"tick_every": sc["tick_every"], "min_location_failed": 1, "monitor_name": mon["name"]},
                        "steps": [{"name": x, "type": "assertCurrentUrl" if x.startswith("Navigate") else ("assertElementContent" if x.startswith("Assert") else "click")}
                                  for x in sc["steps"]],
                        "creator": {"email": f"{creator['handle']}@larkspur.com", "handle": f"{creator['handle']}@larkspur.com", "name": creator["name"]}})
            self.ctx.row(dd_synthetic_handle(s["public_id"]), "datadog/synthetics.json", f"/{len(out) - 1}", self.c.t("2024-05-02T14:31:00Z"),
                         "dd.synthetic", "signal")
        self.ctx.write_json("datadog/synthetics.json", out)
        self.ctx.count("records:datadog_synthetics", len(out))
