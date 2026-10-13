"""ServiceNow Table API exports with sysparm_display_value=true: every field of the table present, every
value a string, dates in the integration user's zone (America/New_York) with no marker, references as
display values.

    servicenow/incident.json             history, noise and customer incidents, each as it stands in the export
    servicenow/major_incident/INC0048811.v<n>.json
                                          the live incident as single-record GET responses, one per update
    servicenow/sys_journal_field.json    work notes and comments
    servicenow/sys_audit.json            field history of the live incident (stored values: codes, sys_ids)
    servicenow/change_request.json       changes of the day and the week
    servicenow/cmdb_ci.json, cmdb_rel_ci.json, sys_user.json, sys_user_group.json   the service model
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from smalls.contracts.handles import audit_handle, ci_handle, group_handle, journal_handle, rel_handle, snow_handle, user_handle
from smalls_data.gen.core import ROOT, Ctx, content, local, sys_id
from smalls_data.gen.model import Model
from smalls_data.gen.pgsa import pgsa_text

PRIORITY = {1: "1 - Critical", 2: "2 - High", 3: "3 - Moderate", 4: "4 - Low", 5: "5 - Planning"}
STATE_CODE = {"New": "1", "In Progress": "2", "On Hold": "3", "Resolved": "6", "Closed": "7"}
TABLES = ("incident", "change_request", "sys_journal_field", "sys_audit", "cmdb_ci", "cmdb_rel_ci", "sys_user", "sys_user_group")


def duration(a: datetime, b: datetime) -> str:
    s = int((b - a).total_seconds())
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    parts = ([f"{d} Day" + ("s" if d != 1 else "")] if d else []) + ([f"{h} Hour" + ("s" if h != 1 else "")] if h else []) \
        + ([f"{m} Minute" + ("s" if m != 1 else "")] if m else [])
    return " ".join(parts)


class ServiceNow:
    def __init__(self, ctx: Ctx, model: Model):
        self.ctx, self.c, self.m = ctx, ctx.clock, model
        self.zone = ctx.facts["zones"]["servicenow"]["zone"]
        self.fields = {t: json.loads((ROOT / "schemas/servicenow/extracted" / f"{t}.fields.json").read_text())["fields"] for t in TABLES}
        self.journal: list[tuple] = []
        self.groups = {g["name"]: g for g in ctx.facts["groups"]}
        self.used_orders: set[str] = set()
        self.used_carts: set[int] = set()

    # -- helpers -------------------------------------------------------------------------------------

    def dv(self, t: datetime | None) -> str:
        return "" if t is None else local(t, self.zone).strftime("%Y-%m-%d %H:%M:%S")

    def name(self, handle: str | None) -> str:
        return "" if not handle else self.ctx.person(handle)["name"]

    def blank(self, table: str) -> dict:
        return {f: "" for f in self.fields[table]}

    def eastern(self, s: str) -> datetime:
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo(self.zone)).astimezone(ZoneInfo("UTC"))

    def note(self, t: datetime, number: str, inc_sys_id: str, element: str, by: str, text: str, kind: str, sig_id=None,
             cls: str = "snow.journal", key: str = "") -> None:
        jid = sys_id("journal", number, t.isoformat(), key)
        self.journal.append((t, {"sys_id": jid, "element": element, "element_id": inc_sys_id, "name": "incident", "value": text,
                                 "sys_created_by": by, "sys_created_on": self.dv(t)}, kind, sig_id, cls))

    def number_at(self, t: datetime) -> str:
        """Incident numbers rise with time; between the fixed numbers of the scenario they are interpolated."""
        anchors = sorted((self.c.t(h["opened"]), int(h["number"][3:])) for h in self.ctx.scenario["history"])
        anchors.append((self.c.t(self.ctx.scenario["servicenow"]["live"]["opened"]), int(self.ctx.scenario["servicenow"]["live"]["number"][3:])))
        anchors.sort()
        for (t0, n0), (t1, n1) in zip(anchors, anchors[1:]):
            if t0 < t < t1:
                return f"INC{n0 + 1 + int((n1 - n0 - 2) * (t - t0) / (t1 - t0)):07d}"
        raise ValueError(f"no number range for {t}")

    # -- people and the service model --------------------------------------------------------------

    def users(self) -> list[dict]:
        out = []
        created = self.c.t("2023-02-01T15:00:00Z")
        for p in self.ctx.facts["people"]:
            rec = self.blank("sys_user")
            first, last = p["name"].split(" ", 1)
            mgr = self.groups[p["team"]]["manager"]
            rec.update({"user_name": p["handle"], "name": p["name"], "first_name": first, "last_name": last,
                        "email": p.get("email", f"{p['handle']}@larkspur.com"), "title": p["title"], "department": p["team"],
                        "manager": self.name(mgr) if mgr and mgr != p["handle"] else "", "active": "true" if p["sn_active"] else "false",
                        "locked_out": "false" if p["sn_active"] else "true", "time_zone": p["tz"], "company": "Larkspur Retail",
                        "location": {"Europe/London": "London", "Europe/Rome": "Milan", "America/New_York": "New York"}[p["tz"]],
                        "notification": "Enable" if p["sn_active"] else "Disable", "vip": "false", "source": "ldap:corp.larkspur.com",
                        "employee_number": str(10000 + int(p["sn_sys_id"][:4], 16) % 9000), "sys_id": p["sn_sys_id"],
                        "last_login_time": self.dv(self.c.t(p["left_on"] + "T21:00:00Z")) if p.get("left_on") else
                        self.dv(self.c.t("2026-10-12T07:00:00Z") + timedelta(minutes=int(p["sn_sys_id"][:3], 16) % 600)),
                        "sys_created_on": self.dv(created), "sys_created_by": "ldap.import",
                        "sys_updated_on": self.dv(self.c.t(p["left_on"] + "T21:05:00Z")) if p.get("left_on") else self.dv(created),
                        "sys_updated_by": "ldap.import", "sys_mod_count": "4" if p.get("left_on") else "1", "sys_domain": "global", "sys_domain_path": "/"})
            out.append(rec)
        for u in self.ctx.facts["integration_users"]:
            rec = self.blank("sys_user")
            rec.update({"user_name": u["user_name"], "name": u["name"], "first_name": u["name"], "active": "true", "locked_out": "false",
                        "time_zone": self.zone, "company": "Larkspur Retail", "source": "", "notification": "Disable", "vip": "false",
                        "title": "Integration" if u["user_name"].startswith("svc-") else "Shared account", "sys_id": u["sn_sys_id"],
                        "sys_created_on": self.dv(created), "sys_created_by": "admin", "sys_updated_on": self.dv(created), "sys_updated_by": "admin",
                        "sys_mod_count": "0", "sys_domain": "global", "sys_domain_path": "/"})
            out.append(rec)
        for i, rec in enumerate(out):
            self.ctx.row(user_handle(rec["user_name"]), "servicenow/sys_user.json", f"/result/{i}", created, "snow.user", "noise")
        return out

    def user_groups(self) -> list[dict]:
        out = []
        created = self.c.t("2023-02-01T15:10:00Z")
        for g in self.ctx.facts["groups"]:
            rec = self.blank("sys_user_group")
            rec.update({"name": g["name"], "manager": self.name(g["manager"]), "active": "true", "type": "itil",
                        "email": g["name"].lower().replace(" ", "-") + "@larkspur.com", "description": f"{g['name']} team",
                        "include_members": "false", "exclude_manager": "false", "sys_id": g["sn_sys_id"], "sys_created_on": self.dv(created),
                        "sys_created_by": "admin", "sys_updated_on": self.dv(created), "sys_updated_by": "admin", "sys_mod_count": "0",
                        "sys_domain": "global", "sys_domain_path": "/"})
            out.append(rec)
        for i, rec in enumerate(out):
            self.ctx.row(group_handle(rec["sys_id"]), "servicenow/sys_user_group.json", f"/result/{i}", created, "snow.group", "noise")
        return out

    def cmdb(self) -> tuple[list[dict], list[dict]]:
        C = content("servicenow/cmdb.yaml")
        net = self.ctx.facts["network"]
        cis, rels = [], []
        for ci in C["cis"]:
            rec = self.blank("cmdb_ci")
            retired = ci.get("status") == "retired"
            created, updated = self.c.t(ci["created"]), self.c.t(ci["updated"])
            rec.update({"name": ci["name"], "sys_class_name": C["classes"][ci["class"]], "owned_by": self.name(ci["owned_by"]),
                        "managed_by": self.name(ci["owned_by"]), "support_group": ci["group"], "assignment_group": ci["group"],
                        "short_description": ci.get("description", ""), "busines_criticality": ci.get("criticality", "2 - somewhat critical"),
                        "install_status": "Retired" if retired else "Installed", "operational_status": "Retired" if retired else "Operational",
                        "environment": "Production", "used_for": "Production", "company": "Larkspur Retail", "version": ci.get("version", ""),
                        "u_external_ids": ci.get("external", ""), "sys_id": sys_id("cmdb_ci", ci["name"]), "sys_created_on": self.dv(created),
                        "sys_created_by": "admin", "sys_updated_on": self.dv(updated), "sys_updated_by": "discovery" if ci["class"] == "linux" else "admin",
                        "sys_mod_count": "12", "sys_domain": "global", "sys_domain_path": "/", "category": "Hardware" if ci["class"] == "linux" else "Software"})
            if ci.get("ip"):
                rec.update({"ip_address": net[ci["ip"]], "fqdn": f"{ci['name']}.larkspur.internal", "dns_domain": "larkspur.internal",
                            "discovery_source": "ServiceNow", "first_discovered": self.dv(created), "last_discovered": self.dv(updated),
                            "manufacturer": "VMware, Inc.", "subcategory": "Computer"})
            cis.append(rec)
        by_name = {r["name"]: r for r in cis}
        for parent, typ, child in C["rels"]:
            rid = sys_id("cmdb_rel_ci", parent, typ, child)
            rec = self.blank("cmdb_rel_ci")
            created = max(self.c.t(next(c for c in C["cis"] if c["name"] == n)["created"]) for n in (parent, child))
            rec.update({"parent": parent, "type": typ, "child": child, "sys_id": rid, "connection_strength": "Always", "percent_outage": "",
                        "port": "", "sys_created_on": self.dv(created), "sys_created_by": "admin", "sys_updated_on": self.dv(created),
                        "sys_updated_by": "admin", "sys_mod_count": "0", "sys_domain": "global", "sys_domain_path": "/"})
            assert parent in by_name and child in by_name, (parent, child)
            rels.append(rec)
        for i, (ci, rec) in enumerate(zip(C["cis"], cis)):
            kind = "trap" if ci["name"] in ("Payments Gateway", "redis-sessions-01") else "noise"
            sig = {"Payments Gateway": "sig.cmdb_owner_marcus", "redis-sessions-01": "sig.cmdb_redis_retired"}.get(ci["name"])
            self.ctx.row(ci_handle(ci["name"]), "servicenow/cmdb_ci.json", f"/result/{i}", self.c.t(ci["created"]), "snow.ci", kind, sig)
        for i, rec in enumerate(rels):
            kind = "trap" if rec["child"] == "redis-sessions-01" else "noise"
            sig = "sig.cmdb_redis_edge" if kind == "trap" else None
            t = datetime.strptime(rec["sys_created_on"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo(self.zone))
            self.ctx.row(rel_handle(rec["sys_id"]), "servicenow/cmdb_rel_ci.json", f"/result/{i}", t, "snow.rel", kind, sig)
        return cis, rels

    # -- incidents -------------------------------------------------------------------------------------

    def incident(self, number: str, opened: datetime, body: dict, **kw) -> dict:
        rec = self.blank("incident")
        rec.update({"number": number, "sys_id": sys_id("incident", number), "sys_class_name": "Incident", "sys_domain": "global",
                    "sys_domain_path": "/", "company": "Larkspur Retail", "knowledge": "false", "made_sla": "true", "notify": "Do Not Notify",
                    "escalation": "Normal", "approval": "Not Yet Requested", "upon_approval": "Proceed to Next Task",
                    "upon_reject": "Cancel all future Tasks", "sla_due": "UNKNOWN", "reopen_count": "0", "opened_at": self.dv(opened),
                    "sys_created_on": self.dv(opened), "short_description": body["short_description"], "description": body["description"]})
        rec.update(kw)
        return rec

    def history(self) -> list[tuple]:
        out = []
        C = content("servicenow/history.yaml")
        for h in self.ctx.scenario["history"]:
            body = C[h["id"]]
            opened, resolved, closed = (self.c.t(h[k]) for k in ("opened", "resolved", "closed"))
            rec = self.incident(h["number"], opened, body, **{
                "state": "Closed", "incident_state": "Closed", "active": "false", "priority": PRIORITY[h["priority"]],
                "impact": body["impact"], "urgency": body["urgency"], "severity": body["severity"], "category": body["category"],
                "contact_type": body["contact_type"], "caller_id": self.name(body["caller"]), "opened_by": self.name(body["opened_by"]),
                "assigned_to": self.name(body["assigned_to"]), "assignment_group": h["group"], "cmdb_ci": h["ci"],
                "business_service": body["business_service"], "resolved_at": self.dv(resolved), "closed_at": self.dv(closed),
                "resolved_by": self.name(body["resolved_by"]), "closed_by": self.name(body["closed_by"]), "close_code": body["close_code"],
                "close_notes": body["close_notes"], "calendar_duration": duration(opened, resolved), "business_duration": duration(opened, resolved),
                "reassignment_count": body["reassignment_count"], "sys_created_by": body["opened_by"], "sys_updated_on": self.dv(closed),
                "sys_updated_by": body["closed_by"], "sys_mod_count": str(6 + len(body["work_notes"]))})
            out.append((opened, rec, h["kind"], h.get("sig_id"), "snow.incident.history"))
            for wn in body["work_notes"]:
                self.note(self.c.t(wn["at"]), h["number"], rec["sys_id"], "work_notes", wn["by"], wn["text"], h["kind"], cls="snow.journal.history")
        return out

    def noise_incidents(self) -> list[tuple]:
        out = []
        for key, b in content("servicenow/incidents-noise.yaml").items():
            opened = self.eastern(b["opened_at"])
            resolved = opened + timedelta(minutes=b["resolved_after_min"])
            closed = resolved + timedelta(days=3)          # auto-close three days after resolution
            number = self.number_at(opened)
            caller = b["caller"]
            rec = self.incident(number, opened, b, **{
                "state": "Closed", "incident_state": "Closed", "active": "false", "priority": PRIORITY[b["priority"]],
                "impact": b["impact"], "urgency": b["urgency"], "severity": "3 - Low", "category": b["category"],
                "contact_type": b["contact_type"], "caller_id": self.name(caller), "opened_by": self.name(caller),
                "assigned_to": self.name(b["assigned_to"]), "assignment_group": b["assignment_group"], "cmdb_ci": b["cmdb_ci"],
                "business_service": b["business_service"], "resolved_at": self.dv(resolved), "closed_at": self.dv(closed),
                "resolved_by": self.name(b["assigned_to"]), "closed_by": "System Administrator", "close_code": b["close_code"],
                "close_notes": b["close_notes"], "calendar_duration": duration(opened, resolved), "business_duration": duration(opened, resolved),
                "reassignment_count": "0", "sys_created_by": caller, "sys_updated_on": self.dv(closed), "sys_updated_by": "system",
                "sys_mod_count": "7"})
            out.append((opened, rec, "noise", None, "snow.incident.noise"))
            wn = b["work_notes"][0]           # the triage note; the close notes carry the resolution (record budget)
            self.note(opened + timedelta(minutes=wn["after_min"]), number, rec["sys_id"], "work_notes", wn["by"], wn["text"], "noise",
                      cls="snow.journal.noise")
        return out

    def pick_order(self, report: str, t: datetime) -> str:
        """A real order from the request stream for a customer's ticket, one the served logs keep (rates in
        integration.yaml sampling:), so the agent can follow it. Deterministic: the latest that fits."""
        reqs = self.m.requests()
        def latest(pred, lo_s, hi_s):
            best = None
            for q in reqs:
                if t - timedelta(seconds=hi_s) <= q.t <= t - timedelta(seconds=lo_s) and q.cart not in self.used_carts and pred(q):
                    best = q
            return best
        failing = lambda q: q.outcome in ("gave_up", "pool_timeout") and q.u < 1 / 100
        if report == "spinner":
            q = latest(failing, 60, 1200)
        elif report == "spinner_repeat":
            # The customer tried three times: a cart with at least three failed submits, quoted by its last order.
            fails: dict[int, int] = {}
            for x in reqs:
                if x.t < t and failing(x):
                    fails[x.cart] = fails.get(x.cart, 0) + 1
            q = latest(lambda q: failing(q) and fails.get(q.cart, 0) >= 3, 30, 1200)
        elif report == "charged_unconfirmed":
            q = latest(lambda q: q.outcome == "gave_up" and q.u < 1 / 100, 120, 900)
        elif report == "declined":
            q = latest(lambda q: q.outcome == "fail_fast" and q.u < 1 / 60, 30, 1200)
        elif report == "charged_twice":
            # The customer quotes the order that went through after an earlier attempt for the same cart had
            # been taken by the provider while the gateway gave up on it.
            # The customer quotes the attempt that errored (the provider took it while the gateway gave up); the
            # same cart's retry went through. The failed attempt is in the gateway and checkout-api logs.
            paid = {}
            for x in reqs:
                if x.outcome in ("ok", "slow_ok") and x.t < t:
                    paid[x.cart] = x.t
            q = latest(lambda q: q.outcome == "gave_up" and q.u < 1 / 100 and paid.get(q.cart, q.t) > q.t, 60, 3600)
        else:
            raise KeyError(report)
        if q is None:
            raise ValueError(f"no order for a {report} ticket at {t}")
        self.used_orders.add(q.order)
        self.used_carts.add(q.cart)
        return q.order.split("-", 1)[1]

    def customer_incidents(self) -> list[tuple]:
        out = []
        sc = self.ctx.scenario["servicenow"]["customer_incidents"]
        early = content("servicenow/live.yaml")["early"]
        late = content("servicenow/incidents-customer.yaml")["late"]
        n = int(sc["numbers_from"][3:])
        for part, items in (("early", early), ("late", late)):
            spec = sc[part]
            for it in items:
                t = self.c.t(it["at"])
                order = self.pick_order(it["report"], t)
                desc = it["description"].replace("order {order}", "{order}").replace("{order}", f"order {order}")
                body = {"short_description": it["short_description"], "description": desc}
                number = f"INC{n:07d}"
                n += 1
                rec = self.incident(number, t, body, **{
                    "state": "New", "incident_state": "New", "active": "true", "priority": "4 - Low", "impact": "3 - Low",
                    "urgency": "2 - Medium", "severity": "3 - Low", "category": "Inquiry / Help", "contact_type": it["contact_type"],
                    "caller_id": "Guest", "opened_by": "Customer Support Desk", "assignment_group": "Customer Support",
                    "business_service": "Online Checkout", "parent_incident": "INC0048811" if part == "late" else "",
                    "sys_created_by": "support.desk", "sys_updated_on": self.dv(t), "sys_updated_by": "support.desk", "sys_mod_count": "0"})
                out.append((t, rec, spec["kind"], spec["sig_id"], "snow.incident.customer"))
        return out

    def live(self) -> None:
        """INC0048811 as the integration received each update: a version per field change or journal entry,
        each a single-record GET response; its field history in sys_audit."""
        L = self.ctx.scenario["servicenow"]["live"]
        C = content("servicenow/live.yaml")["inc0048811"]
        J = content("servicenow/live.yaml")["journal"]
        number, opened = L["number"], self.c.t(L["opened"])
        sid = sys_id("incident", number)
        summary, rows = pgsa_text(self.ctx, self.m)
        for j in L["journal"]:
            text = J[j["key"]].replace("{pgsa_summary}", summary).replace("{pgsa_rows}", rows)
            self.note(self.c.t(j["at"]), number, sid, j["element"], j["by"], text, j["kind"], j.get("sig_id"), cls=j.get("class", "snow.journal.live"),
                      key=j["key"])
        for i, f in enumerate(content("servicenow/journal-filler.yaml")["entries"]):
            self.note(self.c.t(f["at"]), number, sid, f["element"], f["by"], f["text"], "noise", cls="snow.journal.filler", key=f"filler{i}")
        rec = self._as_created(opened, C, L)
        updates: list[tuple] = []        # (t, kind, payload)
        audit = []
        groups = {g["name"]: g["sn_sys_id"] for g in self.ctx.facts["groups"]}
        users = {p["handle"]: p["sn_sys_id"] for p in self.ctx.facts["people"]}
        for a in L["audit"]:
            updates.append((self.c.t(a["at"]), 0, a))
        for t, row, kind, sig, cls in self.journal:
            if row["element_id"] == sid:
                updates.append((t, 1, row))
        updates.sort(key=lambda u: (u[0], u[1]))
        versions = [(opened, rec)]
        reassign = 0
        for t, typ, u in updates:
            rec = dict(rec)
            if typ == 0:
                f, new = u["field"], u["new"]
                old_stored, new_stored = u["old"], u["new"]
                if f == "state":
                    rec["state"] = rec["incident_state"] = new
                    old_stored, new_stored = STATE_CODE.get(u["old"], ""), STATE_CODE[new]
                    if new == "Resolved":
                        rec.update({"resolved_at": self.dv(t), "resolved_by": self.name(u["by"]), "close_code": C["close_code"],
                                    "close_notes": C["close_notes"], "calendar_duration": duration(opened, t), "business_duration": duration(opened, t)})
                elif f == "assignment_group":
                    rec["assignment_group"] = new
                    reassign += 1
                    rec["reassignment_count"] = str(reassign)
                    old_stored, new_stored = groups[u["old"]], groups[new]
                elif f == "assigned_to":
                    rec["assigned_to"] = self.name(new) if new else ""
                    old_stored, new_stored = users.get(u["old"], ""), users.get(new, "")
                elif f == "priority":
                    rec["priority"] = PRIORITY[int(new)]
                    if new == "1":       # priority follows impact x urgency: Alice raised impact to 1 - High
                        audit.append((t, {"sys_id": sys_id("sys_audit", number, t.isoformat(), "impact"), "tablename": "incident",
                                          "documentkey": sid, "fieldname": "impact", "oldvalue": rec["impact"][0], "newvalue": "1",
                                          "user": u["by"], "reason": "", "record_checkpoint": "", "internal_checkpoint": f"{int(t.timestamp() * 1000) - 1:x}",
                                          "sys_created_by": u["by"], "sys_created_on": self.dv(t)}, {"field": "impact"}))
                        rec["impact"] = C["impact"]["at_p1"]
                audit.append((t, {"sys_id": sys_id("sys_audit", number, t.isoformat(), f), "tablename": "incident", "documentkey": sid,
                                  "fieldname": f, "oldvalue": old_stored, "newvalue": new_stored, "user": u["by"], "reason": "",
                                  "record_checkpoint": "", "internal_checkpoint": f"{int(t.timestamp() * 1000):x}",
                                  "sys_created_by": u["by"], "sys_created_on": self.dv(t)}, u))
                by = u["by"]
            else:
                by = u["sys_created_by"]
            rec["sys_updated_on"], rec["sys_updated_by"] = self.dv(t), by
            if t > opened and versions[-1][0] != t:          # one increment per save, not per field
                rec["sys_mod_count"] = str(int(rec["sys_mod_count"]) + 1)
            if audit and audit[-1][0] == t:
                for k_ in range(len(audit) - 1, -1, -1):
                    if audit[k_][0] == t:
                        audit[k_][1]["record_checkpoint"] = rec["sys_mod_count"]
            if versions[-1][0] == t:
                versions[-1] = (t, rec)
            else:
                versions.append((t, rec))
        export = self.c.t("15:30")
        for k, (t, v) in enumerate(versions):
            if t > export:
                break
            end = versions[k + 1][0] if k + 1 < len(versions) else None
            rel = f"servicenow/major_incident/{number}.v{k + 1}.json"
            self.ctx.write_json(rel, {"result": v})
            self.ctx.row(snow_handle(number), rel, "/", opened, "snow.incident.live", L["kind"], L.get("sig_id"),
                         t_known=t, valid_from=t, valid_to=end)
        audit.sort(key=lambda a: a[0])
        out = []
        for i, (t, row, u) in enumerate(audit):
            u.setdefault("by", row["user"])
            out.append(row)
            reassign = u["field"] == "assignment_group"
            self.ctx.row(audit_handle(row["sys_id"]), "servicenow/sys_audit.json", f"/result/{i}", t,
                         "snow.audit.reassign" if reassign else "snow.audit", L["reassign"]["kind"] if reassign else "noise",
                         L["reassign"]["sig_id"] if reassign else None)
        self.ctx.write_json("servicenow/sys_audit.json", {"result": out})
        self.ctx.count("records:servicenow_incidents")
        self.ctx.count("records:servicenow_audit", len(out))

    def _as_created(self, opened: datetime, C: dict, L: dict) -> dict:
        return self.incident(L["number"], opened, C, **{
            "state": "New", "incident_state": "New", "active": "true", "priority": PRIORITY[L["priority"]], "impact": C["impact"]["initial"],
            "urgency": C["urgency"], "severity": C["severity"], "category": C["category"], "subcategory": C["subcategory"],
            "contact_type": C["contact_type"], "caller_id": self.name(L["caller"]), "opened_by": self.name(L["opened_by"]),
            "assignment_group": L["initial_group"], "cmdb_ci": L["ci"], "business_service": L["business_service"],
            "correlation_id": C["correlation_id"], "correlation_display": C["correlation_display"], "reassignment_count": "0",
            "sys_created_by": L["opened_by"], "sys_updated_on": self.dv(opened), "sys_updated_by": L["opened_by"], "sys_mod_count": "0"})

    # -- changes ----------------------------------------------------------------------------------------

    def changes(self) -> list[dict]:
        C = dict(content("servicenow/changes.yaml"))
        C.update(content("servicenow/changes-noise.yaml"))
        out = []
        for ch in self.ctx.scenario["changes"]:
            body = C[ch["id"]]
            created = self.c.t(ch.get("opened") or body["created"])
            p0, p1 = (self.c.t(x) for x in ch["planned"])
            a0 = self.c.t(ch["actual"][0]) if ch["actual"][0] else None
            a1 = self.c.t(ch["actual"][1]) if ch["actual"][1] else None
            rec = self.blank("change_request")
            closed = ch["state"] == "Closed"
            closed_at = (a1 + timedelta(minutes=12)) if closed and a1 else None
            if ch["id"] == "chg0031902" and a1:
                closed_at = a1 + timedelta(seconds=40)
            started = ch["state"] in ("Implement", "Review", "Closed")
            phase = {"Closed": "Complete", "Implement": "Implementation", "Review": "Post Implementation Review",
                     "Scheduled": "Scheduled", "Assess": "Assessment"}[ch["state"]]
            rec.update({
                "number": ch["number"], "sys_id": sys_id("change_request", ch["number"]), "type": ch["type"].capitalize(),
                "state": ch["state"], "active": "false" if closed else "true", "short_description": body["short_description"],
                "description": body["description"], "implementation_plan": body["implementation_plan"], "backout_plan": body["backout_plan"],
                "test_plan": body["test_plan"], "justification": body.get("justification", ""), "risk": body["risk"],
                "risk_impact_analysis": body.get("risk_impact_analysis", ""), "impact": body["impact"], "urgency": "3 - Low",
                "priority": "4 - Low" if body["risk"] == "Low" else ("1 - Critical" if ch["type"] == "emergency" else "3 - Moderate"),
                "cmdb_ci": ch["ci"], "assignment_group": ch["group"], "requested_by": self.name(body["requested_by"]),
                "assigned_to": self.name(body["assigned_to"]), "opened_by": self.name(body["requested_by"]), "opened_at": self.dv(created),
                "start_date": self.dv(p0), "end_date": self.dv(p1), "work_start": self.dv(a0), "work_end": self.dv(a1),
                "cab_date": self.dv(self.c.t(ch["cab_date"])) if ch.get("cab_date") else "", "cab_required": "true" if ch["type"] == "normal" else "false",
                "cab_recommendation": body.get("cab_recommendation", ""),
                "approval": "Requested" if ch["type"] == "emergency" else ("Approved" if started or ch["state"] == "Scheduled" else "Not Yet Requested"),
                "close_code": body["close_code"], "close_notes": body["close_notes"], "closed_at": self.dv(closed_at),
                "closed_by": self.name(body["assigned_to"]) if closed else "",
                "category": "Software" if "deploy" in body["implementation_plan"].lower() or "Jenkins" in body["implementation_plan"] else "Network" if "irewall" in body["short_description"] else "Other",
                "phase": phase, "production_system": "true", "outside_maintenance_schedule": "true" if ch["type"] == "emergency" else "false",
                "unauthorized": "false", "on_hold": "false", "review_status": "", "escalation": "Normal", "made_sla": "true",
                "sys_class_name": "Change Request", "sys_created_on": self.dv(created), "sys_created_by": body["requested_by"],
                "sys_updated_on": self.dv(max(created, closed_at or a0 or created)), "sys_updated_by": body["assigned_to"],
                "sys_mod_count": str(9 if closed else 4), "sys_domain": "global", "sys_domain_path": "/", "company": "Larkspur Retail",
                "u_jenkins_job": ch.get("jenkins", ""), "chg_model": ch["type"].capitalize(), "contact_type": "Self-service",
                "upon_approval": "Proceed to Next Task", "upon_reject": "Cancel all future Tasks", "conflict_status": "No Conflict",
            })
            out.append((created, rec, ch, a0 or p0))
        out.sort(key=lambda x: x[0])
        rows = []
        for i, (created, rec, ch, t_event) in enumerate(out):
            rows.append(rec)
            self.ctx.row(snow_handle(rec["number"]), "servicenow/change_request.json", f"/result/{i}", t_event, "snow.change",
                         ch["kind"], ch.get("sig_id"), t_known=created)
        return rows

    # -- render -------------------------------------------------------------------------------------------

    def render(self) -> None:
        ctx = self.ctx
        users = self.users()
        ctx.write_json("servicenow/sys_user.json", {"result": users})
        ctx.write_json("servicenow/sys_user_group.json", {"result": self.user_groups()})
        cis, rels = self.cmdb()
        ctx.write_json("servicenow/cmdb_ci.json", {"result": cis})
        ctx.write_json("servicenow/cmdb_rel_ci.json", {"result": rels})
        inc = sorted(self.history() + self.noise_incidents() + self.customer_incidents(), key=lambda x: x[0])
        for i, (t, rec, kind, sig, cls) in enumerate(inc):
            ctx.row(snow_handle(rec["number"]), "servicenow/incident.json", f"/result/{i}", t, cls, kind, sig)
        ctx.write_json("servicenow/incident.json", {"result": [x[1] for x in inc]})
        self.live()
        chg = self.changes()
        ctx.write_json("servicenow/change_request.json", {"result": chg})
        self.journal.sort(key=lambda x: x[0])
        jn = []
        for i, (t, rec, kind, sig, cls) in enumerate(self.journal):
            jn.append(rec)
            ctx.row(journal_handle(rec["sys_id"]), "servicenow/sys_journal_field.json", f"/result/{i}", t, cls, kind, sig)
        ctx.write_json("servicenow/sys_journal_field.json", {"result": jn})
        ctx.count("records:servicenow_incidents", len(inc))
        ctx.count("records:servicenow_changes", len(chg))
        ctx.count("records:servicenow_journal", len(jn))
        ctx.count("records:cmdb_ci", len(cis))
        ctx.count("records:cmdb_rel_ci", len(rels))
        ctx.count("records:servicenow_users", len(users))
