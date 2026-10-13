"""PagerDuty REST v2 exports.

- An incident is served as one snapshot per status or assignment change (`incidents/<number>.v<n>.json`),
  each known from its `updated_at`; every snapshot keeps the incident's `created_at` as its event time.
- Log entries are append-only and point at their incident. An annotate entry is a note a person wrote;
  the others are the incident's machine timeline.
- Services are configuration as synced before the incident: `status` is the value at that sync and is
  withheld; the tool that shows a service derives its status at `now` from the visible incidents.
- Escalation policies carry no time in their bytes: `t_known` is 0 and they are always visible.
- An on-call shift is known from its `start`; its `end` is a scheduled time, known in advance.
"""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Iterator

from smalls.contracts import handles as H
from smalls.tools.ingest.base import Item, Record, clean, elements, version_number, versioned


def _summary(ref: dict | None) -> str | None:
    return (ref or {}).get("summary")


def adapter(bundle, source: str) -> Iterator[Item]:
    integ = bundle.integration
    tz_rule = integ.tz_rule(source)

    def when(value: str | None) -> int | None:
        return integ.parse(source, value)[0] if value else None

    number_of: dict[str, int] = {}      # an incident's opaque id -> its number, for log-entry parents
    by_number: dict[int, list[Record]] = {}
    snapshots = sorted(bundle.files(source, "incidents/*.json"), key=lambda rel: (rel.rsplit(".v", 1)[0], version_number(PurePosixPath(rel).name)))
    for rel in snapshots:
        text = bundle.text(rel)
        x = json.loads(text)["incident"]
        number_of[x["id"]] = x["incident_number"]
        by_number.setdefault(x["incident_number"], []).append(Record(
            handle=H.pd_incident_handle(x["incident_number"]), source=source, dataset="incidents", kind="incident", path=rel,
            locator="/", native=text, t_event=when(x["created_at"]), t_known=when(x["updated_at"]), t_end=when(x.get("resolved_at")),
            native_id=str(x["incident_number"]), entity_raw=_summary(x.get("service")), stream_key=x.get("incident_key"),
            t_native=x["updated_at"], tz_rule=tz_rule, sev_text=x.get("urgency"), status=x.get("status"), title=x.get("title"),
            attrs=clean({"id": x["id"], "service": _summary(x.get("service")), "service_id": (x.get("service") or {}).get("id"),
                         "escalation_policy": _summary(x.get("escalation_policy")), "priority": _summary(x.get("priority")),
                         "incident_key": x.get("incident_key"), "assigned_via": x.get("assigned_via"),
                         "assignments": [{"at": a.get("at"), "assignee": _summary(a.get("assignee"))} for a in x.get("assignments") or []],
                         "acknowledgements": [{"at": a.get("at"), "by": _summary(a.get("acknowledger"))} for a in x.get("acknowledgements") or []],
                         "last_status_change_at": x.get("last_status_change_at"), "last_status_change_by": _summary(x.get("last_status_change_by")),
                         "alert_counts": x.get("alert_counts"), "resolved_at": x.get("resolved_at"), "resolve_reason": x.get("resolve_reason")})))
    for versions in by_number.values():
        yield from versioned(versions)

    for rel in bundle.files(source, "log_entries.json"):
        for pointer, raw, x in elements(bundle.text(rel), "/log_entries"):
            t = when(x["created_at"])
            incident = x.get("incident") or {}
            number = number_of.get(incident.get("id"))
            if number is None:
                in_summary = re.match(r"\[#(\d+)\]", incident.get("summary") or "")
                number = int(in_summary.group(1)) if in_summary else None
            entry_type = (x.get("type") or "").removesuffix("_log_entry")
            yield Record(
                handle=H.pd_log_handle(x["id"]), source=source, dataset="log_entries", kind="note" if entry_type == "annotate" else "event",
                path=rel, locator=pointer, native=raw, t_event=t, t_known=t, native_id=x["id"], entity_raw=_summary(x.get("service")),
                t_native=x["created_at"], tz_rule=tz_rule, status=entry_type or None, title=x.get("summary"),
                parent=H.pd_incident_handle(number) if number is not None else None,
                attrs=clean({"type": x.get("type"), "agent": _summary(x.get("agent")), "channel": (x.get("channel") or {}).get("type"),
                             "service": _summary(x.get("service")), "assignees": [_summary(a) for a in x.get("assignees") or []],
                             "user": _summary(x.get("user")), "event_details": x.get("event_details")}))

    for rel in bundle.files(source, "services.json"):
        for pointer, raw, x in elements(bundle.text(rel), "/services"):
            t = when(x["created_at"])
            yield Record(
                handle=H.pd_service_handle(x["id"]), source=source, dataset="services", kind="config", path=rel, locator=pointer,
                native=raw, t_event=t, t_known=t, native_id=x["id"], entity_raw=x.get("name"), t_native=x["created_at"], tz_rule=tz_rule,
                title=x.get("name"), body=x.get("description"),
                attrs=clean({"escalation_policy": _summary(x.get("escalation_policy")), "escalation_policy_id": (x.get("escalation_policy") or {}).get("id"),
                             "auto_resolve_timeout": x.get("auto_resolve_timeout"), "urgency": (x.get("incident_urgency_rule") or {}).get("urgency"),
                             "alert_creation": x.get("alert_creation")}),
                flags={"withheld": ["status"]})

    for rel in bundle.files(source, "escalation_policies.json"):
        for pointer, raw, x in elements(bundle.text(rel), "/escalation_policies"):
            rules = [{"level": n, "delay_minutes": r.get("escalation_delay_in_minutes"), "targets": [_summary(t) for t in r.get("targets") or []]}
                     for n, r in enumerate(x.get("escalation_rules") or [], 1)]
            yield Record(
                handle=H.pd_policy_handle(x["id"]), source=source, dataset="escalation_policies", kind="config", path=rel, locator=pointer,
                native=raw, t_event=0, t_known=0, native_id=x["id"], entity_raw=x.get("name"), title=x.get("name"),
                attrs=clean({"rules": rules, "services": [_summary(s) for s in x.get("services") or []], "num_loops": x.get("num_loops")}),
                flags={"undated": True})

    for rel in bundle.files(source, "oncalls.json"):
        for pointer, raw, x in elements(bundle.text(rel), "/oncalls"):
            policy, user = x.get("escalation_policy") or {}, x.get("user") or {}
            t = when(x["start"])
            yield Record(
                handle=H.pd_oncall_handle(policy["id"], x["escalation_level"], user["id"]), source=source, dataset="oncalls", kind="config",
                path=rel, locator=pointer, native=raw, t_event=t, t_known=t, t_end=when(x.get("end")), entity_raw=_summary(policy),
                t_native=x["start"], tz_rule=tz_rule, title=f"{_summary(policy)} level {x['escalation_level']}: {_summary(user)}",
                attrs=clean({"escalation_policy": _summary(policy), "escalation_policy_id": policy.get("id"), "level": x.get("escalation_level"),
                             "schedule": _summary(x.get("schedule")), "user": _summary(user), "user_id": user.get("id"),
                             "start": x.get("start"), "end": x.get("end")}))
