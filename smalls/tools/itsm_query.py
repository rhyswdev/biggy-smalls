"""`itsm_query`: the service desk's tables, as they stood at `now` (PLAN section 4; section 6.4 and K1 of
docs/decisions/tools-agent-build.md).

`table` is `incident`, `journal`, `user` or `group`. A filter is equality on a field's display value,
without regard to case, and filters are ANDed. `text` is a substring of the row's title and body. Rows
are newest first. An incident that is still open is the version valid at `now`: nothing is rewound and
nothing later is shown.

Every row prints what identifies it whatever `fields` asks for: an incident its number, short description
and state; a journal entry its incident, its kind, its author and its text; a user or a group its name and
whether it is active. `fields` adds named fields to that, in the order given. With no `fields`, a short
default set is printed. A time written on the desk's wall clock is shown as UTC with the native value
beside it.

A journal entry's text is shortened past 300 characters and says so; `get_record` returns all of it. An
incident's journal entries are referenced from its row. A journal entry can be filtered by the `number`
of its incident: the link is the one the desk itself records.
"""

from __future__ import annotations

import json
import re
from typing import Any

from smalls.tools import derive, errors, schemas
from smalls.tools.parts import Line, Parts
from smalls.tools.registry import ToolContext
from smalls.tools.store import render_native

FACET = "signal"
TEXT_CHARS = 300
WALL_CLOCK = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
FIELD = re.compile(r"^[A-Za-z0-9_.]{1,64}$")
# table -> (the desk's own table name, the field newest-first orders by, the fields printed when none is asked for)
TABLES = {
    "incident": ("incident", "opened_at", ("priority", "cmdb_ci", "business_service", "assignment_group", "assigned_to", "opened_at", "resolved_at",
                                           "close_code", "sys_id")),
    "journal": ("sys_journal_field", "sys_created_on", ()),
    "user": ("sys_user", "sys_created_on", ("user_name", "title", "department", "email", "manager", "time_zone")),
    "group": ("sys_user_group", "sys_created_on", ("manager", "email", "type", "description")),
}
# Fields the store keeps in a column of their own, by table and by the name the desk gives them.
COLUMNS = {"incident": {"number": "native_id", "short_description": "title"}, "journal": {"value": "body"}, "user": {"name": "title"},
           "group": {"name": "title"}}
LINKED = {"journal": ("number",)}       # a journal entry's `number` is its incident's
SHORTENED = "[text ending in […+N chars] is shortened: get_record <handle> returns the whole entry]"
TAG_ONLY_INCIDENTS = "signal applies to table=incident only"


def preview(args: Any, ctx: ToolContext) -> tuple[str, None]:
    """The table request a call would echo, without running it (seam S7). It has no window."""
    return _native(args), None


def _native(args: Any) -> str:
    dataset, order_field, defaults = TABLES[args.table]
    names = [name for name in (args.fields or defaults) if FIELD.match(name)]
    query = "^".join([*(f"{pair.name}={pair.value}" for pair in args.filters), *([f"123TEXTQUERY321={args.text}"] if args.text else []),
                      f"ORDERBYDESC{order_field}"])
    native = f"GET /api/now/table/{dataset}?sysparm_query={query}&sysparm_display_value=true&sysparm_limit={args.limit}"
    if args.fields:
        native += "&sysparm_fields=" + ",".join(names)
    return native


