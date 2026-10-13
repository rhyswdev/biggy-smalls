"""`get_record`: any handle, as the whole native record stood at `now` (PLAN section 4; section 6.4, K4 and
K8 of docs/decisions/tools-agent-build.md; ruling G7).

This is the call that returns in full whatever another tool showed shortened. No field is clipped: a
record that renders larger than the cap is split at line ends into parts, the result says "part 1 of n",
and `part` asks for another. The only things left out for good are empty fields, which are counted, and
the fields ingestion withheld because their served value is not true at `now`. Where such a value can be
derived at `now` from visible rows, it is printed, and said to be derived.

The record is the version valid at `now`. A handle that is unknown, not yet visible or absent gets one
answer, so the three cannot be told apart.

Children are pointers, read from the links ingestion resolved inside one source: an incident's journal
entries and audit rows, a build's commits, a thread's replies, a definition's alerts. A saved search
returns its definition and the visible alerts it fired, and is never run.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from smalls.contracts.envelope import Envelope, Row
from smalls.tools import derive, errors, schemas
from smalls.tools.parts import CHARS_PER_TOKEN
from smalls.tools.registry import CAPS, ToolContext
from smalls.tools.windows import iso

CAP_TOKENS = CAPS["get_record"][0]
RESERVE_CHARS = 400          # room in every part for its first line and its footer
INLINE_LIST_CHARS = 160
EMPTY = ("", None, [], {})
# Fields that hold a time known in advance, by source and dataset. Each is printed with the word planned.
SCHEDULED = {("servicenow", "change_request"): ("start_date", "end_date"), ("pagerduty", "oncalls"): ("end",)}
FACETS = ("signal", "act", "change_kind")


def run(args: Any, ctx: ToolContext) -> Envelope:
    handle = args.handle.strip()
    refusal = errors.for_handle(handle)
    if refusal:
        return refusal
    record = derive.one(ctx, "SELECT * FROM visible WHERE handle = ?", (handle,))
    if record is None:
        return errors.not_in_bundle(handle)
    head, body, children = render(ctx, record)
    pieces = split(body, CAP_TOKENS * CHARS_PER_TOKEN - RESERVE_CHARS - len(head))
    if args.part > len(pieces):
        return errors.error(errors.PART_OUT_OF_RANGE.format(handle=handle, parts=len(pieces), s="" if len(pieces) == 1 else "s", part=args.part),
                            "part_out_of_range")
    lines = [head if len(pieces) == 1 else f"{head}  part {args.part} of {len(pieces)}", *pieces[args.part - 1]]
    if args.part < len(pieces):
        lines.append(f"[part {args.part} of {len(pieces)}: get_record with part={args.part + 1} returns the next; nothing is left out]")
    text = "\n".join(lines)
    return Envelope(text, rows=[Row(handle, text, iso(record["t_known"]) if record["t_known"] else None)],
                    referenced=[h for h in children if h != handle], meta={"total": len(pieces), "returned": 1})


# -- rendering ---------------------------------------------------------------------------------------------


def render(ctx: ToolContext, record: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """A record as (its first line, its body lines, the handles it points at)."""
    now = ctx.store.now
    flags = derive.loads(record["flags"])
    head = f"{record['handle']}  {record['kind']} from {record['source']}/{record['dataset']}, as it stood at {iso(now)}"
    if record["t_known"]:
        head += f"; known {derive.stamp(record['t_known'], record['t_native'])}"
    if record["valid_from"] is not None:
        head += f"; version {record['version']}"
    if ctx.tags:
        head += derive.tag_label({f: v for f, v in derive.tags_of(record).items() if f in schemas.offered_facets()}, FACETS)
    body = [*_derived(ctx, record, flags)]
    native = derive.render_native(record["native"], record["flags"])
    try:
        parsed = json.loads(native)
    except ValueError:
        parsed = None
    hidden = 0
    if isinstance(parsed, (dict, list)):
        parsed = _as_of_now(parsed, now)
        scheduled = SCHEDULED.get((record["source"], record["dataset"]), ())
        counter = [0]
        for path, value in flatten(parsed, "", counter):
            suffix = " (planned)" if path.rsplit(".", 1)[-1] in scheduled else ""
            first, *rest = value.split("\n")
            body.append(f"{path}: {first}{suffix}")
            body += [f"    {line}" for line in rest]
        hidden = counter[0]
    else:
        body += native.split("\n")
    pointers, children = _pointers(ctx, record)
    body += pointers
    if hidden:
        body.append(f"[{hidden} empty fields hidden]")
    if flags.get("withheld"):
        body.append(f"[{len(flags['withheld'])} served fields are not shown because their value was not true at now]")
    if flags.get("truncated"):
        body.append("[this record was cut short at the source]")
    return head, body, children


def flatten(value: Any, path: str, hidden: list[int]) -> Iterator[tuple[str, str]]:
    """A parsed record as `path: value` lines. Empty fields are left out and counted in `hidden[0]`."""
    if isinstance(value, dict):
        for key, item in value.items():
            yield from flatten(item, f"{path}.{key}" if path else str(key), hidden)
    elif isinstance(value, list):
        scalars = [v for v in value if not isinstance(v, (dict, list))]
        if not value:
            hidden[0] += 1
        elif len(scalars) == len(value) and sum(len(str(v)) for v in value) <= INLINE_LIST_CHARS and not any("\n" in str(v) for v in value):
            yield path, ", ".join(_scalar(v) for v in value)
        else:
            for i, item in enumerate(value):
                yield from flatten(item, f"{path}[{i}]", hidden)
    elif value in EMPTY:
        hidden[0] += 1
    else:
        yield path or "value", _scalar(value)


def _scalar(value: Any) -> str:
    return json.dumps(value) if isinstance(value, bool) or value is None else str(value)


def split(lines: list[str], budget_chars: int) -> list[list[str]]:
    """Body lines as parts that each fit the budget, split at line ends. A single line longer than a part
    is itself split, so nothing is dropped."""
    budget_chars = max(budget_chars, 200)
    pieces: list[list[str]] = [[]]
    used = 0
    for line in lines:
        chunks = [line[i:i + budget_chars] for i in range(0, len(line), budget_chars)] or [""]
        for chunk in chunks:
            if pieces[-1] and used + len(chunk) + 1 > budget_chars:
                pieces.append([])
                used = 0
            pieces[-1].append(chunk)
            used += len(chunk) + 1
    return pieces


# -- values derived at now (K4) ----------------------------------------------------------------------------


def _as_of_now(parsed: Any, now: int) -> Any:
    """A problem's evidence, as it stood at now: the items that had started, and their count. The served
    count is the count at export time."""
    details = parsed.get("evidenceDetails") if isinstance(parsed, dict) else None
    if isinstance(details, dict) and isinstance(details.get("details"), list):
        started = [e for e in details["details"] if not isinstance(e.get("startTime"), (int, float)) or e["startTime"] < now]
        parsed = {**parsed, "evidenceDetails": {**details, "totalCount": len(started), "details": started}}
    return parsed


def _derived(ctx: ToolContext, record: dict[str, Any], flags: dict[str, Any]) -> list[str]:
    """The lines that say what was derived at `now` from visible rows, in place of a withheld value."""
    now = ctx.store.now
    withheld = flags.get("withheld") or []
    if record["kind"] == "alert_def" and withheld:
        groups = derive.monitor_groups(ctx, record["handle"])
        if not groups:
            return ["derived at now: no transition seen, so no state is claimed"]
        worst = min(groups, key=lambda g: (g["sev"] if g["sev"] is not None else 99, g["key"]))
        lines = [f"derived at now: state {worst['state']} (the worst of {len(groups)} group{'s' if len(groups) != 1 else ''}, each from its last visible transition)"]
        return lines + [f"derived at now: group {g['key']}: {g['state']} since {iso(g['last'])}, {g['transitions']} transitions" for g in groups]
    if record["kind"] == "config" and withheld:
        status, why = derive.service_status(ctx, record["native_id"])
        return [f"derived at now: status {status} ({why})"]
    if record["kind"] == "alert" and withheld and record["status"] is None:
        reset = record["t_end"] is not None and record["t_end"] < now
        return [f"derived at now: state {'reset at ' + iso(record['t_end']) if reset else 'active'}"]
    if record["kind"] == "message" and record["parent"] is None:
        replies = derive.query(ctx, "SELECT handle, t_known, attrs FROM visible WHERE parent = ? ORDER BY t_known, rid", (record["handle"],))
        if replies:
            summary = derive.thread_summary(replies, derive.chat_names(ctx))
            return [f"derived at now: thread has {summary['count']} replies by {', '.join(summary['repliers'])}; latest {iso(summary['latest'])}"]
    return []


def _pointers(ctx: ToolContext, record: dict[str, Any]) -> tuple[list[str], list[str]]:
    """The lines that list a record's parent and children, and the handles on them."""
    lines, handles = [], []
    if record["parent"]:
        lines.append(f"parent: {record['parent']}")
        handles.append(record["parent"])
    children = derive.query(ctx, "SELECT handle, kind, t_known, stream_key FROM visible WHERE parent = ? ORDER BY t_known, rid", (record["handle"],))
    if record["kind"] == "alert_def" and record["stream_key"] is None:      # a saved search: what it fired, by search id
        by_sid: dict[str, list[dict[str, Any]]] = {}
        for child in children:
            by_sid.setdefault(child["stream_key"] or child["handle"], []).append(child)
        for sid, deliveries in by_sid.items():
            lines.append(f"fired {sid}: " + ", ".join(f"{d['handle']} received {iso(d['t_known'])}" for d in deliveries))
    else:
        by_kind: dict[str, list[str]] = {}
        for child in children:
            by_kind.setdefault(child["kind"], []).append(child["handle"])
        lines += [f"children ({kind}): {' '.join(found)}" for kind, found in by_kind.items()]
    handles += [c["handle"] for c in children]
    if record["kind"] == "alert_def" and record["stream_key"]:
        related = [r["handle"] for r in derive.query(ctx, "SELECT handle FROM visible WHERE kind = 'alert_def' AND stream_key = ? AND handle != ? "
                                                          "ORDER BY rid", (record["stream_key"], record["handle"]))]
        if related:
            lines.append(f"related definitions: {' '.join(related)}")
            handles += related
    return lines, handles
