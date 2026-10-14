"""`list_alerts`: one row per alert stream (PLAN section 4; section 6.4, K2 and K4 of
docs/decisions/tools-agent-build.md).

A stream is the records one alert source wrote about one thing: the transitions of one monitor group, the
versions of one problem or page, the deliveries of one search alert, one network-monitor alert. A stream
that flaps is still one row, with a count. First seen, last change, state and the count are computed over
the stream's whole visible history, whatever the window; the window only decides which streams are listed.

The row's handle is the handle of the stream's earliest visible record. The visible set only grows, so
the handle is the same at every checkpoint. That record's title and time are printed, so it is the row;
the stream's later records, its definition, a linked page and its saved search are pointers.

State at `now` is derived, never read from a served field (K4):
- a monitor stream takes its last visible transition;
- a problem or a page takes the version valid at now;
- a network-monitor alert is active from its trigger until its reset;
- a search alert has no state and is shown as `fired`.

A page is linked to a stream only when a served field of the page names the stream's own id. Nothing is
linked by a matching title or time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from smalls.tools import derive, errors, schemas
from smalls.tools.parts import Line, Parts
from smalls.tools.registry import ToolContext
from smalls.tools.windows import Window, iso, resolve

FACET = "signal"
MIN_ID_CHARS = 5        # an id shorter than this could match a page's key by accident
MAX_SILENT = 20         # how many definitions that have not fired the footer names
NO_NATIVE = "(no single native query: this merges the alert sources)"


@dataclass
class Stream:
    source: str
    key: str
    records: list[dict[str, Any]]
    state: str = ""
    active: bool = False
    first: int = 0
    last: int = 0
    transitions: int = 0
    spans: list[tuple[int, int | None]] = field(default_factory=list)     # when it was active; None is "still"
    definitions: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    is_page: bool = False

    @property
    def head(self) -> dict[str, Any]:
        return self.records[0]


def preview(args: Any, ctx: ToolContext) -> tuple[str, Window]:
    """What a call would echo, without running it (seam S7): no single native query, and the resolved window."""
    return NO_NATIVE, resolve(args.since, args.until, ctx.now)


def run(args: Any, ctx: ToolContext) -> Parts:
    window = resolve(args.since, args.until, ctx.now)
    tagged = ctx.tags and FACET in schemas.offered_facets()
    signal = getattr(args, FACET, None) if tagged else None
    streams, silent = _streams(ctx)
    sources = sorted({s.source for s in streams})
    if args.source is not None and args.source not in sources:
        raise errors.Refusal(errors.unknown_value("source", args.source, sources))
    matched = [s for s in streams if _in_window(s, window) and (args.source is None or s.source == args.source)
               and (args.state == "all" or s.active) and _has_text(s, args.text)]
    rows = [s for s in matched if _has_tag(s, signal)] if signal else matched
    footer = []
    if signal:
        untagged = sum(1 for s in matched if not _tags(s))
        footer.append(f"[tag filter {FACET}={signal}: {len(rows)} of the {len(matched)} streams that match without it. {untagged} of those "
                      f"{len(matched)} carry no {FACET} tag, and a missing tag says nothing about a stream]")
    if silent:
        named = " ".join(silent[:MAX_SILENT]) + (f" and {len(silent) - MAX_SILENT} more" if len(silent) > MAX_SILENT else "")
        footer.append(f"[{len(silent)} alert definitions have no visible transition and are not rows, since nothing says they have fired: {named}]")
    drop = [name for name, value in (("the tag filter", signal), ("text=", args.text), ("source=", args.source),
                                     ("state=active", args.state == "active")) if value]
    return Parts(rows=[_line(s, tagged) for s in rows], footer=footer, referenced=silent[:MAX_SILENT], window=window, now=ctx.store.now,
                 what="alert streams",
                 native_query=NO_NATIVE, narrow="Add source=, text= or state=active, or shorten the window.",
                 empty_hint=("Drop " + " or ".join(drop) + ", or widen the window.") if drop else "Widen the window.",
                 tag=f"{FACET}={signal}" if signal else None)


# -- building the streams ----------------------------------------------------------------------------------


def _streams(ctx: ToolContext) -> tuple[list[Stream], list[str]]:
    """Every alert stream over the whole visible history, oldest first, and the definitions that have none."""
    now = ctx.store.now
    records = derive.query(ctx, "SELECT rid, handle, source, dataset, kind, native_id, entity_raw, stream_key, t_event, t_end, t_known, "
                                "valid_from, version, t_native, sev, status, title, body, parent, tags, "
                                "json_extract(flags, '$.withheld') AS withheld FROM visible "
                                "WHERE kind = 'alert' OR (kind = 'incident' AND stream_key IS NOT NULL) ORDER BY t_known, rid")
    definitions = derive.query(ctx, "SELECT handle, source, native_id, stream_key FROM visible WHERE kind = 'alert_def'")
    grouped: dict[tuple[str, str], Stream] = {}
    for r in records:
        key = r["stream_key"] or r["handle"]
        grouped.setdefault((r["source"], key), Stream(r["source"], key, [])).records.append(r)
    streams = list(grouped.values())
    for s in streams:
        derive.stream_state(s, now)
        own = {r["parent"] for r in s.records if r["parent"]}
        base = s.key.split("|", 1)[0]
        own |= {d["handle"] for d in definitions if d["source"] == s.source and d["stream_key"] == base}
        s.definitions = sorted(own)
    pages = [s for s in streams if s.is_page]
    ids_of_definition = {d["handle"]: d["native_id"] for d in definitions}
    for s in streams:
        if s.is_page:
            continue
        ids = {ids_of_definition.get(h) for h in s.definitions} | {s.key.split("|", 1)[0]} | {r["native_id"] for r in s.records}
        group = s.key.split("|", 1)[1] if "|" in s.key else None
        for page in pages:
            if _names(page.key, ids) and (group is None or group in page.key):
                s.pages.append(page.head["handle"])
                page.definitions.append(s.head["handle"])      # the page is claimed: it is not a row of its own
    claimed = {h for s in streams for h in s.pages}
    fired = {h for s in streams for h in s.definitions}
    silent = sorted(d["handle"] for d in definitions if d["stream_key"] and d["handle"] not in fired)
    listed = [s for s in streams if not (s.is_page and s.head["handle"] in claimed)]
    return sorted(listed, key=lambda s: (s.first, s.head["rid"])), silent


def _names(key: str, ids: set[str | None]) -> bool:
    """True when a page's key holds one of a stream's ids as a whole token."""
    for candidate in ids:
        if candidate and len(candidate) >= MIN_ID_CHARS and re.search(rf"(?<![A-Za-z0-9]){re.escape(candidate)}(?![A-Za-z0-9])", key):
            return True
    return False


# -- filters -----------------------------------------------------------------------------------------------


def _in_window(s: Stream, window: Window) -> bool:
    """Listed when one of its records falls in the window, or it is active at any point in it."""
    if any(window.since <= r["t_known"] < window.until for r in s.records):
        return True
    return any(start < window.until and (end is None or end > window.since) for start, end in s.spans)


def _has_text(s: Stream, text: str | None) -> bool:
    if not text:
        return True
    needle = text.lower()
    return any(needle in f"{r['title'] or ''}\n{r['body'] or ''}".lower() for r in s.records)


def _tags(s: Stream) -> list[str]:
    """The distinct tags of the stream's records, in the order they were first seen."""
    return list(dict.fromkeys(value for r in s.records for value in derive.tags_of(r).get(FACET, [])))