def run(args: Any, ctx: ToolContext) -> Parts:
    dataset, order_field, defaults = TABLES[args.table]
    tagged = ctx.tags and FACET in schemas.offered_facets()
    signal = getattr(args, FACET, None) if tagged else None
    if signal and args.table != "incident":
        raise errors.Refusal(errors.bad_arguments("itsm_query", TAG_ONLY_INCIDENTS))
    rows = derive.query(ctx, "SELECT v.rid, v.handle, v.source, v.kind, v.native_id, v.title, v.body, v.status, v.parent, v.t_event, v.t_known, v.attrs, "
                             "v.native, v.flags, v.tags, p.native_id AS parent_number FROM visible v LEFT JOIN visible p ON p.handle = v.parent "
                             "WHERE v.dataset = ? ORDER BY v.t_event DESC, v.rid DESC", (dataset,))
    for row in rows:
        row["attrs"] = derive.loads(row["attrs"])
    columns = COLUMNS[args.table]
    known = sorted({name for row in rows for name in row["attrs"]} | set(columns) | set(LINKED.get(args.table, ())))
    for pair in args.filters:
        if not FIELD.match(pair.name) or (rows and pair.name not in known and all(_value(row, pair.name, columns) is None for row in rows)):
            raise errors.Refusal(errors.unknown_value("filters.name", pair.name, known))
    matched = [row for row in rows if all(_same(_value(row, pair.name, columns), pair.value) for pair in args.filters)
               and (not args.text or args.text.lower() in f"{row['title'] or ''}\n{row['body'] or ''}".lower())]
    listed = [row for row in matched if _has_tag(row, signal)] if signal else matched
    shown, hidden = listed[: args.limit], listed[args.limit:]
    children: dict[str, list[str]] = {}
    if args.table == "incident":
        for child in derive.query(ctx, "SELECT handle, parent FROM visible WHERE kind = 'note' AND parent IS NOT NULL ORDER BY t_known, rid"):
            children.setdefault(child["parent"], []).append(child["handle"])
    names = [name for name in (args.fields or defaults) if FIELD.match(name)]
    lines = [_line(ctx, args.table, row, names, children.get(row["handle"], []), tagged, asked=bool(args.fields)) for row in shown]
    footer = []
    if signal:
        untagged = sum(1 for row in matched if not derive.tags_of(row).get(FACET))
        footer.append(f"[tag filter {FACET}={signal}: {len(listed)} of the {len(matched)} incidents that match without it. {untagged} of those "
                      f"{len(matched)} carry no {FACET} tag, and a missing tag says nothing about an incident]")
    if any("[…+" in line.text for line in lines):
        footer.append(SHORTENED)
    drop = [what for what, value in (("the tag filter", signal), ("a filter", args.filters), ("text=", args.text)) if value]
    native = _native(args)
    return Parts(rows=lines, footer=footer, total=len(listed), what=f"{args.table} rows", keep="newest_first", native_label="sysparm_query",
                 native_query=native, hidden_span=(min(r["t_event"] for r in hidden), max(r["t_event"] for r in hidden)) if hidden else None,
                 narrow="Add a filter or text=.", tag=f"{FACET}={signal}" if signal else None,
                 empty_hint=("Drop " + " or ".join(drop) + ".") if drop else f"No visible row in table={args.table}.")


def _value(row: dict[str, Any], name: str, columns: dict[str, str]) -> str | None:
    """A field's display value: the parsed field, or the column that holds it, or the served record's own."""
    if name == "number" and row["kind"] == "note":
        return row["parent_number"]
    value = row["attrs"].get(name)
    if value in (None, "") and name in columns:
        value = row[columns[name]]
    if value in (None, "") and name == "state":
        value = row["status"]
    if value in (None, ""):
        try:
            served = json.loads(render_native(row["native"], row["flags"]))
        except ValueError:
            return None
        served = served.get("result", served) if isinstance(served, dict) else {}
        value = served.get(name) if isinstance(served, dict) else None
        if isinstance(value, dict):
            value = value.get("display_value", value.get("value"))
    return None if value in (None, "") else str(value)


def _same(value: str | None, wanted: str) -> bool:
    return value is not None and value.strip().lower() == wanted.strip().lower()


def _has_tag(row: dict[str, Any], value: str) -> bool:
    tags = derive.tags_of(row).get(FACET, [])
    return not tags if value == schemas.UNTAGGED else value in tags


def _shown(ctx: ToolContext, row: dict[str, Any], name: str, columns: dict[str, str] | None = None) -> str | None:
    """`name value`, with a wall-clock time as UTC and its native value beside it; None for an empty field."""
    value = _value(row, name, columns or {})
    if value is None:
        return None
    if WALL_CLOCK.match(value):
        return f"{name} {derive.stamp(ctx.store.integration.parse(row['source'], value)[0], value)}"
    return f"{name} {value}"


def _line(ctx: ToolContext, table: str, row: dict[str, Any], names: list[str], notes: list[str], tagged: bool, *, asked: bool) -> Line:
    """One row. A field that was asked for by name is printed even when it is empty; a default one is not."""
    if table == "incident":
        cells = [row["handle"], row["native_id"] or "(no number)", row["title"] or "(no short description)", f"state {row['status'] or '(none)'}"]
    elif table == "journal":
        cells = [row["handle"], f"{row['attrs'].get('element') or 'entry'} on {row['parent'] or '(no record)'}",
                 f"by {row['attrs'].get('sys_created_by') or '(nobody named)'}", _shown(ctx, row, "sys_created_on") or "sys_created_on (empty)"]
    else:
        cells = [row["handle"], row["title"] or "(no name)", row["status"] or "(no active flag)"]
    for name in names:
        cell = _shown(ctx, row, name, COLUMNS[table])
        if cell or asked:
            cells.append(cell or f"{name} (empty)")
    if table == "incident" and notes:
        cells.append(f"journal {' '.join(notes)}")
    text = "  ".join(cells)
    if table == "journal":
        body = " ".join((row["body"] or "").split())
        text += "\n    " + (body if len(body) <= TEXT_CHARS else f"{body[:TEXT_CHARS]} […+{len(body) - TEXT_CHARS} chars]")
    if tagged and table == "incident":
        text += derive.tag_label(derive.tags_of(row), (FACET,))
    return Line(row["handle"], text, row["t_known"], refs=tuple(notes))
