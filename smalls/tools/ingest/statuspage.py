"""A provider status page (Statuspage shape), served as one snapshot per published state:
`status.v<n>.json`. Each snapshot is its own handle, known from its `page.updated_at`, and valid until the
next one is published. The first snapshot is the page as it already stood, so nothing bounds it from below.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Iterator

from smalls.contracts import handles as H
from smalls.tools.ingest.base import Item, Record, clean

SNAPSHOTS = "status.v*.json"


def adapter(bundle, source: str) -> Iterator[Item]:
    integ = bundle.integration
    snapshots: list[Record] = []
    for rel in bundle.files(source, SNAPSHOTS):
        text = bundle.text(rel)
        d = json.loads(text)
        page, status = d.get("page") or {}, d.get("status") or {}
        version = int(re.search(r"\.v(\d+)\.", PurePosixPath(rel).name).group(1))
        t, _ = integ.parse(source, page["updated_at"])
        incidents = [clean({"name": i.get("name"), "status": i.get("status"), "impact": i.get("impact"),
                            "started_at": i.get("started_at"), "created_at": i.get("created_at"),
                            "updates": [clean({"status": u.get("status"), "at": u.get("display_at") or u.get("created_at"),
                                               "body": u.get("body")}) for u in i.get("incident_updates") or []]})
                     for i in d.get("incidents") or []]
        body = "\n".join(f"{i['name']} [{i.get('status')}]: {i['updates'][0].get('body', '')}" if i.get("updates") else i["name"]
                         for i in incidents) or None
        snapshots.append(Record(
            handle=H.psp_handle(source, version), source=source, dataset="status", kind="status", path=rel, locator="/",
            native=text, t_event=t, t_known=t, version=version, native_id=page.get("id"), entity_raw=page.get("name"),
            t_native=page["updated_at"], tz_rule=integ.tz_rule(source), status=status.get("indicator"),
            title=status.get("description"), body=body,
            attrs=clean({"page": page.get("name"), "url": page.get("url"), "time_zone": page.get("time_zone"),
                         "components": [{"name": c.get("name"), "status": c.get("status")} for c in d.get("components") or []],
                         "incidents": incidents, "scheduled_maintenances": len(d.get("scheduled_maintenances") or [])})))
    snapshots.sort(key=lambda r: r.version)
    for snap, later in zip(snapshots, snapshots[1:] + [None]):
        snap.valid_from = snap.t_known if snap is not snapshots[0] else None
        snap.valid_to = later.t_known if later else None
        yield snap
