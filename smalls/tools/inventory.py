"""The source inventory (ruling R1): what exists, built at run time from visible rows only, and injected once
per checkpoint in the first user turn. It names things so the model can ask for them; it shows no record's
content, so every handle it lists is referenced, never shown.

It lists knowledge documents, metric series with their scopes, saved searches with how often each fired,
chat channels, log sourcetypes with their hosts, the first and last visible record time per source and,
with tags on, how many rows carry each tag, with the untagged count beside them.

It is capped at 2k tokens: past 20 entries of a kind it prints the count and the first 20.

Nothing here comes from a file that has no clock without first passing through visible rows: a channel is
listed when it has a visible message, a metric when it has a visible point.
"""

from __future__ import annotations

from smalls.contracts.envelope import Envelope
from smalls.tools import derive, schemas
from smalls.tools.registry import ToolContext
from smalls.tools.windows import iso

MAX_LISTED = 20
CAP_TOKENS = 2000
# The kinds of record each tag facet applies to. A kind a facet does not apply to is not counted as untagged.
FACET_KINDS = {"signal": ("log", "alert", "alert_def", "incident"), "act": ("message", "note"), "change_kind": ("change", "build", "commit")}


def listed(values: list[str], sep: str = ", ") -> str:
    """Past 20 entries of a kind the inventory prints the count and the first 20."""
    return sep.join(values) if len(values) <= MAX_LISTED else f"{len(values)} in all; the first {MAX_LISTED}: " + sep.join(values[:MAX_LISTED])


def block(title: str, rows: list[str]) -> list[str]:
    if not rows:
        return []
    more = [f"  ({len(rows) - MAX_LISTED} more are not listed)"] if len(rows) > MAX_LISTED else []
    return [f"{title} ({len(rows)}):", *(f"  {row}" for row in rows[:MAX_LISTED]), *more]


def build(ctx: ToolContext) -> Envelope:
    lines: list[str] = []
    handles: list[str] = []
    tagged = ctx.tags

    docs = derive.query(ctx, "SELECT handle, title, json_extract(attrs, '$.owner') AS owner, json_extract(attrs, '$.last_updated') AS updated "
                             "FROM visible WHERE kind = 'doc' ORDER BY handle")
    lines += block("Knowledge documents", [f"{d['handle']}  {d['title']}  owner {d['owner'] or '(none)'}  updated {d['updated'] or '(no date)'}" for d in docs])
    handles += [d["handle"] for d in docs]

    metrics = derive.metric_queries(ctx)
    rows = []
    for m in metrics:
        signal = sorted({v for s in m["series"] for v in s["tags"].get("signal", [])})
        label = derive.tag_label({"signal": signal}, ("signal",)) if tagged and "signal" in schemas.offered_facets() else ""
        grouped = f"  by {','.join(m['by'])} ({len(m['series'])} series)" if m["by"] else ""
        rows.append(f"{m['handle']}  metric {m['metric']}  scope {','.join(sorted(m['scope'])) or '*'}{grouped}{label}")
    lines += block("Metric series", rows)
    handles += [m["handle"] for m in metrics]

    searches = derive.query(ctx, "SELECT s.handle, s.title, s.status, (SELECT count(DISTINCT a.stream_key) FROM visible a WHERE a.parent = s.handle) AS fired "
                                 "FROM visible s WHERE s.kind = 'alert_def' AND s.stream_key IS NULL ORDER BY s.handle")
    lines += block("Saved searches", [f"{s['handle']}  {s['title']}  {s['status'] or ''}  fired {s['fired']} times".replace("    ", "  ") for s in searches])
    handles += [s["handle"] for s in searches]

    channels = derive.channels(ctx)
    if channels:
        lines.append("Chat channels: " + listed(channels))

    logs = derive.query(ctx, "SELECT json_extract(attrs, '$.sourcetype') AS sourcetype, host, count(*) AS n FROM visible WHERE kind = 'log' GROUP BY 1, 2 ORDER BY 1, 2")
    by_type: dict[str, list[dict]] = {}
    for row in logs:
        by_type.setdefault(row["sourcetype"], []).append(row)
    lines += block("Logs, by sourcetype", [f"{sourcetype}: {sum(r['n'] for r in rows)} records; hosts: {listed([r['host'] for r in rows if r['host']])}"
                                           for sourcetype, rows in by_type.items()])

    coverage = derive.query(ctx, "SELECT source, count(*) AS n, min(t_known) AS first, max(t_known) AS last FROM visible WHERE t_known > 0 GROUP BY source ORDER BY source")
    lines += block("Time coverage, first to last visible record", [f"{c['source']}: {iso(c['first'])} to {iso(c['last'])} ({c['n']} records)" for c in coverage])

    if tagged:
        counts = []
        for facet in schemas.offered_facets():
            kinds = FACET_KINDS.get(facet, ())
            marks = ", ".join("?" * len(kinds))
            rows = derive.query(ctx, f"SELECT tags FROM visible WHERE kind IN ({marks}) AND (tag_key IS NOT NULL OR tags != '{{}}')", kinds)
            per: dict[str, int] = {}
            untagged = 0
            for row in rows:
                values = derive.tags_of(row).get(facet, [])
                untagged += not values
                for value in values:
                    per[value] = per.get(value, 0) + 1
            if rows:
                counts.append(f"{facet} over {len(rows)} rows: " + ", ".join(f"{v} {n}" for v, n in sorted(per.items(), key=lambda kv: (-kv[1], kv[0])))
                              + f"; untagged {untagged}")
        lines += block("Tags, a search aid and never evidence; a row can carry several or none", counts)

    text = "\n".join(lines) or "Nothing is visible yet."
    while len(text) // 4 > CAP_TOKENS and len(lines) > 1:       # the cap holds whatever the bundle's size
        lines.pop()
        text = "\n".join([*lines, "[the inventory was cut to its cap]"])
    return Envelope(text, referenced=list(dict.fromkeys(handles)))
