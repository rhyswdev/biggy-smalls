"""PagerDuty REST v2: services, escalation policies and on-call (configuration, as listed at 15:30),
incidents as version snapshots (pagerduty/incidents/<number>.v<n>.json, a GET /incidents/{id} body as it
stood after each status or assignment change, the way webhook v3 events deliver them) and the
append-only log entries that drive them (pagerduty/log_entries.json)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from smalls.contracts.handles import pd_incident_handle, pd_log_handle, pd_oncall_handle, pd_policy_handle, pd_service_handle
from smalls_data.gen.core import Ctx, iso_z

BASE = "https://larkspur.pagerduty.com"
API = "https://api.pagerduty.com"
LOG_TYPES = {"trigger": "trigger_log_entry", "assign": "assign_log_entry", "notify": "notify_log_entry",
             "acknowledge": "acknowledge_log_entry", "escalate": "escalate_log_entry", "resolve": "resolve_log_entry",
             "merge_in": "annotate_log_entry"}


def pd_id(*key) -> str:
    """PagerDuty-style opaque id: uppercase letters and digits."""
    h = hashlib.sha1("|".join(map(str, key)).encode()).digest()
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(alphabet[b % 36] for b in h)[:14]


class PagerDuty:
    def __init__(self, ctx: Ctx):
        self.ctx, self.c = ctx, ctx.clock
        self.P = ctx.scenario["pagerduty"]
        self.people = {p["handle"]: p for p in ctx.facts["people"]}
        self.svc = {s["name"]: s for s in self.P["services"]}
        self.pol = {p["id"]: p for p in self.P["policies"]}

    def ref(self, kind: str, id_: str, summary: str) -> dict:
        path = {"service": "services", "escalation_policy": "escalation_policies", "user": "users", "schedule": "schedules",
                "incident": "incidents"}[kind]
        return {"id": id_, "type": f"{kind}_reference", "summary": summary, "self": f"{API}/{path}/{id_}",
                "html_url": f"{BASE}/{path.replace('_', '-')}/{id_}"}

    def user(self, handle: str) -> dict:
        p = self.people[handle]
        return self.ref("user", p["pagerduty_id"], p["name"])

    def svc_ref(self, name: str) -> dict:
        return self.ref("service", self.svc[name]["id"], name)

    def pol_ref(self, pid: str) -> dict:
        return self.ref("escalation_policy", pid, self.pol[pid]["name"])

    # -- configuration ---------------------------------------------------------------------------------

    def services(self) -> list[dict]:
        """Services as configured (synced at 13:45, like the Datadog monitor list): `status` is the state then,
        and `last_incident_timestamp` is left out because it would change with the day's incidents."""
        created = self.c.t("2023-06-01T10:00:00Z")
        out = []
        for s in self.P["services"]:
            out.append({"id": s["id"], "type": "service", "summary": s["name"], "self": f"{API}/services/{s['id']}",
                        "html_url": f"{BASE}/service-directory/{s['id']}", "name": s["name"],
                        "description": f"{s['name']} alerts from Datadog", "auto_resolve_timeout": 14400,
                        "created_at": iso_z(created, 0), "status": "active",
                        "escalation_policy": self.pol_ref(s["policy"]), "teams": [], "integrations": [],
                        "incident_urgency_rule": {"type": "constant", "urgency": s.get("urgency", "high")},
                        "alert_creation": "create_alerts_and_incidents"})
            self.ctx.row(pd_service_handle(s["id"]), "pagerduty/services.json", f"/services/{len(out) - 1}", created, "pd.service", "noise")
        return out

    def policies(self) -> list[dict]:
        out = []
        for p in self.P["policies"]:
            rules = [{"id": pd_id("rule", p["id"], lv["level"]), "escalation_delay_in_minutes": p["delay_min"],
                      "targets": [self.ref("schedule", lv["schedule"], f"{p['name']} level {lv['level']}") | {"type": "schedule_reference"}]}
                     for lv in p["levels"]]
            svcs = [self.svc_ref(s["name"]) for s in self.P["services"] if s["policy"] == p["id"]]
            out.append({"id": p["id"], "type": "escalation_policy", "summary": p["name"], "self": f"{API}/escalation_policies/{p['id']}",
                        "html_url": f"{BASE}/escalation_policies/{p['id']}", "name": p["name"], "escalation_rules": rules,
                        "services": svcs, "num_loops": 0, "teams": [], "on_call_handoff_notifications": "if_has_services"})
            self.ctx.row(pd_policy_handle(p["id"]), "pagerduty/escalation_policies.json", f"/escalation_policies/{len(out) - 1}",
                         self.c.t("2023-06-01T10:00:00Z"), "pd.policy", "signal" if p["name"] == "Payments Engineering" else "noise",
                         "sig.pd_policy_payments" if p["name"] == "Payments Engineering" else None)
        return out

    def oncalls(self) -> list[dict]:
        """The day's shifts: 08:00 UTC to 08:00 UTC the next day, a scheduled end known in advance."""
        start, end = self.c.t("08:00:00"), self.c.t("08:00:00") + timedelta(days=1)
        out = []
        for p in self.P["policies"]:
            for lv in p["levels"]:
                out.append({"escalation_policy": self.pol_ref(p["id"]), "escalation_level": lv["level"],
                            "schedule": self.ref("schedule", lv["schedule"], f"{p['name']} level {lv['level']}"),
                            "user": self.user(lv["user"]), "start": iso_z(start, 0), "end": iso_z(end, 0)})
                sig = {"priya.raman": "sig.pd_oncall_priya"}.get(lv["user"])
                self.ctx.row(pd_oncall_handle(p["id"], lv["level"], self.people[lv["user"]]["pagerduty_id"]), "pagerduty/oncalls.json",
                             f"/oncalls/{len(out) - 1}", start, "pd.oncall", "signal" if sig else "noise", sig)
        return out

    # -- incidents and log entries -------------------------------------------------------------------------

    def summary(self, inc: dict, e: dict) -> str:
        who = self.people[e["user"]]["name"] if e.get("user") else None
        pol = self.pol[self.svc[inc["service"]]["policy"]]
        if e["type"] == "trigger":
            return "Triggered through the API."
        if e["type"] == "assign":
            return f"Assigned to {who}."
        if e["type"] == "notify":
            return f"Notified {who} by phone."
        if e["type"] == "acknowledge":
            return f"Acknowledged by {who}."
        if e["type"] == "escalate":
            return f"Escalated to {who} at level 2 of {pol['name']} after {pol['delay_min']} minutes without acknowledgement."
        if e["type"] == "merge_in":
            return f"Incident #{e['from']} was merged into this incident."
        if e.get("via") == "datadog recovery":
            return "Resolved through the API."
        if e.get("merged_into"):
            return f"Resolved by merging into incident #{e['merged_into']}."
        return f"Resolved by {who}."

    def render(self) -> None:
        ctx = self.ctx
        ctx.write_json("pagerduty/services.json", {"services": self.services(), "limit": 25, "offset": 0, "more": False, "total": None})
        ctx.write_json("pagerduty/escalation_policies.json", {"escalation_policies": self.policies(), "limit": 25, "offset": 0, "more": False, "total": None})
        ctx.write_json("pagerduty/oncalls.json", {"oncalls": self.oncalls(), "limit": 25, "offset": 0, "more": False, "total": None})
        entries = []
        for inc in self.P["incidents"]:
            svc = self.svc_ref(inc["service"])
            pol = self.pol_ref(self.svc[inc["service"]]["policy"])
            iref = self.ref("incident", inc["id"], f"[#{inc['number']}] {inc['title']}")
            created = self.c.t(inc["created"])
            trigger_id = None
            state = {"status": "triggered", "assignments": [], "acknowledgements": [], "alerts": 1, "resolved": None,
                     "changed_at": created, "changed_by": svc}
            versions: list[tuple] = []
            for k, e in enumerate(inc["log"]):
                t = self.c.t(e["at"])
                lid = pd_id("log", inc["number"], k)
                agent = self.user(e["user"]) if e.get("user") and e["type"] in ("acknowledge", "resolve", "merge_in") else svc
                channel = {"type": e.get("channel") or {"trigger": "api", "notify": "auto", "assign": "auto", "escalate": "timeout"}.get(e["type"], "website")}
                if e.get("via") == "datadog recovery":
                    channel, agent = {"type": "api"}, svc
                summary = self.summary(inc, e)
                entry = {"id": lid, "type": LOG_TYPES[e["type"]], "summary": summary, "self": f"{API}/log_entries/{lid}",
                         "html_url": f"{BASE}/incidents/{inc['id']}/log_entries/{lid}", "created_at": iso_z(t, 0), "agent": agent,
                         "channel": channel, "service": svc, "incident": iref, "teams": [], "contexts": []}
                if e["type"] in ("assign", "escalate"):
                    entry["assignees"] = [self.user(e["user"])]
                elif e["type"] == "notify":
                    entry["user"] = self.user(e["user"])
                if e["type"] == "trigger":
                    trigger_id = lid
                    entry["event_details"] = {"description": inc["title"]}
                entries.append((t, entry, inc, e))
                # The incident after this entry.
                changed = True
                if e["type"] in ("assign", "escalate"):
                    state["assignments"] = [{"at": iso_z(t, 0), "assignee": self.user(e["user"])}]
                elif e["type"] == "acknowledge":
                    state.update(status="acknowledged", changed_at=t, changed_by=self.user(e["user"]))
                    state["acknowledgements"] = [{"at": iso_z(t, 0), "acknowledger": self.user(e["user"])}]
                elif e["type"] == "merge_in":
                    state["alerts"] += 1
                elif e["type"] == "resolve":
                    state.update(status="resolved", changed_at=t, changed_by=agent, resolved=t, assignments=[], acknowledgements=[])
                elif e["type"] == "trigger":
                    pass
                else:
                    changed = False
                if changed:
                    body = {"id": inc["id"], "type": "incident", "summary": f"[#{inc['number']}] {inc['title']}",
                            "self": f"{API}/incidents/{inc['id']}", "html_url": f"{BASE}/incidents/{inc['id']}",
                            "incident_number": inc["number"], "title": inc["title"], "created_at": iso_z(created, 0), "updated_at": iso_z(t, 0),
                            "status": state["status"], "incident_key": inc["dedup_key"],
                            "service": svc, "assignments": list(state["assignments"]), "assigned_via": "escalation_policy",
                            "acknowledgements": list(state["acknowledgements"]), "last_status_change_at": iso_z(state["changed_at"], 0),
                            "last_status_change_by": state["changed_by"],
                            "first_trigger_log_entry": {"id": trigger_id, "type": "trigger_log_entry_reference", "summary": "Triggered through the API.",
                                                        "self": f"{API}/log_entries/{trigger_id}", "html_url": f"{BASE}/incidents/{inc['id']}/log_entries/{trigger_id}"},
                            "alert_counts": {"all": state["alerts"], "triggered": 0 if state["status"] == "resolved" else state["alerts"],
                                             "resolved": state["alerts"] if state["status"] == "resolved" else 0},
                            "is_mergeable": state["status"] != "resolved", "escalation_policy": pol, "teams": [], "pending_actions": [],
                            "urgency": inc["urgency"], "priority": {"id": pd_id("prio", inc["urgency"]), "type": "priority",
                                                                     "summary": "P2" if inc["urgency"] == "high" else "P4", "name": "P2" if inc["urgency"] == "high" else "P4"},
}
                    if state["resolved"]:
                        body["resolved_at"] = iso_z(state["resolved"], 0)
                    if e["type"] == "resolve" and e.get("merged_into"):
                        target = next(x for x in self.P["incidents"] if x["number"] == e["merged_into"])
                        body["resolve_reason"] = {"type": "merge_resolve_reason",
                                                  "incident": self.ref("incident", target["id"], f"[#{target['number']}] {target['title']}")}
                    if versions and versions[-1][0] == t:
                        versions[-1] = (t, body)
                    else:
                        versions.append((t, body))
            for v, (t, body) in enumerate(versions):
                end = versions[v + 1][0] if v + 1 < len(versions) else None
                rel = f"pagerduty/incidents/{inc['number']}.v{v + 1}.json"
                ctx.write_json(rel, {"incident": body})
                ctx.row(pd_incident_handle(inc["number"]), rel, "/", created, "pd.incident", inc["kind"], inc.get("sig_id"),
                        t_known=t, valid_from=t, valid_to=end)
            ctx.count("records:pagerduty_objects")
        entries.sort(key=lambda x: x[0])
        out = []
        for i, (t, entry, inc, e) in enumerate(entries):
            out.append(entry)
            ctx.row(pd_log_handle(entry["id"]), "pagerduty/log_entries.json", f"/log_entries/{i}", t, "pd.log", e.get("kind", inc["kind"] if e["type"] == "trigger" else "noise"),
                    e.get("sig_id"))
        ctx.write_json("pagerduty/log_entries.json", {"log_entries": out, "limit": 100, "offset": 0, "more": False, "total": None})
        ctx.count("records:pagerduty_objects", len(out))
