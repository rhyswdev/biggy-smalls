"""What a tool returns, and the one renderer that turns it into an envelope (docs/decisions/tools-agent-build.md
section 5 and the rules of section 6.2).

A tool does not build its own envelope. It returns parts: header lines, rows in order, footer hints and
the native query. The renderer applies both limits, so a row cut from the text is also cut from `rows`,
and writes the lines every result shares: the clamped-window note, the footer of a truncated result, and
the native query echo, which comes last.

Caps count tokens as characters over 4. Rows drop from the end until the result fits, or from the start
for a tool that lists oldest first and keeps the newest (`keep="newest"`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from smalls.contracts.envelope import Envelope, Row
from smalls.tools import errors
from smalls.tools.windows import Window, iso

CHARS_PER_TOKEN = 4
TRUNCATED = "[showing {returned} of {total} {what}: {cut} not shown. {narrow}]"
COUNT = "[{total} {what}]"
NATIVE = "[{label}] {query}"


@dataclass
class Line:
    """One row: its handle, its text as the model reads it (the handle first), when it became known, and
    the handles it only points at."""

    handle: str
    text: str
    t_known: int | None = None              # epoch ms; None for an aggregate
    refs: tuple[str, ...] = ()


@dataclass
class Parts:
    rows: list[Line] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    footer: list[str] = field(default_factory=list)         # hints that hold whether or not rows were cut
    referenced: list[str] = field(default_factory=list)     # pointers that belong to the result, not to one row
    native_query: str | None = None
    native_label: str = "native query"
    window: Window | None = None
    now: int | None = None                  # epoch ms; printed when the window was clamped
    total: int | None = None                # how many matched before any limit; defaults to len(rows)
    hidden_span: tuple[int, int] | None = None      # first and last known time of the rows the tool's own limit left out
    aggregate: bool = False                 # a count table: `total` is what it counted, and no row is ever cut from it
    what: str = "rows"
    # Which rows survive a cut, by how the rows are ordered: "oldest" (oldest first), "newest" (oldest first, cut from the
    # start), "newest_first", "best" (ranked) or "nearest" (by distance). All but "newest" cut from the end.
    keep: str = "oldest"
    narrow: str = "Narrow the window or add a filter."
    empty_hint: str = "Widen the window or drop a filter."
    sampled: bool = False
    tag: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


def tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)


def _assemble(parts: Parts, rows: list[Line], total: int, cut: str | None) -> str:
    lines = list(parts.header)
    lines += [row.text for row in rows]
    if cut:
        lines.append(TRUNCATED.format(returned=len(rows), total=total, what=parts.what, cut=cut, narrow=parts.narrow))
    elif rows and len(rows) > 1:
        lines.append(COUNT.format(total=total, what=parts.what))
    lines += parts.footer
    if parts.window and parts.window.clamped and parts.now is not None:
        lines.append(errors.CLAMPED.format(now=iso(parts.now)))
    if parts.native_query:
        lines.append(NATIVE.format(label=parts.native_label, query=parts.native_query))
    return "\n".join(lines)


def _cut_range(parts: Parts, dropped: list[Line], hidden: int) -> str:
    """What a truncated result leaves out, in words the model can narrow on."""
    n = len(dropped) + hidden
    which = {"oldest": "the newest", "newest": "the oldest", "newest_first": "the oldest", "best": "the lower-ranked",
             "nearest": "the farthest"}[parts.keep]
    times = [row.t_known for row in dropped if row.t_known is not None] + list(parts.hidden_span or ())
    timed = parts.keep not in ("best", "nearest")          # a rank or a distance has no time range to name
    span = f", from {iso(min(times))} to {iso(max(times))}" if times and (parts.hidden_span or not hidden) and timed else ""
    return f"{which} {n}{span}"


def render(parts: Parts, *, cap_tokens: int, cap_rows: int | None = None) -> Envelope:
    """The envelope for a tool's parts, inside both caps."""
    total = parts.total if parts.total is not None else len(parts.rows)
    rows = list(parts.rows)
    hidden = max(total - len(rows), 0)          # rows the tool's own limit already left out
    dropped: list[Line] = []
    if not rows and not parts.header:
        text = errors.EMPTY.format(hint=parts.empty_hint)
        tail = _assemble(Parts(footer=parts.footer, window=parts.window, now=parts.now, native_query=parts.native_query,
                               native_label=parts.native_label), [], 0, None)
        return Envelope("\n".join(x for x in (text, tail) if x), native_query=parts.native_query,
                        window=parts.window.iso if parts.window else None,
                        meta={**parts.meta, "total": total, "returned": 0, "empty": True, "sampled": parts.sampled, "tag": parts.tag,
                              "clamped": bool(parts.window and parts.window.clamped)})

    def drop_one() -> None:
        dropped.append(rows.pop(0) if parts.keep == "newest" else rows.pop())

    if parts.aggregate:
        text = _assemble(Parts(header=parts.header, footer=parts.footer, window=parts.window, now=parts.now,
                               native_query=parts.native_query, native_label=parts.native_label), rows[:1], 1, None)
        return Envelope(text, rows=[Row(row.handle, row.text, None) for row in rows[:1]], referenced=list(dict.fromkeys(
                            [*(ref for row in rows[:1] for ref in row.refs), *parts.referenced])),
                        native_query=parts.native_query, window=parts.window.iso if parts.window else None,
                        meta={**parts.meta, "total": total, "returned": total, "truncated": False, "empty": False,
                              "clamped": bool(parts.window and parts.window.clamped), "sampled": parts.sampled, "tag": parts.tag})
    while cap_rows is not None and len(rows) > cap_rows:
        drop_one()
    while True:
        cut = _cut_range(parts, dropped, hidden) if dropped or hidden else None
        text = _assemble(parts, rows, total, cut)
        if tokens(text) <= cap_tokens or len(rows) <= 1:
            break
        drop_one()
    shown = {row.handle for row in rows}
    referenced = [h for h in dict.fromkeys([*(ref for row in rows for ref in row.refs), *parts.referenced]) if h not in shown]
    # A record with no time in its bytes is stored at zero: it has no known time to report.
    return Envelope(text, rows=[Row(row.handle, row.text, iso(row.t_known) if row.t_known else None) for row in rows],
                    referenced=referenced, native_query=parts.native_query, window=parts.window.iso if parts.window else None,
                    meta={**parts.meta, "total": total, "returned": len(rows), "truncated": bool(dropped or hidden), "empty": False,
                          "clamped": bool(parts.window and parts.window.clamped), "sampled": parts.sampled, "tag": parts.tag})
