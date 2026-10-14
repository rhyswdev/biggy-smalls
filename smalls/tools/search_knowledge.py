"""`search_knowledge`: runbooks and knowledge pages, and the close notes and work notes of incidents (PLAN
section 4; section 6.4 of docs/decisions/tools-agent-build.md).

Plain keyword ranking: SQLite FTS5 `bm25()` over porter-stemmed text. Every word of the query is quoted, so
no query text can raise a syntax error, and the quoted words are joined with OR, so a passage that holds
any of them is a hit and the ranking does the rest. It is never tuned against particular documents, which
is why the top hit is not always the right one.

A hit is one passage: a section of a page, a close note, a work note. Two hits on one page are two rows
with the same handle. A page section is shown as a snippet and marked; `get_record` on its handle returns
the whole page. A closed incident's close note is returned whole, with its CI, category and close code.

A passage is searchable exactly when the record it belongs to is visible at `now`.
"""

from __future__ import annotations

import re
from typing import Any

from smalls.tools import derive
from smalls.tools.parts import Line, Parts
from smalls.tools.registry import ToolContext
from smalls.tools.windows import iso

MAX_K = 5               # the most passages one call returns (the argument model's own bound)
SNIPPET_CHARS = 600
LEAD_CHARS = 150        # how much of a passage a snippet shows before the first word it matched
KINDS = {"doc": "page section", "incident": "close note", "note": "work note"}
SHORTENED = "[a passage ending in […] is a snippet: get_record <handle> returns the whole record]"
ADVICE = "[ranked by keyword overlap only, and never tuned: read past the top hit]"


NO_WORDS = "(no words in the query)"


def preview(args: Any, ctx: ToolContext) -> tuple[str, None]:
    """The match expression a call would run, without running it (seam S7). It has no window."""
    words = re.findall(r"\w+", args.query)
    return (" OR ".join(f'"{word}"' for word in words) if words else NO_WORDS), None


def run(args: Any, ctx: ToolContext) -> Parts:
    words = re.findall(r"\w+", args.query)
    match = " OR ".join(f'"{word}"' for word in words)
    if not words:
        return Parts(native_query=NO_WORDS, native_label="FTS5", empty_hint="Give at least one word to search for.")
    hits = derive.query(ctx, "SELECT v.handle, v.kind, v.title, v.t_known, v.attrs, vc.heading, vc.text, bm25(chunk_fts) AS score "
                             "FROM chunk_fts JOIN visible_chunk vc ON vc.chunk_id = chunk_fts.rowid JOIN visible v ON v.rid = vc.rid "
                             "WHERE chunk_fts MATCH ? ORDER BY score, vc.chunk_id", (match,))
    rows = [_line(n, hit, words) for n, hit in enumerate(hits[: args.k], 1)]
    footer = [ADVICE] if rows else []
    if any("[…" in row.text for row in rows):
        footer.append(SHORTENED)
    return Parts(rows=rows, footer=footer, native_query=match, native_label="FTS5", total=len(hits), what="passages", keep="best",
                 narrow=("Raise k, or use" if args.k < MAX_K else "Use") + " words the passage you want would hold.",
                 empty_hint="Use other words: each word is matched as a whole keyword, and any one of them is enough.")


def _line(rank: int, hit: dict[str, Any], words: list[str]) -> Line:
    attrs = derive.loads(hit["attrs"])
    updated = attrs.get("last_updated") or (iso(hit["t_known"])[:10] if hit["t_known"] else "(no date)")
    text = hit["text"] if hit["kind"] == "incident" else _snippet(hit["text"], words)       # a close note is returned whole
    body = text.replace("\n", "\n    ")
    head = f"{hit['handle']}  [{rank}] {hit['heading'] or hit['title'] or '(untitled)'}  {KINDS.get(hit['kind'], hit['kind'])}  updated {updated}"
    return Line(hit["handle"], f"{head}\n    {body}", hit["t_known"])


def _snippet(text: str, words: list[str]) -> str:
    """Up to 600 characters of a passage, around the first query word it holds, and marked where it is cut."""
    if len(text) <= SNIPPET_CHARS:
        return text
    lowered = text.lower()
    found = [pos for pos in (lowered.find(word.lower()) for word in words) if pos >= 0]
    start = max(min(found) - LEAD_CHARS, 0) if found else 0
    start = min(start, len(text) - SNIPPET_CHARS)
    if start > 0:
        start = text.find(" ", start) + 1 or start      # begin on a word
    piece = text[start:start + SNIPPET_CHARS]
    return ("[…] " if start > 0 else "") + piece + (f" […+{len(text) - start - len(piece)} chars]" if start + len(piece) < len(text) else "")
