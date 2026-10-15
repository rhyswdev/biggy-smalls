"""Smaller sources: Confluence pages, the PayCore status page, the triggers and the served
integration.yaml (each source's clock, as an integration admin would configure it)."""

from __future__ import annotations

from datetime import timedelta

import yaml

from smalls.contracts.handles import conf_handle_for_path, psp_handle
from smalls_data.gen.core import Ctx, content, content_path, local


def iso_local(t, zone: str) -> str:
    lt = local(t, zone)
    s = lt.isoformat(timespec="milliseconds")
    return s


class Confluence:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx

    def render(self) -> None:
        for p in self.ctx.scenario["confluence"]:
            if not p.get("slice", False):
                continue
            src = content_path(f"confluence/{p['key']}.md")
            rel = f"confluence/{p['file']}"
            self.ctx.write_text(rel, src.read_text())
            self.ctx.row(conf_handle_for_path(rel), rel, 1, self.ctx.clock.t(p["updated"] + "T12:00:00Z"), "conf.page", p["kind"], p.get("sig_id"))
            self.ctx.count("confluence_pages")


class PayCore:
    def __init__(self, ctx: Ctx):
        self.ctx, self.c = ctx, ctx.clock
        self.zone = ctx.facts["zones"]["paycore"]["zone"]

    def render(self) -> None:
        """status.v1 (green, as published at 10:33 UTC), v2 (15:10:22, investigating, started_at backdated to
        14:24) and v3 (15:30, monitoring). Each is the page as it stood when published."""
        T = content("paycore.yaml")
        comps = [("Authorizations (EU)", "eu-auth"), ("Authorizations (US)", "us-auth"), ("Refunds", "refunds"),
                 ("Settlement files", "settle"), ("Dashboard", "dash"), ("API", "api")]
        page_id = "pc7v2kq9x1ld"
        created = self.c.t("2021-04-12T15:03:11Z")
        inc_id = "q8k2d7v1m4xz"
        updates = []
        for v in self.ctx.scenario["paycore"]:
            cap = self.c.t(v["captured"])
            # v1 is the page as last changed at 10:33 UTC; later versions are published when captured.
            updated = cap - timedelta(hours=3, minutes=12) if v["version"] == 1 else cap
            degraded = v.get("incident_status") == "investigating"
            components = []
            for i, (n, k) in enumerate(comps):
                eu = k == "eu-auth"
                components.append({"id": f"{k}{i}x7q2m9", "name": n, "status": "degraded_performance" if eu and degraded else "operational",
                                   "created_at": iso_local(created, self.zone),
                                   "updated_at": iso_local(updated if eu and v["version"] > 1 else self.c.t("2026-07-19T16:52:40Z"), self.zone),
                                   "position": i + 1, "description": None, "showcase": False, "start_date": None, "group_id": None,
                                   "page_id": page_id, "group": False, "only_show_if_degraded": False})
            incidents = []
            if v.get("incident_status"):
                status = v["incident_status"]
                eu = components[0]
                updates.insert(0, {"id": f"u{len(updates)}{inc_id[:10]}", "status": status, "body": T["updates"][status], "incident_id": inc_id,
                                   "created_at": iso_local(cap, self.zone), "updated_at": iso_local(cap, self.zone),
                                   "display_at": iso_local(cap, self.zone),
                                   "affected_components": [{"code": eu["id"], "name": eu["name"],
                                                            "old_status": "operational" if status == "investigating" else "degraded_performance",
                                                            "new_status": eu["status"]}],
                                   "deliver_notifications": True, "custom_tweet": None, "tweet_id": None})
                first = self.c.t(next(x for x in self.ctx.scenario["paycore"] if x.get("created"))["created"])
                incidents.append({"id": inc_id, "name": T["incident_name"], "status": status, "created_at": iso_local(first, self.zone),
                                  "updated_at": iso_local(cap, self.zone),
                                  "monitoring_at": iso_local(cap, self.zone) if status == "monitoring" else None, "resolved_at": None,
                                  "impact": "minor", "shortlink": "https://stspg.io/pc4x9k2", "page_id": page_id,
                                  "started_at": iso_local(self.c.t(next(x for x in self.ctx.scenario["paycore"] if x.get("started"))["started"]), self.zone),
                                  "incident_updates": [dict(u) for u in updates], "components": [eu]})
            doc = {"page": {"id": page_id, "name": "PayCore", "url": "https://status.paycore.example", "time_zone": self.zone,
                            "updated_at": iso_local(updated, self.zone)},
                   "components": components, "incidents": incidents, "scheduled_maintenances": [],
                   "status": {"indicator": v["indicator"],
                              "description": "All Systems Operational" if v["indicator"] == "none" else "Minor Service Outage"}}
            rel = f"paycore/status.v{v['version']}.json"
            self.ctx.write_json(rel, doc)
            # Known from when the page published this state (page.updated_at), which re-derives from the bytes.
            self.ctx.row(psp_handle("paycore", v["version"]), rel, "/", updated, "psp.status", v["kind"], v.get("sig_id"),
                         valid_from=self.c.t(v["valid_from"]) if v.get("valid_from") else None,
                         valid_to=self.c.t(v["valid_to"]) if v.get("valid_to") else None)
            self.ctx.count("status_snapshots")


