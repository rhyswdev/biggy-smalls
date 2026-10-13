"""`list_changes`: pipeline builds and change requests, side by side (PLAN section 4; section 6.4 and K6 of
docs/decisions/tools-agent-build.md).

Rows are ordered by start, oldest first, and are never joined or ranked: whether a build belongs to a
change request, and which change matters, is for the reader to work out from what each row states.

A change or a build is listed when its span, from its start to its end or to `now`, overlaps the window.
A visible change that has not started is listed too, marked `planned`, when `until` is left out: its start
is a scheduled time, known in advance. With an explicit `until`, the footer counts the planned ones.

A build is the version valid at `now`: one that is still running shows no result and no duration. A change
request shows its served state; there is no rewind.
"""

from __future__ import annotations

from typing import Any

from smalls.tools import derive, schemas
from smalls.tools.parts import Line, Parts
from smalls.tools.registry import ToolContext
from smalls.tools.windows import Window, iso, resolve

FACET = "change_kind"
NO_NATIVE = "(no single native query: builds and change requests are listed side by side)"
PLANNED = "planned"


def run(args: Any, ctx: ToolContext) -> Parts:
    window = resolve(args.since, args.until, ctx.now)
    now = ctx.store.now
    tagged = ctx.tags and FACET in schemas.offered_facets()
    kind = getattr(args, FACET, None) if tagged else None
    items = [_item(ctx, r) for r in derive.query(
        ctx, "SELECT rid, handle, source, kind, native_id, entity_raw, t_event, t_end, t_known, status, title, body, attrs, tags "
             "FROM visible WHERE kind IN ('change', 'build') ORDER BY t_event, rid")]
    commits = derive.query(ctx, "SELECT handle, parent FROM visible WHERE kind = 'commit' ORDER BY rid")
    planned = [i for i in items if i["start"] >= now and _has_text(i, args.text)]
    started = [i for i in items if i["start"] < now and i["start"] < window.until and (i["end"] or now) > window.since]
    matched = [i for i in [*started, *(planned if args.until is None else [])] if _has_text(i, args.text)]
    rows = [i for i in matched if _has_tag(i, kind)] if kind else matched
    footer = []
    if kind:
        untagged = sum(1 for i in matched if not i["tags"])
        footer.append(f"[tag filter {FACET}={kind}: {len(rows)} of the {len(matched)} changes that match without it. {untagged} of those "
                      f"{len(matched)} carry no {FACET} tag, and a missing tag says nothing about a change]")
    if args.until is not None and planned:
        footer.append(f"[{len(planned)} {PLANNED} changes start after now and are not listed: leave until out to list them]")
    children: dict[str, list[str]] = {}
    for c in commits:
        children.setdefault(c["parent"], []).append(c["handle"])
    drop = [name for name, value in (("the tag filter", kind), ("text=", args.text)) if value]
    return Parts(rows=[_line(i, children.get(i["handle"], []), tagged) for i in rows], footer=footer, window=window, now=now,
                 what="changes", native_query=NO_NATIVE, narrow="Add text= or shorten the window.",
                 empty_hint=("Drop " + " or ".join(drop) + ", or widen the window.") if drop else "Widen the window.",
                 tag=f"{FACET}={kind}" if kind else None)


def _item(ctx: ToolContext, row: dict[str, Any]) -> dict[str, Any]:
    """One change or build with its times in UTC. A change request's times are written on the source's
    wall clock, so each is converted through the served integration settings."""
    attrs = derive.loads(row["attrs"])
    item = {**row, "attrs": attrs, "tags": derive.tags_of(row).get(FACET, []), "start": row["t_event"], "end": row["t_end"]}
    if row["kind"] == "change":
        def when(name: str) -> tuple[int | None, str | None]:
            text = attrs.get(name)
            return (ctx.store.integration.parse(row["source"], text)[0], text) if text else (None, None)

        item["actual_start"], item["actual_end"] = when("work_start"), when("work_end")
        item["planned_start"], item["planned_end"] = when("start_date"), when("end_date")
    return item


def _has_text(item: dict[str, Any], text: str | None) -> bool:
    return not text or text.lower() in f"{item['title'] or ''}\n{item['body'] or ''}".lower()


def _has_tag(item: dict[str, Any], value: str) -> bool:
    return not item["tags"] if value == schemas.UNTAGGED else value in item["tags"]


def _line(item: dict[str, Any], commits: list[str], tagged: bool) -> Line:
    attrs = item["attrs"]
    if item["kind"] == "build":
        cells = [item["handle"], "build", item["native_id"], f"job {attrs.get('job')}", item["title"] or "(no title)"]
        cells += [f"{name} {value}" for name, value in (attrs.get("parameters") or {}).items()]
        cells += [f"start {iso(item['start'])}", f"end {iso(item['end'])}" if item["end"] else "end (still running)",
                  f"state {item['status'] or 'unknown'}", f"requester {attrs.get('started_by') or '(none)'}"]
        cells += [f"commits {' '.join(commits)}"] if commits else []
    else:
        cells = [item["handle"], "change", item["native_id"], f"ci {item['entity_raw'] or '(none)'}", item["title"] or "(no title)"]
        cells += [f"type {attrs['type']}"] if attrs.get("type") else []
        cells += [_when("start", item["actual_start"], item["planned_start"]), _when("end", item["actual_end"], item["planned_end"]),
                  f"state {item['status'] or 'unknown'}", f"requester {attrs.get('requested_by') or '(none)'}"]
    text = "  ".join(cells) + (derive.tag_label({FACET: item["tags"]}, (FACET,)) if tagged else "")
    return Line(item["handle"], text, item["t_known"], refs=tuple(commits))


def _when(label: str, actual: tuple[int | None, str | None], planned: tuple[int | None, str | None]) -> str:
    """An actual time when the work has one, else the planned time, said to be planned."""
    if actual[0] is not None:
        return f"{label} {derive.stamp(*actual)}"
    if planned[0] is not None:
        return f"{PLANNED} {label} {derive.stamp(*planned)}"
    return f"{label} (none)"
