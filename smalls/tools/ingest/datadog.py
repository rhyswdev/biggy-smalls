"""Datadog: the event stream, monitors, synthetic tests and metric query responses.

- An event is a monitor transition. Events of one monitor (and one group, for a multi-alert) share a
  `stream_key`, and each points at its monitor.
- Monitors are configuration as synced before the incident. `overall_state` is the value at that sync, so
  it is withheld: the tool that lists alerts derives the state at `now` from the visible events.
- Synthetic tests carry no time in their bytes: `t_known` is 0 and they are always visible.
- A metric response becomes one `point` row per bucket, known when the bucket closes. Its envelope is kept
  as a reference file with the fields that say how far the data runs withheld, the points among them:
  points are read through `visible_point`, never from the envelope.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Iterator

from smalls.contracts import handles as H
from smalls.tools.ingest.base import Item, Point, Record, Ref, clean, elements, ms_of, sev_of

# What a monitor or event is about, unresolved: its first tag under one of these keys.
_ENTITY_TAGS = ("host", "service", "peer.service", "cluster", "kube_cluster_name", "kube_cronjob", "db", "site")
_TRANSITION = re.compile(r"^\[(?P<state>[^\]]+?)(?: on (?P<group>[^\]]+))?\]")
_ALERT_TYPES = {"success": 6, "user_update": 6, "recommendation": 6, "snapshot": 6}
METRIC_WITHHELD = ("to_date", "series[].end", "series[].length", "series[].pointlist")


def _entity(tags: list[str]) -> str | None:
    for key in _ENTITY_TAGS:
        for tag in tags or []:
            if tag.startswith(key + ":"):
                return tag
    return None


def _stream(monitor_id, group: str | None = None) -> str | None:
    if monitor_id is None:
        return None
    return f"monitor:{monitor_id}" + (f"|{group}" if group else "")


def adapter(bundle, source: str) -> Iterator[Item]:
    tz_rule = bundle.integration.tz_rule(source)
    for rel in bundle.files(source, "events.jsonl"):
        for n, line in enumerate(bundle.text(rel).splitlines(), 1):
            ev = json.loads(line)["event"]
            t = int(ev["date_happened"]) * 1000
            m = _TRANSITION.match(ev.get("title") or "")
            group = m["group"] if m else None
            kind = ev.get("alert_type")
            yield Record(
                handle=H.dd_event_handle(ev["id"]), source=source, dataset="events", kind="alert", path=rel, locator=n, native=line,
                t_event=t, t_known=t, native_id=str(ev["id"]), host=ev.get("host"),
                entity_raw=group or ev.get("host") or _entity(ev.get("tags")), stream_key=_stream(ev.get("monitor_id"), group),
                t_native=str(ev["date_happened"]), tz_rule=tz_rule, sev=sev_of(kind) if sev_of(kind) is not None else _ALERT_TYPES.get(kind),
                sev_text=kind, status=m["state"] if m else None, title=ev.get("title"), body=ev.get("text"),
                parent=H.dd_monitor_handle(ev["monitor_id"]) if ev.get("monitor_id") is not None else None,
                attrs=clean({k: ev.get(k) for k in ("monitor_id", "priority", "source_type_name", "tags", "url")}))

    for rel in bundle.files(source, "monitors.json"):
        for pointer, raw, mon in elements(bundle.text(rel), "/"):
            t = ms_of(datetime.fromisoformat(mon["created"]))
            options = mon.get("options") or {}
            yield Record(
                handle=H.dd_monitor_handle(mon["id"]), source=source, dataset="monitors", kind="config", path=rel, locator=pointer,
                native=raw, t_event=t, t_known=t, native_id=str(mon["id"]), entity_raw=_entity(mon.get("tags")),
                stream_key=_stream(mon["id"]), t_native=mon["created"], tz_rule=tz_rule, title=mon.get("name"), body=mon.get("message"),
                attrs=clean({"type": mon.get("type"), "query": mon.get("query"), "tags": mon.get("tags"),
                             "thresholds": options.get("thresholds"), "priority": mon.get("priority"), "modified": mon.get("modified"),
                             "creator": (mon.get("creator") or {}).get("name")}),
                flags={"withheld": ["overall_state"]})

    for rel in bundle.files(source, "synthetics.json"):
        for pointer, raw, syn in elements(bundle.text(rel), "/"):
            request = ((syn.get("config") or {}).get("request") or {})
            yield Record(
                handle=H.dd_synthetic_handle(syn["public_id"]), source=source, dataset="synthetics", kind="config", path=rel,
                locator=pointer, native=raw, t_event=0, t_known=0, native_id=syn["public_id"],
                entity_raw=request.get("url") or _entity(syn.get("tags")), stream_key=_stream(syn.get("monitor_id")),
                status=syn.get("status"), title=syn.get("name"), body=syn.get("message"),
                attrs=clean({"type": syn.get("type"), "locations": syn.get("locations"), "monitor_id": syn.get("monitor_id"),
                             "tags": syn.get("tags"), "url": request.get("url"), "method": request.get("method"),
                             "tick_every": (syn.get("options") or {}).get("tick_every")}),
                flags={"undated": True})

    for rel in bundle.files(source, "metrics/*.json"):
        text = bundle.text(rel)
        d = json.loads(text)
        yield Ref(source=source, name=rel, native=text, flags={"withheld": list(METRIC_WITHHELD)})
        handle = H.metric_handle(d["query"])
        for si, series in enumerate(d.get("series") or []):
            interval = int(series["interval"])
            name = series.get("expression") or f"{d['query']}#{si}"
            for pi, (t, value) in enumerate(series.get("pointlist") or []):
                t = int(t)
                yield Point(handle=handle, series=name, t=t, value=value, interval_s=interval, t_known=t + interval * 1000,
                            path=rel, locator=f"/series/{si}/pointlist/{pi}")
