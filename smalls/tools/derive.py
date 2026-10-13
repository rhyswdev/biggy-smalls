"""Reading the store, and the values a tool derives at `now` (rule 7 of section 6.2 and K4 in
docs/decisions/tools-agent-build.md).

A tool reads only the views: `visible`, `visible_point`, `visible_chunk`, and the clockless `ref` rows.
Ingestion withholds every served field whose value is not true at `now`; the tool that shows a record
supplies the value derived at `now`, from here, computed from rows that are already visible. Nothing here
reads a withheld field.

| Value                        | Rule                                                                              |
|------------------------------|-----------------------------------------------------------------------------------|
| Monitor state                | The last visible transition per group; overall, the worst group                   |
| Paging service status        | critical if a valid incident is triggered; warning if one is acknowledged         |
| Thread summary               | Count, repliers and latest reply, from visible replies                            |
| Network-monitor alert active | Triggered before now, and not reset before now                                    |
| Problem evidence count       | The evidence items of the valid version that start before now                     |
| Metric envelope              | The end is the last visible point; the length is the count of visible points      |
| Channel list                 | The channels with a visible message                                               |
| Time coverage per source     | The first and last visible record time                                            |
"""

from __future__ import annotations

import json
from typing import Any

from smalls.tools.registry import ToolContext
from smalls.tools.windows import iso


def query(ctx: ToolContext, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
    """Run a read against the store's views and return dict rows."""
    cursor = ctx.store.conn.execute(sql, tuple(params))
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def one(ctx: ToolContext, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
    found = query(ctx, sql, params)
    return found[0] if found else None


def now_ms(ctx: ToolContext) -> int:
    return ctx.store.now


def loads(text: str | None) -> Any:
    return json.loads(text) if text else {}


def tags_of(row: dict[str, Any]) -> dict[str, list[str]]:
    """A row's tags as `{facet: [values]}`, whether the facet stores one value or several."""
    return {facet: value if isinstance(value, list) else [value] for facet, value in loads(row.get("tags")).items()}


def tag_label(tags: dict[str, list[str]], facets: tuple[str, ...]) -> str:
    """The one fixed form a row shows its tags in: ` [tag <facet>=<value>,<value>]` per facet, and nothing
    when it carries none. Only called with tags on."""
    return "".join(f" [tag {facet}={','.join(tags[facet])}]" for facet in facets if tags.get(facet))


def stamp(ms: int, native: str | None = None) -> str:
    """A time as UTC, with the native value beside it where it differs (rule 3 of section 6.2)."""
    utc = iso(ms)
    if not native:
        return utc
    same = native.replace(" ", "T").rstrip("Z").split("+")[0].rstrip("0").rstrip(".")
    if utc.rstrip("Z").rstrip("0").rstrip(".").startswith(same) and same:
        return utc
    return f"{utc} (native {native})"


# -- alert streams (K2 and K4) -----------------------------------------------------------------------------

FIRED = "fired"
ACTIVE, RESET = "active", "reset"
RECOVERY_SEV = 6        # a transition at informational severity is a recovery


def stream_state(stream: Any, now: int) -> None:
    """Fill in a stream's first seen, last change, state at `now`, transition count and active spans, from
    its visible records alone. Which rule applies is read from the shape of the records:

    - versions of one record (a problem, a page): the version valid at now gives the state, and it stays
      active until that version carries an end;
    - records that each carry a transition (a monitor's events): the last one gives the state, and a
      transition at informational severity is a recovery;
    - a record whose served state ingestion withheld (a network-monitor alert): active from its trigger
      until its reset, and a reset that has not happened by now is never mentioned;
    - anything else (a delivery of a search alert): no state at all; it fired.
    """
    records = stream.records
    head, last = records[0], records[-1]
    stream.is_page = head["kind"] == "incident"
    if head["valid_from"] is not None:
        ended = last["t_end"] if last["t_end"] is not None and last["t_end"] < now else None
        stream.first, stream.last = head["t_event"], last["t_known"]
        stream.state, stream.active = last["status"] or "unknown", ended is None
        stream.transitions = last["version"]
        stream.spans = [(head["t_event"], ended)]
    elif any(r["status"] for r in records):
        stream.first, stream.last = head["t_known"], last["t_known"]
        stream.state = last["status"] or "unknown"
        stream.transitions = len(records)
        start = None
        for r in records:
            recovering = r["sev"] is not None and r["sev"] >= RECOVERY_SEV
            if not recovering and start is None:
                start = r["t_known"]
            elif recovering and start is not None:
                stream.spans.append((start, r["t_known"]))
                start = None
        if start is not None:
            stream.spans.append((start, None))
        stream.active = start is not None
    elif any(r["withheld"] for r in records):
        reset = head["t_end"] if head["t_end"] is not None and head["t_end"] < now else None
        stream.first, stream.last = head["t_event"], reset or head["t_known"]
        stream.state, stream.active = (RESET if reset else ACTIVE), reset is None
        stream.transitions = 2 if reset else 1
        stream.spans = [(head["t_event"], reset)]
    else:
        stream.first, stream.last = head["t_known"], last["t_known"]
        stream.state, stream.active, stream.transitions = FIRED, True, 1
        stream.spans = [(r["t_known"], r["t_known"] + 1) for r in records]


def channels(ctx: ToolContext) -> list[str]:
    """The chat channels that have a visible message. The channel list file has no clock, so a channel
    created after `now` must not be listed from it."""
    return [r["dataset"] for r in query(ctx, "SELECT DISTINCT dataset FROM visible WHERE kind = 'message' ORDER BY dataset")]
