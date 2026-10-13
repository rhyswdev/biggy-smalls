"""Dynatrace: dynatrace/problems/<displayId>.v<n>.json (Problems API v2 problem as the notification
integration received it when it opened and once it closed) and dynatrace/entities.json (Monitored
entities API v2, VM-side only, as synced at 13:45)."""

from __future__ import annotations

from datetime import timedelta

from smalls.contracts.handles import dt_entity_handle, dt_problem_handle
from smalls_data.gen.core import Ctx, content, ms, rng


class Dynatrace:
    def __init__(self, ctx: Ctx):
        self.ctx, self.c = ctx, ctx.clock
        self.C = content("dynatrace.yaml")
        self.ent = {e["key"]: e for e in self.C["entities"]}
        self.zone = {z["name"]: z for z in self.C["management_zones"]}

    def ref(self, key: str) -> dict:
        e = self.ent[key]
        return {"entityId": {"id": e["id"], "type": e["type"]}, "name": e["name"]}

    def problem(self, p: dict, at_end: bool) -> dict:
        """The problem as GET /api/v2/problems/{id} returned it when it opened, or once it closed."""
        body = self.C["problems"][p["display_id"]]
        t0, t1 = self.c.t(p["start"]), self.c.t(p["end"])
        r = rng(self.ctx.seed, "dt", p["display_id"])
        pid = f"{r.randint(10**18, 9 * 10**18)}_{ms(t0)}V2"
        ev = []
        for e in body["evidence"]:
            d = {k: v for k, v in e.items() if k not in ("entity",)}
            d["entity"] = self.ref(e["entity"])
            d["startTime"] = ms(t0) - r.randint(30, 180) * 1000
            if e["evidenceType"] in ("TRANSACTIONAL", "METRIC", "AVAILABILITY_EVIDENCE") and at_end:
                d["endTime"] = ms(t1)
            if e["evidenceType"] == "EVENT":
                d["eventId"] = f"{r.randint(10**17, 9 * 10**17)}_{d['startTime']}"
            ev.append(d)
        first = self.ent[body["affected"][0]]
        zone = self.zone[first.get("zone", "Payments")]
        impacts = {"impacts": []}
        if "impact" in body:
            users = body["impact"]["estimatedAffectedUsers"]
            if not at_end:   # Davis's estimate when the problem opened: the users of its first two minutes
                users = max(1, round(users * 2 / max(2, (t1 - t0).total_seconds() / 60)))
            impacts = {"impacts": [{"impactType": body["impact"]["impactType"], "impactedEntity": self.ref(body["impact"]["entity"]),
                                    "estimatedAffectedUsers": users}]}
        return {"problemId": pid, "displayId": p["display_id"], "title": p["title"], "impactLevel": p["impact"],
                "severityLevel": p["severity"], "status": "CLOSED" if at_end else "OPEN",
                "affectedEntities": [self.ref(k) for k in body["affected"]],
                "impactedEntities": [self.ref(k) for k in body.get("impacted", body["affected"])],
                "rootCauseEntity": self.ref(body["root_cause"]) if body.get("root_cause") else None,
                "managementZones": [{"id": zone["id"], "name": zone["name"]}],
                "entityTags": [{"context": "CONTEXTLESS", "key": t.split(":")[0], "value": t.split(":")[1], "stringRepresentation": t}
                               for t in first.get("tags", []) if ":" in t],
                "problemFilters": [{"id": "c4d1a5e8-0f2b-4f6e-9d3a-7b8c2e1f6a40", "name": "Default"}],
                "startTime": ms(t0), "endTime": ms(t1) if at_end else -1,
                "evidenceDetails": {"totalCount": len(ev), "details": ev},
                "impactAnalysis": impacts, "linkedProblemInfo": None,
                "recentComments": {"comments": [], "totalCount": 0}}

    def render(self) -> None:
        """Problems are served as version snapshots, as the problem notification integration received them:
        dynatrace/problems/<displayId>.v1.json when it opened and .v2.json once it closed (ruling D1b)."""
        ctx = self.ctx
        export = self.c.t("15:30")
        n = 0
        for p in ctx.scenario["dynatrace"]:
            if not p.get("slice", True):
                continue
            t0, t1 = self.c.t(p["start"]), self.c.t(p["end"])
            closed = t1 <= export
            for v, at_end in ((1, False), (2, True)):
                if at_end and not closed:
                    continue
                rel = f"dynatrace/problems/{p['display_id']}.v{v}.json"
                ctx.write_json(rel, self.problem(p, at_end))
                ctx.row(dt_problem_handle(p["display_id"]), rel, "/", t0, "dt.problem", p.get("kind", "noise"), p.get("sig_id"),
                        t_known=t1 if at_end else t0, valid_from=t1 if at_end else t0, valid_to=None if at_end or not closed else t1)
            n += 1
        ctx.count("records:dynatrace_problems", n)
        # The entity list as synced at the start of the log window.
        synced = self.c.t("13:45")
        ents = []
        for e in self.C["entities"]:
            d = {"entityId": e["id"], "type": e["type"], "displayName": e["name"],
                 "firstSeenTms": ms(self.c.t("2024-01-09T08:00:00Z")), "lastSeenTms": ms(synced - timedelta(seconds=40)),
                 "tags": [{"context": "CONTEXTLESS", "key": t.split(":")[0], **({"value": t.split(":")[1]} if ":" in t else {}),
                           "stringRepresentation": t} for t in e.get("tags", [])],
                 "managementZones": [{"id": self.zone[e["zone"]]["id"], "name": e["zone"]}],
                 "fromRelationships": {}, "toRelationships": {}}
            if e.get("runs_on"):
                d["fromRelationships"]["runsOnHost"] = [{"id": self.ent[h]["id"], "type": "HOST"} for h in e["runs_on"]]
            if e.get("calls"):
                d["fromRelationships"]["calls"] = [{"id": self.ent[c]["id"], "type": "SERVICE"} for c in e["calls"]]
            ents.append(d)
            ctx.row(dt_entity_handle(e["id"]), "dynatrace/entities.json", f"/entities/{len(ents) - 1}", self.c.t("2024-01-09T08:00:00Z"),
                    "dt.entity", "noise")
        ctx.write_json("dynatrace/entities.json", {"totalCount": len(ents), "pageSize": 500, "entities": ents})
