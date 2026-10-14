"""`search_logs`: raw logs, as rows or as a count table (PLAN section 4; section 6.4 and K5 of
docs/decisions/tools-agent-build.md).

Rows mode prints the total, then one row per record, oldest first: handle, UTC time, host, sourcetype and
the record's first line, with a stack trace folded to three frames. A row that was shortened is marked, and
`get_record` on its handle returns the whole record.

Stats mode prints a count table by minute, host, level, template or, with tags on, signal. It is one
citable row whose handle is minted for this call. A count that matches nothing is still a table with its
handle, so an absence can be cited for exactly those terms, filters and window. A count that depends on a
tag is not citable, because a tag is never evidence, and when it matches nothing it is the empty sentence.

Every count from a sampled source carries its sampling rate and an estimate: the sum of each matched
record's own rate. Only the rate and its unit are ever shown.

Terms are ANDed, case-insensitive substrings of the record as served and of its body. Nothing semantic
happens here.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from smalls.contracts.handles import query_handle
from smalls.tools import derive, errors, schemas
from smalls.tools.ingest import masker
from smalls.tools.parts import Line, Parts
from smalls.tools.registry import ToolContext
from smalls.tools.windows import Window, clock, iso, resolve

LINE_CHARS = 300
TRACE_FRAMES = 3
TOP_GROUPS = 20
MAX_BUCKETS = 60
BUCKETS = (("1m", 60_000), ("5m", 300_000), ("15m", 900_000), ("1h", 3_600_000))
FACET = "signal"
SHORTENED = "[a row ending in […] is shortened, and a structured record shows its level, logger and message: get_record <handle> returns the whole record]"
NOT_CITABLE = "[not citable: this count depends on a tag, and a tag is a search aid, never evidence. Cite the records themselves.]"
MULTI_TAG = "[a record that carries two values is counted under each, so these counts can add up to more than the {n} records matched]"
NO_NATIVE_TAG = " (the tag filter has no native equivalent and is not in this query)"
NO_GROUPS = "(none: no record matched)"
ZERO = ("[no record matched these terms and filters in the window. A misspelt term also counts 0, so check the terms "
        "before reading this as an absence. To look wider: {hint}]")
ZERO_SAMPLED = "[the sources in scope keep {rate}, so a rare record may not have been kept]"


def run(args: Any, ctx: ToolContext) -> Parts:
    window = resolve(args.since, args.until, ctx.now)
    tagged = ctx.tags and FACET in schemas.offered_facets()
    signal = getattr(args, FACET, None) if tagged else None
    _check_values(args, ctx)
    matched = _match(args, ctx, window)
    rows = [r for r in matched if _has_tag(r, signal)] if signal else matched
    spl = _spl(args, ctx, window)
    footer = [_tag_footer(matched, rows, signal)] if signal else []
    native = spl + (NO_NATIVE_TAG if signal else "")
    common = dict(window=window, now=ctx.store.now, native_label="SPL", tag=f"{FACET}={signal}" if signal else None)
    if args.group_by:
        return _stats(args, ctx, window, rows, footer, native, common, signal)
    shown = rows[: args.limit]
    lines = [_line(r, args.terms, tagged) for r in shown]
    header = [f"{len(rows)} log records{_sampled(rows)}, {iso(window.since)} to {iso(window.until)}"] if rows else []
    if any("[…" in line.text or r["title"] != r["native"].split("\n", 1)[0] for line, r in zip(lines, shown)):
        footer = [SHORTENED, *footer]
    rest = rows[args.limit:]
    return Parts(rows=lines, header=header, footer=footer, native_query=native, total=len(rows), what="records",
                 hidden_span=(rest[0]["t_known"], rest[-1]["t_known"]) if rest else None,
                 narrow="Add a term, a host or a sourcetype, shorten the window, or count with group_by.",
                 empty_hint=_empty_hint(args, signal), sampled=_is_sampled(rows), **common)


# -- matching ----------------------------------------------------------------------------------------------


def _check_values(args: Any, ctx: ToolContext) -> None:
    """A sourcetype or a host is checked against what is visible, and an unknown one answers with the values
    that exist."""
    for field, column in (("sourcetype", "json_extract(attrs, '$.sourcetype')"), ("host", "host")):
        value = getattr(args, field)
        if value is None:
            continue
        known = [r["v"] for r in derive.query(ctx, f"SELECT DISTINCT {column} AS v FROM visible WHERE kind = 'log' AND {column} IS NOT NULL ORDER BY 1")]
        if value not in known:
            raise errors.Refusal(errors.unknown_value(field, value, known))


def _match(args: Any, ctx: ToolContext, window: Window) -> list[dict[str, Any]]:
    """Every visible log record in the window that holds every term, oldest first. A record that covers a
    span of time (an event counted over several occurrences) is in the window when its span overlaps it."""
    where, params = ["kind = 'log'", "t_event < ?", "t_known >= ?"], [window.until, window.since]
    if args.sourcetype is not None:
        where.append("json_extract(attrs, '$.sourcetype') = ?")
        params.append(args.sourcetype)
    if args.host is not None:
        where.append("host = ?")
        params.append(args.host)
    for term in args.terms:
        where.append("(instr(lower(native), ?) > 0 OR instr(lower(coalesce(body, '')), ?) > 0)")
        params += [term.lower(), term.lower()]
    return derive.query(ctx, "SELECT handle, host, t_known, title, body, native, sev_text, template_id, keep_1_in, sample_per, tags, flags, "
                             "json_extract(attrs, '$.sourcetype') AS sourcetype, json_extract(attrs, '$.index') AS idx "
                             f"FROM visible WHERE {' AND '.join(where)} ORDER BY t_known, rid", params)


def _has_tag(row: dict[str, Any], value: str) -> bool:
    values = derive.tags_of(row).get(FACET, [])
    return not values if value == schemas.UNTAGGED else value in values


def _tag_footer(matched: list[dict[str, Any]], rows: list[dict[str, Any]], value: str) -> str:
    untagged = sum(1 for r in matched if not derive.tags_of(r).get(FACET))
    return (f"[tag filter {FACET}={value}: {len(rows)} of the {len(matched)} records that match without it. {untagged} of those "
            f"{len(matched)} carry no {FACET} tag, and a missing tag says nothing about a record]")


def _empty_hint(args: Any, signal: str | None) -> str:
    drop = [name for name, value in (("the tag filter", signal), ("a term", args.terms), ("host=", args.host), ("sourcetype=", args.sourcetype)) if value]
    return ("Drop " + " or ".join(drop) + ", or widen the window.") if drop else "Widen the window."


# -- rows --------------------------------------------------------------------------------------------------


def _line(row: dict[str, Any], terms: list[str], tagged: bool) -> Line:
    first, *rest = (row["body"] or row["title"] or "").split("\n")
    head = row["title"] or first
    text = head if len(head) <= LINE_CHARS else f"{head[:LINE_CHARS]} […+{len(head) - LINE_CHARS} chars]"
    shown_text = "\n".join([head[:LINE_CHARS], *rest[:TRACE_FRAMES]]).lower()
    if any(term.lower() not in shown_text for term in terms):
        text += " [matched in the full record]"
    if derive.loads(row["flags"]).get("truncated"):
        text += " [cut short at the source]"
    if tagged:
        text += derive.tag_label(derive.tags_of(row), (FACET,))
    lines = [f"{row['handle']}  {iso(row['t_known'])}  {row['host']}  {row['sourcetype']}  {text}"]
    lines += [f"    {frame.strip()}" for frame in rest[:TRACE_FRAMES]]
    if len(rest) > TRACE_FRAMES:
        lines.append(f"    […+{len(rest) - TRACE_FRAMES} more lines]")
    return Line(row["handle"], "\n".join(lines), row["t_known"])


# -- sampling ----------------------------------------------------------------------------------------------


def _is_sampled(rows: list[dict[str, Any]]) -> bool:
    return any((r["keep_1_in"] or 1) > 1 for r in rows)


def _estimate(rows: list[dict[str, Any]]) -> int:
    return sum(r["keep_1_in"] or 1 for r in rows)


def _rate(rows: list[dict[str, Any]]) -> str:
    """`1:N`, or the lowest and highest when they differ, with the unit the source samples by."""
    rates = sorted({r["keep_1_in"] or 1 for r in rows})
    units = sorted({r["sample_per"] for r in rows if r["sample_per"] and (r["keep_1_in"] or 1) > 1})
    span = f"1:{rates[0]}" if len(rates) == 1 else f"1:{rates[0]} to 1:{rates[-1]}"
    return f"{span} per {' or '.join(units)}" if units else span


def _sampled(rows: list[dict[str, Any]]) -> str:
    return f" (sampled {_rate(rows)}, estimated {_estimate(rows)})" if _is_sampled(rows) else ""


# -- stats -------------------------------------------------------------------------------------------------


def _stats(args: Any, ctx: ToolContext, window: Window, rows: list[dict[str, Any]], footer: list[str], native: str,
           common: dict[str, Any], signal: str | None) -> Parts:
    by = args.group_by
    sampled = _is_sampled(rows)
    if not rows and (signal or by == FACET):        # a tag-dependent count of nothing stays the empty sentence
        return Parts(footer=footer, native_query=native, empty_hint=_empty_hint(args, signal), **common)
    if not rows:
        kept = _sampled_scope(args, ctx, window)
        footer = [ZERO.format(hint=_empty_hint(args, signal)), *([ZERO_SAMPLED.format(rate=_rate(kept))] if kept else []), *footer]
    examples: list[str] = []
    if by == "minute":
        table, said, pipe = _by_time(args, window, rows, sampled)
    else:
        table, said, pipe, examples = _by_group(by, rows, sampled)
        table = table or [NO_GROUPS]
    title = f"count by {by}: {len(rows)} log records{_sampled(rows)}, {iso(window.since)} to {iso(window.until)}"
    head = ["  ".join(["group" if by != "minute" else "bucket", "records", *(["estimated"] if sampled else [])])]
    native = native + pipe
    if signal or by == FACET:       # a count built from tags cannot be cited
        return Parts(header=[title, *said, *head, *table, NOT_CITABLE], footer=footer, native_query=native, total=len(rows),
                     aggregate=True, referenced=examples, sampled=sampled, **{**common, "tag": common["tag"] or f"group_by={FACET}"})
    identity = json.dumps({"tool": "search_logs", "terms": [t.lower() for t in args.terms], "sourcetype": args.sourcetype, "host": args.host,
                           "window": window.iso, "group_by": by, "bucket": args.bucket if by == "minute" else None}, sort_keys=True)
    handle = query_handle(identity, iso(ctx.store.now))
    text = "\n".join([f"{handle}  {title}", *said, *head, *table])
    return Parts(rows=[Line(handle, text, refs=tuple(examples))], footer=footer, native_query=native, total=len(rows), aggregate=True,
                 sampled=sampled, **common)


def _sampled_scope(args: Any, ctx: ToolContext, window: Window) -> list[dict[str, Any]]:
    """The sampled records in the window under the same sourcetype and host, terms aside: what a count of
    nothing cannot see."""
    where, params = ["kind = 'log'", "t_event < ?", "t_known >= ?", "keep_1_in > 1"], [window.until, window.since]
    if args.sourcetype is not None:
        where.append("json_extract(attrs, '$.sourcetype') = ?")
        params.append(args.sourcetype)
    if args.host is not None:
        where.append("host = ?")
        params.append(args.host)
    return derive.query(ctx, f"SELECT DISTINCT keep_1_in, sample_per FROM visible WHERE {' AND '.join(where)}", params)


def preview(args: Any, ctx: ToolContext) -> tuple[str, Window]:
    """The native search and the resolved window a call would echo, without running it (seam S7). A count
    adds its pipe; a tag-dependent count that would find nothing echoes no pipe when it runs."""
    window = resolve(args.since, args.until, ctx.now)
    signal = getattr(args, FACET, None) if ctx.tags and FACET in schemas.offered_facets() else None
    native = _spl(args, ctx, window) + (NO_NATIVE_TAG if signal else "")
    by = args.group_by
    if by == "minute":
        native += f" | timechart span={_bucket(args, window)[0]} count"
    elif by:
        native += NO_NATIVE_TAG if by == FACET else f" | stats count by {'punct' if by == 'template' else by}"
    return native, window


def _bucket(args: Any, window: Window) -> tuple[str, int]:
    """The bucket a count by time uses: the one asked for, widened until the window needs at most 60."""
    asked = dict(BUCKETS)[args.bucket]
    return next(((name, ms) for name, ms in BUCKETS if ms >= asked and -(-(window.until - window.since) // ms) <= MAX_BUCKETS), BUCKETS[-1])


def _by_time(args: Any, window: Window, rows: list[dict[str, Any]], sampled: bool) -> tuple[list[str], list[str], str]:
    """One count per bucket, zeros included, never more than 60 buckets: past that the bucket widens."""
    asked = dict(BUCKETS)[args.bucket]
    label, width = _bucket(args, window)
    said = [f"[the bucket was widened from {args.bucket} to {label} to keep the table to {MAX_BUCKETS} buckets]"] if width != asked else []
    start = window.since - window.since % width
    count, estimate = Counter(), Counter()
    for r in rows:
        slot = max(r["t_known"], window.since)
        slot -= slot % width
        count[slot] += 1
        estimate[slot] += r["keep_1_in"] or 1
    full = iso if window.spans_dates() else clock
    slots = list(range(start, window.until, width))
    if len(slots) > MAX_BUCKETS:       # the newest buckets are never the ones cut
        said.append(f"[the oldest {len(slots) - MAX_BUCKETS} buckets are not shown; shorten the window to see them]")
    table = []
    for slot in slots[-MAX_BUCKETS:]:
        table.append("  ".join([full(slot), str(count[slot]), *([str(estimate[slot])] if sampled else [])]))
    return table, said, f" | timechart span={label} count"


def _keys(by: str, row: dict[str, Any]) -> list[str]:
    if by == "host":
        return [row["host"] or "(none)"]
    if by == "level":
        return [row["sev_text"] or "(none)"]
    if by == "template":
        return [row["template_id"] or "(none)"]
    return derive.tags_of(row).get(FACET) or [schemas.UNTAGGED]        # a record with two values counts under each


def _by_group(by: str, rows: list[dict[str, Any]], sampled: bool) -> tuple[list[str], list[str], str, list[str]]:
    """One count per group over the window: the top 20 and an "other" row. A template is shown as the masked
    line of one of its records, which is named beside it as an example and is only referenced: a masked
    line is not that record's content."""
    count, estimate, example = Counter(), Counter(), {}
    for r in rows:
        for key in _keys(by, r):
            count[key] += 1
            estimate[key] += r["keep_1_in"] or 1
            example.setdefault(key, r)
    if by == FACET:
        count.setdefault(schemas.UNTAGGED, 0)       # the untagged row is always printed, even at zero
    ranked = sorted(count, key=lambda k: (-count[k], str(k)))
    top, rest = ranked[:TOP_GROUPS], ranked[TOP_GROUPS:]
    table, examples = [], []
    for key in top:
        cells = [str(key), str(count[key]), *([str(estimate[key])] if sampled else [])]
        if by == "template":
            cells[0] = f"{example[key]['sourcetype']}: {masker.mask(example[key]['title'] or '')}"
            cells.append(f"example {example[key]['handle']}")
            examples.append(example[key]["handle"])
        table.append("  ".join(cells))
    if rest:
        table.append("  ".join([f"other ({len(rest)} more groups)", str(sum(count[k] for k in rest)),
                                *([str(sum(estimate[k] for k in rest))] if sampled else [])]))
    said = [MULTI_TAG.format(n=len(rows))] if by == FACET else []
    pipe = NO_NATIVE_TAG if by == FACET else f" | stats count by {'punct' if by == 'template' else by}"
    return table, said, pipe, examples


def _spl(args: Any, ctx: ToolContext, window: Window) -> str:
    """The equivalent native search, echoed for the trace. It is built from the arguments, never parsed."""
    where, params = "kind = 'log'", []
    if args.sourcetype is not None:
        where, params = where + " AND json_extract(attrs, '$.sourcetype') = ?", [args.sourcetype]
    indexes = [r["i"] for r in derive.query(ctx, f"SELECT DISTINCT json_extract(attrs, '$.index') AS i FROM visible WHERE {where} ORDER BY 1", params)
               if r["i"]]
    parts = ["search", f"index={indexes[0]}" if len(indexes) == 1 else "index=*"]
    parts += [f"sourcetype={json.dumps(args.sourcetype)}"] if args.sourcetype is not None else []
    parts += [f"host={json.dumps(args.host)}"] if args.host is not None else []
    parts += [json.dumps(term) for term in args.terms]
    parts += [f'earliest="{iso(window.since)}"', f'latest="{iso(window.until)}"']
    return " ".join(parts)
