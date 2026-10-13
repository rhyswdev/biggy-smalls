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


def channels(ctx: ToolContext) -> list[str]:
    """The chat channels that have a visible message. The channel list file has no clock, so a channel
    created after `now` must not be listed from it."""
    return [r["dataset"] for r in query(ctx, "SELECT DISTINCT dataset FROM visible WHERE kind = 'message' ORDER BY dataset")]
