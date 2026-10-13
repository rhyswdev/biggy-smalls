"""What an adapter hands the store: records, metric points and reference files, plus the small helpers
every adapter shares (clocks, JSON spans, severity). Adapters parse one source each from its vendor-native
shape; nothing here knows any vendor.

Times are epoch milliseconds UTC, floored, as integers. Every adapter keeps the same pair beside them:
`t_native` is the text `t_known` was read from, exactly as written, and `tz_rule` names the zone and format
it was read with, from the served integration.yaml. Where a record also has an earlier event time (a
version of a ticket, a webhook delivery, an aggregated event), that time's own text stays in `attrs` or
`native`; `t_native` is always the known time's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator

UTC = timezone.utc
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)

# `sev` is a syslog severity number, lower is worse. It is set only where the source has a level of that
# kind (log levels, syslog words, Kubernetes event types, Datadog alert types, SolarWinds severities).
# Vendor priorities and categories are not mapped onto it: they stay in `sev_text` as the vendor wrote them.
SYSLOG = {"EMERG": 0, "PANIC": 0, "ALERT": 1, "CRIT": 2, "CRITICAL": 2, "FATAL": 2, "ERR": 3, "ERROR": 3,
          "WARN": 4, "WARNING": 4, "NOTICE": 5, "INFO": 6, "LOG": 6, "NORMAL": 6, "DEBUG": 7, "TRACE": 7}


def sev_of(word: str | None) -> int | None:
    return SYSLOG.get(word.upper()) if word else None


def ms_of(dt: datetime) -> int:
    """Epoch milliseconds of an aware datetime, floored (integer arithmetic, no float rounding)."""
    return (dt - _EPOCH) // _MS


def iso(ms: int) -> str:
    """Epoch milliseconds as the manifest writes times: 2026-10-13T14:31:07.221Z."""
    dt = _EPOCH + ms * _MS
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


@dataclass
class Record:
    """One row of `record`: one version of one vendor record. Field meanings are the DDL's
    (docs/decisions/ingestion-decisions.md section 3.1)."""

    handle: str
    source: str
    dataset: str
    kind: str
    path: str
    locator: int | str
    native: str
    t_event: int
    t_known: int
    version: int = 1
    native_id: str | None = None
    host: str | None = None
    entity_raw: str | None = None
    stream_key: str | None = None
    t_end: int | None = None
    valid_from: int | None = None
    valid_to: int | None = None
    t_native: str | None = None
    tz_rule: str | None = None
    sev: int | None = None
    sev_text: str | None = None
    status: str | None = None
    title: str | None = None
    body: str | None = None
    parent: str | None = None
    template_id: str | None = None
    keep_1_in: int | None = None
    sample_per: str | None = None
    attrs: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)
    # Knowledge prose only: (heading, text) chunks for the full-text table.
    chunks: list[tuple[str | None, str]] = field(default_factory=list)
    # Logs only: the masked line `template_id` hashes. It is what the tagger sees for a log record, and it
    # is not a column.
    template: str | None = None


@dataclass
class Point:
    """One row of `point`: one bucket of one metric series."""

    handle: str
    series: str
    t: int
    value: float | None
    interval_s: int
    t_known: int
    path: str
    locator: str


@dataclass
class Ref:
    """One row of `ref`: a served file (or envelope) that has no record rows of its own."""

    source: str
    name: str
    native: str
    flags: dict = field(default_factory=dict)


Item = Record | Point | Ref


def withhold(obj, paths: tuple[str, ...]):
    """A copy of a parsed JSON value without the fields at `paths`. A path is dotted keys; `[]` walks every
    element of a list (`series[].end`, `[].members`)."""

    def drop(node, parts: list[str]):
        if not parts:
            return
        head, rest = parts[0], parts[1:]
        if head == "[]":
            if isinstance(node, list):
                for item in node:
                    drop(item, rest)
        elif isinstance(node, dict) and head in node:
            if rest:
                drop(node[head], rest)
            else:
                del node[head]

    out = json.loads(json.dumps(obj))
    for path in paths:
        drop(out, [p for p in re.split(r"\.|(\[\])", path) if p])
    return out


def clean(d: dict) -> dict:
    """Drop the keys an attrs dict does not need to carry: None, empty strings and empty containers."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


# -- JSON spans: the exact served bytes of an element inside a document -----------------------------------

_DECODER = json.JSONDecoder()
_WS = re.compile(r"[ \t\n\r]*")


def _skip(text: str, i: int) -> int:
    return _WS.match(text, i).end()


def children(text: str, start: int) -> Iterator[tuple[str, int, int]]:
    """The direct children of the JSON container that starts at text[start], as (key or index, start, end)
    spans into `text`. This is how a record's `native` stays the bytes as served, indentation included."""
    opener = text[start]
    i = _skip(text, start + 1)
    n = 0
    while text[i] not in "]}":
        if opener == "{":
            key, i = json.decoder.scanstring(text, i + 1)
            i = _skip(text, i)
            i = _skip(text, i + 1)      # past the colon
        else:
            key = str(n)
        _, end = _DECODER.raw_decode(text, i)
        yield key, i, end
        n += 1
        i = _skip(text, end)
        if text[i] == ",":
            i = _skip(text, i + 1)


def span(text: str, pointer: str) -> tuple[int, int]:
    """The (start, end) span of the element a JSON pointer names. `/` is the whole document."""
    start = _skip(text, 0)
    end = _DECODER.raw_decode(text, start)[1]
    for part in [p for p in pointer.split("/") if p]:
        for key, s, e in children(text, start):
            if key == part:
                start, end = s, e
                break
        else:
            raise KeyError(f"{pointer}: no {part!r}")
    return start, end


def elements(text: str, pointer: str) -> Iterator[tuple[str, str, object]]:
    """Each element of the array at `pointer`, as (its own pointer, its served bytes, its parsed value)."""
    start, _ = span(text, pointer)
    base = pointer.rstrip("/")
    for key, s, e in children(text, start):
        raw = text[s:e]
        yield f"{base}/{key}", raw, json.loads(raw)


def versioned(records: list[Record]) -> list[Record]:
    """Number the versions of one record in the order given and bound each one's validity: `valid_from` is
    the version's own `t_known`, `valid_to` the next version's `valid_from`, the last one open. Every
    version keeps the first version's event time."""
    for n, rec in enumerate(records):
        rec.version = n + 1
        rec.valid_from = rec.t_known
        rec.t_event = records[0].t_event
    for rec, nxt in zip(records, records[1:]):
        rec.valid_to = nxt.valid_from
    return records


def version_number(name: str) -> int:
    """The version a file name carries: `INC0048811.v12.json` is 12."""
    return int(re.search(r"\.v(\d+)\.", name).group(1))

