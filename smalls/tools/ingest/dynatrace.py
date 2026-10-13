"""Dynatrace: problems and the entity list.

A problem is served as one snapshot per state: an open version known from its `startTime` and a closed
version known from its `endTime`. Both keep the problem's start as their event time. The entity list is
the topology as synced before the incident.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Iterator

from smalls.contracts import handles as H
from smalls.tools.ingest.base import Item, Record, clean, elements, version_number, versioned


def _names(items) -> list[str]:
    return [x.get("name") for x in items or [] if x.get("name")]


def adapter(bundle, source: str) -> Iterator[Item]:
    tz_rule = bundle.integration.tz_rule(source)
    by_problem: dict[str, list[Record]] = {}
    files = sorted(bundle.files(source, "problems/*.json"), key=lambda rel: (rel.rsplit(".v", 1)[0], version_number(PurePosixPath(rel).name)))
    for rel in files:
        text = bundle.text(rel)
        p = json.loads(text)
        closed = p.get("status") != "OPEN" and (p.get("endTime") or -1) > 0
        known = int(p["endTime"] if closed else p["startTime"])
        evidence = [clean({"type": e.get("evidenceType"), "name": e.get("displayName"), "entity": (e.get("entity") or {}).get("name"),
                           "root_cause_relevant": e.get("rootCauseRelevant")})
                    for e in (p.get("evidenceDetails") or {}).get("details") or []]
        affected = _names(p.get("affectedEntities"))
        root = (p.get("rootCauseEntity") or {}).get("name")
        by_problem.setdefault(p["displayId"], []).append(Record(
            handle=H.dt_problem_handle(p["displayId"]), source=source, dataset="problems", kind="alert", path=rel, locator="/",
            native=text, t_event=int(p["startTime"]), t_known=known, t_end=int(p["endTime"]) if closed else None,
            native_id=p.get("problemId"), entity_raw=affected[0] if affected else root, stream_key=p["displayId"],
            t_native=str(known), tz_rule=tz_rule, sev_text=p.get("severityLevel"), status=p.get("status"), title=p.get("title"),
            attrs=clean({"display_id": p["displayId"], "impact_level": p.get("impactLevel"), "affected": affected,
                         "impacted": _names(p.get("impactedEntities")), "root_cause": root,
                         "management_zones": _names(p.get("managementZones")),
                         "tags": [t.get("stringRepresentation") for t in p.get("entityTags") or []], "evidence": evidence})))
    for versions in by_problem.values():
        yield from versioned(versions)

    for rel in bundle.files(source, "entities.json"):
        for pointer, raw, e in elements(bundle.text(rel), "/entities"):
            t = int(e["firstSeenTms"])
            yield Record(
                handle=H.dt_entity_handle(e["entityId"]), source=source, dataset="entities", kind="entity", path=rel, locator=pointer,
                native=raw, t_event=t, t_known=t, native_id=e["entityId"], entity_raw=e.get("displayName"), t_native=str(t),
                tz_rule=tz_rule, title=e.get("displayName"),
                attrs=clean({"type": e.get("type"), "tags": [t.get("stringRepresentation") for t in e.get("tags") or []],
                             "management_zones": _names(e.get("managementZones")), "from": e.get("fromRelationships"),
                             "to": e.get("toRelationships"), "last_seen": e.get("lastSeenTms")}))
