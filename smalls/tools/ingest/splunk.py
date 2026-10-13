"""Splunk alert webhook deliveries and saved searches.

A delivery is one file, named by the time the receiving integration stored it. It happened when the search
fired (the epoch in its `sid`) and is known from its receipt time. A retried POST arrives as a second file
with the same body and `sid`, so deliveries of one alert share a `stream_key`. Each delivery points at its
saved search, which is a record in its own right and is never run (ruling C3).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import PurePosixPath
from typing import Iterator

from smalls.contracts import handles as H
from smalls.tools.ingest.base import UTC, Item, Record, clean, elements, ms_of

_FIRED = re.compile(r"_at_(\d+)_")


def alerts(bundle, source: str) -> Iterator[Item]:
    tz_rule = bundle.integration.tz_rule(source)
    for rel in bundle.files(source, "*.json"):
        text = bundle.text(rel)
        d = json.loads(text)
        result = d.get("result") or {}
        stamp = PurePosixPath(rel).name.split("-", 1)[0]
        received = ms_of(datetime.strptime(stamp, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=UTC))
        fired = _FIRED.search(d.get("sid", ""))
        yield Record(
            handle=H.splunk_alert_handle(rel, text), source=source, dataset="alerts", kind="alert", path=rel, locator="/",
            native=text, t_event=int(fired.group(1)) * 1000 if fired else received, t_known=received,
            native_id=d.get("sid"), host=result.get("host"), entity_raw=result.get("host"), stream_key=d.get("sid"),
            t_native=fired.group(1) if fired else stamp, tz_rule=tz_rule, title=d.get("search_name"), body=result.get("_raw"),
            parent=H.splunk_search_handle(d["search_name"]) if d.get("search_name") else None,
            attrs=clean({"owner": d.get("owner"), "app": d.get("app"), "results_link": d.get("results_link"), "received": stamp,
                         **{k: v for k, v in result.items() if k != "_raw"}}))


def saved_searches(bundle, source: str) -> Iterator[Item]:
    integ = bundle.integration
    text = bundle.text(source)
    for pointer, raw, s in elements(text, "/"):
        t, ambiguous = integ.parse(source, s["updated"])
        yield Record(
            handle=H.splunk_search_handle(s["name"]), source=source, dataset="saved_searches", kind="alert_def", path=source,
            locator=pointer, native=raw, t_event=t, t_known=t, native_id=s["name"], t_native=s["updated"],
            tz_rule=integ.tz_rule(source), status="disabled" if str(s.get("disabled")) == "1" else "enabled",
            title=s["name"], body=s.get("description"),
            attrs=clean({k: v for k, v in s.items() if k not in ("name", "description")}),
            flags={"dst_fold": True} if ambiguous else {})
