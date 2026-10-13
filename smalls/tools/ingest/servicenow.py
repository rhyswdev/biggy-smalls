"""ServiceNow Table API exports (`sysparm_display_value=true`): display strings only, every value a string,
times in the integration user's zone with no marker, reference fields as display names.

- Every table row is known from its `sys_created_on`. A change request happened when its work started
  (`work_start`, else the planned `start_date`); every other row happened when it was created.
- The live incident is served as one snapshot per save under `major_incident/`, each known from its
  `sys_updated_on`. There is no rewind: `sys_audit` rows are records in their own right.
- Journal entries and audit rows point at their record through its `sys_id`, resolved inside this source.
- Close notes and journal entries are also chunked for the full-text table, each tied to its own record
  version, so a note is searchable exactly when it is visible.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Iterator

from smalls.contracts import handles as H
from smalls.tools.ingest.base import Item, Record, clean, elements, version_number, versioned


def adapter(bundle, source: str) -> Iterator[Item]:
    integ = bundle.integration
    tz_rule = integ.tz_rule(source)
    by_sys_id: dict[str, str] = {}      # a task's sys_id -> its handle, for journal and audit parents

    def when(value: str | None) -> tuple[int | None, bool]:
        return integ.parse(source, value) if value else (None, False)

    def table(name: str) -> Iterator[tuple[str, str, str, dict]]:
        rel = f"{source}/{name}.json"
        if rel in bundle.files(source, f"{name}.json"):
            for pointer, raw, row in elements(bundle.text(rel), "/result"):
                yield rel, pointer, raw, row

    def base(rel, pointer, raw, row, dataset, kind, handle, t_known_field="sys_created_on", **more) -> Record:
        t_known, ambiguous = when(row[t_known_field])
        more.setdefault("t_event", t_known)
        more.setdefault("native_id", row.get("number") or row.get("sys_id"))
        return Record(handle=handle, source=source, dataset=dataset, kind=kind, path=rel, locator=pointer, native=raw,
                      t_known=t_known, t_native=row[t_known_field], tz_rule=tz_rule, flags={"dst_fold": True} if ambiguous else {}, **more)

    def incident(rel, pointer, raw, row, t_known_field="sys_created_on") -> Record:
        by_sys_id[row["sys_id"]] = H.snow_handle(row["number"])
        chunks = []
        if row.get("close_notes"):
            facts = " · ".join(f"{label}: {row[f]}" for label, f in (("CI", "cmdb_ci"), ("Category", "category"), ("Close code", "close_code")) if row.get(f))
            chunks.append((f"{row['number']} {row.get('short_description', '')}".strip(), f"{row['close_notes']}\n\n{facts}".strip()))
        return base(rel, pointer, raw, row, "incident", "incident", H.snow_handle(row["number"]), t_known_field,
                    t_event=when(row["sys_created_on"])[0], t_end=when(row.get("resolved_at") or row.get("closed_at"))[0],
                    entity_raw=row.get("cmdb_ci") or row.get("business_service") or None, sev_text=row.get("priority") or None,
                    status=row.get("state") or None, title=row.get("short_description") or None, body=row.get("description") or None,
                    parent=H.snow_handle(row["parent_incident"]) if row.get("parent_incident") else None,
                    attrs=clean({k: v for k, v in row.items() if k not in ("short_description", "description")}), chunks=chunks)

    # The live incident's snapshots, in the order of their version suffix.
    live: dict[str, list[Record]] = {}
    snapshots = sorted(bundle.files(source, "major_incident/*.json"), key=lambda rel: (rel.rsplit(".v", 1)[0], version_number(PurePosixPath(rel).name)))
    for rel in snapshots:
        text = bundle.text(rel)
        row = json.loads(text)["result"]
        live.setdefault(row["number"], []).append(incident(rel, "/", text, row, t_known_field="sys_updated_on"))
    for versions in live.values():
        yield from versioned(versions)

    for rel, pointer, raw, row in table("incident"):
        yield incident(rel, pointer, raw, row)

    for rel, pointer, raw, row in table("change_request"):
        by_sys_id[row["sys_id"]] = H.snow_handle(row["number"])
        yield base(rel, pointer, raw, row, "change_request", "change", H.snow_handle(row["number"]),
                   t_event=when(row.get("work_start") or row.get("start_date") or row["sys_created_on"])[0], t_end=when(row.get("work_end"))[0],
                   entity_raw=row.get("cmdb_ci") or None, sev_text=row.get("priority") or None, status=row.get("state") or None,
                   title=row.get("short_description") or None, body=row.get("description") or None,
                   attrs=clean({k: v for k, v in row.items() if k not in ("short_description", "description")}))

    for rel, pointer, raw, row in table("sys_journal_field"):
        parent = by_sys_id.get(row.get("element_id"))
        heading = " ".join(x for x in ((parent or "").split(":")[-1], (row.get("element") or "").replace("_", " ")) if x)
        yield base(rel, pointer, raw, row, "sys_journal_field", "note", H.journal_handle(row["sys_id"]), body=row.get("value") or None,
                   parent=parent, attrs=clean({k: v for k, v in row.items() if k != "value"}),
                   chunks=[(heading or None, row["value"])] if row.get("value") else [])

    for rel, pointer, raw, row in table("sys_audit"):
        yield base(rel, pointer, raw, row, "sys_audit", "audit", H.audit_handle(row["sys_id"]), title=row.get("fieldname") or None,
                   parent=by_sys_id.get(row.get("documentkey")), attrs=clean(dict(row)))

    for rel, pointer, raw, row in table("cmdb_ci"):
        yield base(rel, pointer, raw, row, "cmdb_ci", "ci", H.ci_handle(row["name"]), native_id=row.get("sys_id"), entity_raw=row["name"],
                   status=row.get("operational_status") or None, title=row["name"], body=row.get("short_description") or None,
                   attrs=clean({k: v for k, v in row.items() if k not in ("name", "short_description")}))

    for rel, pointer, raw, row in table("cmdb_rel_ci"):
        yield base(rel, pointer, raw, row, "cmdb_rel_ci", "rel", H.rel_handle(row["sys_id"]), entity_raw=row.get("parent") or None,
                   title=f"{row.get('parent')} [{row.get('type')}] {row.get('child')}", attrs=clean(dict(row)))

    for rel, pointer, raw, row in table("sys_user"):
        yield base(rel, pointer, raw, row, "sys_user", "user", H.user_handle(row["user_name"]), native_id=row["user_name"],
                   status="active" if row.get("active") == "true" else "inactive", title=row.get("name") or row["user_name"],
                   attrs=clean({k: v for k, v in row.items() if k != "name"}))

    for rel, pointer, raw, row in table("sys_user_group"):
        yield base(rel, pointer, raw, row, "sys_user_group", "group", H.group_handle(row["sys_id"]),
                   status="active" if row.get("active") == "true" else "inactive", title=row.get("name") or None,
                   body=row.get("description") or None, attrs=clean({k: v for k, v in row.items() if k not in ("name", "description")}))
