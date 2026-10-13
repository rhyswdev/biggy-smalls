"""The source inventory (ruling R1): what exists, built at run time from visible rows only, and injected once
per checkpoint in the first user turn. It names things so the model can ask for them; it shows no record's
content, so every handle it lists is referenced, never shown.

This is the minimal inventory the first runs use: log sourcetypes with their hosts, and chat channels. The
full one (knowledge documents, metric series, saved searches, time coverage, tag counts) arrives with the
knowledge tools.
"""

from __future__ import annotations

from smalls.contracts.envelope import Envelope
from smalls.tools import derive
from smalls.tools.registry import ToolContext
from smalls.tools.windows import iso

MAX_LISTED = 20


def listed(values: list[str]) -> str:
    """Past 20 entries of a kind the inventory prints the count and the first 20."""
    return ", ".join(values) if len(values) <= MAX_LISTED else f"{len(values)} in all; the first {MAX_LISTED}: " + ", ".join(values[:MAX_LISTED])


def build(ctx: ToolContext) -> Envelope:
    lines = []
    logs = derive.query(ctx, "SELECT json_extract(attrs, '$.sourcetype') AS sourcetype, host, count(*) AS n, min(t_known) AS first, "
                             "max(t_known) AS last FROM visible WHERE kind = 'log' GROUP BY 1, 2 ORDER BY 1, 2")
    by_type: dict[str, list[dict]] = {}
    for row in logs:
        by_type.setdefault(row["sourcetype"], []).append(row)
    if by_type:
        lines.append("Logs, by sourcetype:")
    for sourcetype, rows in by_type.items():
        hosts = listed([r["host"] for r in rows if r["host"]])
        lines.append(f"  {sourcetype}: {sum(r['n'] for r in rows)} records, {iso(min(r['first'] for r in rows))} to "
                     f"{iso(max(r['last'] for r in rows))}; hosts: {hosts}")
    channels = derive.channels(ctx)
    if channels:
        lines.append("Chat channels: " + listed(channels))
    return Envelope("\n".join(lines) or "Nothing is visible yet.")