def _has_tag(s: Stream, value: str) -> bool:
    values = _tags(s)
    return not values if value == schemas.UNTAGGED else value in values


# -- the row -----------------------------------------------------------------------------------------------


def _line(s: Stream, tagged: bool) -> Line:
    head = s.head
    later = [r["handle"] for r in s.records[1:] if r["handle"] != head["handle"]]
    cells = [head["handle"], s.source, s.key, head["title"] or "(no title)", f"entity {head['entity_raw'] or '(none)'}",
             f"first {derive.stamp(s.first, head['t_native'] if s.first == head['t_known'] else None)}", f"last {iso(s.last)}",
             f"state {s.state}", f"transitions {s.transitions}"]
    deliveries = len(s.records) if s.state == derive.FIRED else 0
    if deliveries:
        cells.append(f"deliveries {deliveries}")
    cells += [f"paged {' '.join(s.pages)}"] if s.pages else []
    cells += [f"definition {' '.join(s.definitions)}"] if s.definitions and not s.is_page else []
    cells += [f"later {' '.join(later)}"] if later else []
    text = "  ".join(cells) + (derive.tag_label({FACET: _tags(s)}, (FACET,)) if tagged else "")
    refs = tuple(dict.fromkeys([*later, *(s.definitions if not s.is_page else []), *s.pages]))
    return Line(head["handle"], text, head["t_known"], refs=refs)
