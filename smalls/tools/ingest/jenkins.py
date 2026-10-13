"""Jenkins builds (`api/json?depth=1`), served as two snapshots: `<n>.building.json` when the build starts
and `<n>.json` once it finishes. The building snapshot is known from `timestamp`, the finished one from
`timestamp + duration`; both keep the start as their event time. Each commit in a build's changeSet is a
record of its own that points at the build and rides with the build's versions.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Iterator

from smalls.contracts import handles as H
from smalls.tools.ingest.base import Item, Record, clean, elements, versioned


def _actions(build: dict) -> tuple[dict, list[str], str | None]:
    params, causes, user = {}, [], None
    for action in build.get("actions") or []:
        for p in action.get("parameters") or []:
            params[p.get("name")] = p.get("value")
        for c in action.get("causes") or []:
            causes.append(c.get("shortDescription"))
            user = user or c.get("userName") or c.get("userId")
    return params, [c for c in causes if c], user


def adapter(bundle, source: str) -> Iterator[Item]:
    tz_rule = bundle.integration.tz_rule(source)
    versions: dict[str, list[Record]] = {}
    # The building snapshot sorts before the finished one: that is the version order.
    files = sorted(bundle.files(source, "*.json"), key=lambda rel: (PurePosixPath(rel).parent, int(PurePosixPath(rel).name.split(".")[0]),
                                                                  not PurePosixPath(rel).name.endswith(".building.json")))
    for rel in files:
        text = bundle.text(rel)
        b = json.loads(text)
        job = PurePosixPath(rel).parts[-3]
        started = int(b["timestamp"])
        finished = None if b.get("building") else started + int(b.get("duration") or 0)
        known = started if finished is None else finished
        params, causes, user = _actions(b)
        build_handle = H.jenkins_build_handle(job, b["number"])
        versions.setdefault(build_handle, []).append(Record(
            handle=build_handle, source=source, dataset="builds", kind="build", path=rel, locator="/", native=text, t_event=started,
            t_known=known, t_end=finished, native_id=f"{job}#{b['number']}", entity_raw=job, t_native=str(known), tz_rule=tz_rule,
            status=b.get("result") or ("BUILDING" if b.get("building") else None), title=b.get("fullDisplayName"), body=b.get("description"),
            attrs=clean({"job": job, "number": b["number"], "parameters": clean(params), "causes": causes, "started_by": user,
                         "built_on": b.get("builtOn"), "duration_ms": b.get("duration") or None,
                         "estimated_duration_ms": b.get("estimatedDuration"), "url": b.get("url"),
                         "commits": len((b.get("changeSet") or {}).get("items") or [])})))
        if (b.get("changeSet") or {}).get("items"):
            for pointer, raw, item in elements(text, "/changeSet/items"):
                handle = H.jenkins_commit_handle(job, b["number"], item["commitId"])
                versions.setdefault(handle, []).append(Record(
                    handle=handle, source=source, dataset="builds", kind="commit", path=rel, locator=pointer, native=raw, t_event=started,
                    t_known=known, native_id=item["commitId"], entity_raw=job, t_native=str(known), tz_rule=tz_rule,
                    title=item.get("msg"), body=(item.get("comment") or "").strip() or None, parent=build_handle,
                    attrs=clean({"commit_id": item["commitId"], "author": (item.get("author") or {}).get("fullName"),
                                 "author_email": item.get("authorEmail"), "date": item.get("date"), "paths": item.get("affectedPaths")})))
    for records in versions.values():
        yield from versioned(records)