class Triggers:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx

    def render(self) -> None:
        for t in self.ctx.scenario["triggers"]:
            src = content_path(f"triggers/{t['checkpoint']}.md")
            self.ctx.write_text(t["file"], src.read_text())


class Integration:
    """bundles/<b>/integration.yaml: what the admin of each integration configured. Clock rules for
    every source folder, the record-break rule for multi-line logs, and the syslog year."""

    BREAK = {"splunk/raw/payments_app": r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} ",
             "splunk/raw/postgres": r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} UTC ",
             "splunk/raw/checkout_api": r"^\{", "splunk/raw/k8s_events": r"^\{",
             "splunk/raw/f5": r"^[A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2} ", "splunk/raw/nginx": r"^\S+ - \S+ \["}

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.splunk = content("splunk.yaml")["sourcetypes"]

    def render(self) -> None:
        sources = {}
        present = {p.relative_to(self.ctx.out).as_posix() for p in self.ctx.out.rglob("*") if p.is_dir()}
        for path, z in self.ctx.facts["zones"].items():
            if path not in present:
                continue
            d = {"zone": z["zone"]}
            d.update({k: z[k] for k in ("format", "format_note") if k in z})
            d["marker"] = z["marker"]
            if path in self.BREAK:
                d["break_only_before"] = self.BREAK[path]
            if path in self.splunk:      # how the folder is indexed: index, sourcetype, source, host rule
                d.update(self.splunk[path])
            sources[path] = d
        # Files, not folders, the loader also reads.
        if (self.ctx.out / "splunk/saved_searches.json").exists():
            sources["splunk/saved_searches.json"] = {"zone": "UTC", "format": "%Y-%m-%dT%H:%M:%S%z", "marker": "offset"}
        sampling = {}
        for folder, spec in self.ctx.scenario["sampling"].items():
            if folder not in present:
                continue
            d = {k: v for k, v in spec.items() if k != "classes"}
            d["classes"] = [{k: v for k, v in c.items() if k != "gen"} for c in spec["classes"]]
            sampling[folder] = d
        # A variant may serve the estate's bundle name rather than its own folder's, so nothing served names it.
        doc = {"bundle": self.ctx.scenario["meta"].get("served_name", self.ctx.scenario["meta"]["bundle"]), "syslog_year": int(self.ctx.scenario["meta"]["date"][:4]),
               "servicenow_display_value": "true", "sources": sources, "sampling": sampling}
        self.ctx.write_text("integration.yaml", "# Integration configuration as exported with this bundle: each source's clock and\n"
                            "# record-break rule, and the log pipeline's sampling: one constant rate per line class,\n"
                            "# matched on a record's first line (first matching class wins). Configuration, not evidence.\n"
                            + yaml.safe_dump(doc, sort_keys=False, width=200, allow_unicode=True))
