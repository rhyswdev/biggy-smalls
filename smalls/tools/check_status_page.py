"""`check_status_page`: an external provider's status page, as it was last published before `now` (PLAN
section 4; section 6.4 and K7 of docs/decisions/tools-agent-build.md).

A status page is stored as the snapshots its provider published, and the one valid at `now` is the page.
It is printed as published: the indicator, every component that is not operational, each incident with
its updates, and scheduled maintenance. What a provider says about itself is that provider's claim, and
a page can be behind the provider's own trouble; the tool prints the page and draws no conclusion.

With no `provider` the result names the pages that have a visible snapshot. It shows none of their
content, so their handles are referenced and not shown.

An update's text is shortened past 240 characters, and older updates are dropped when the page would not
fit; both say so, and `get_record` on the snapshot's handle returns every character.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from smalls.tools import derive, errors
from smalls.tools.parts import CHARS_PER_TOKEN, Line, Parts
from smalls.tools.registry import CAPS, ToolContext
from smalls.tools.store import render_native
from smalls.tools.windows import iso, ms_of

BODY_CHARS = 240
OPERATIONAL = "operational"
RESERVE_CHARS = 260         # room for the footers and the native query under the cap
SHORTENED = "[text ending in […+N chars] is shortened, and an incident may hold earlier updates than are listed: get_record {handle} returns the whole page]"
LISTING = "Status pages with a visible snapshot ({n}). Call again with provider= one of these names to read a page:"
ENDPOINT = "GET {url}/api/v2/summary.json"


def run(args: Any, ctx: ToolContext) -> Parts:
    pages = derive.query(ctx, "SELECT handle, source, entity_raw, title, status, t_known, version, attrs, native, flags FROM visible "
                              "WHERE kind = 'status' ORDER BY source, handle")
    if args.provider is None or not args.provider.strip():
        lines = [LISTING.format(n=len(pages))] if pages else []
        lines += [f"  provider={p['source']}  {p['entity_raw'] or '(unnamed page)'}  {derive.loads(p['attrs']).get('url') or '(no url)'}  snapshot {p['handle']}"
                  for p in pages]
        return Parts(header=lines, referenced=[p["handle"] for p in pages], native_label="status page", native_query="(the list of known pages)",
                     empty_hint="No status page has a visible snapshot.")
    wanted = args.provider.strip().lower()
    found = [p for p in pages if wanted in (p["source"].lower(), (p["entity_raw"] or "").lower())]
    if not found:
        raise errors.Refusal(errors.unknown_value("provider", args.provider, sorted({p["source"] for p in pages})))
    page = found[0]
    served = json.loads(render_native(page["native"], page["flags"]))
    url = (served.get("page") or {}).get("url") or derive.loads(page["attrs"]).get("url") or "(no url)"
    budget = CAPS["check_status_page"][0] * CHARS_PER_TOKEN - RESERVE_CHARS
    keep = max((len(i.get("incident_updates") or []) for i in served.get("incidents") or []), default=0)
    while True:
        lines, cut = _page(page, served, url, keep)
        if len("\n".join(lines)) <= budget or keep <= 1:
            break
        keep -= 1
    footer = [SHORTENED.format(handle=page["handle"])] if cut else []
    return Parts(rows=[Line(page["handle"], "\n".join(lines), page["t_known"])], footer=footer, native_label="status page",
                 native_query=ENDPOINT.format(url=url.rstrip("/")), what="pages")


def _page(page: dict[str, Any], served: dict[str, Any], url: str, keep: int) -> tuple[list[str], bool]:
    """The page as lines, with at most `keep` updates per incident, newest first. The flag says whether
    anything was shortened or left out."""
    cut = False
    status = served.get("status") or {}
    head = served.get("page") or {}
    lines = [f"{page['handle']}  status page {head.get('name') or page['entity_raw'] or '(unnamed)'}  {url}  "
             f"indicator {status.get('indicator') or page['status'] or '(none)'}: {status.get('description') or page['title'] or '(no description)'}  "
             f"page updated {_time(head.get('updated_at'))}"]
    components = [c for c in served.get("components") or [] if not c.get("group")]
    degraded = [c for c in components if c.get("status") != OPERATIONAL]
    fine = len(components) - len(degraded)
    listed = "; ".join(f"{c.get('name')} {c.get('status')} (since {_time(c.get('updated_at'))})" for c in degraded)
    lines.append(f"components ({len(components)}): " + (f"{listed}; " if listed else "") + ("all " if not degraded else "") + f"{fine} {OPERATIONAL}")
    for kind, items in (("incidents", served.get("incidents") or []), ("scheduled maintenance", served.get("scheduled_maintenances") or [])):
        lines.append(f"{kind} ({len(items)})" + (":" if items else ": none listed"))
        for item in items:
            times = [f"{name} {_time(item[field])}" for name, field in (("started", "started_at"), ("created", "created_at"), ("updated", "updated_at"),
                                                                        ("scheduled for", "scheduled_for"), ("scheduled until", "scheduled_until"),
                                                                        ("monitoring", "monitoring_at"), ("resolved", "resolved_at")) if item.get(field)]
            affected = ", ".join(c.get("name") or "" for c in item.get("components") or [])
            lines.append("  " + "  ".join([str(item.get("name") or "(unnamed)"), f"impact {item.get('impact') or '(none)'}",
                                           f"status {item.get('status') or '(none)'}", *times, *([f"components {affected}"] if affected else [])]))
            updates = sorted(item.get("incident_updates") or [], key=lambda u: _ms(u.get("display_at") or u.get("created_at")) or 0, reverse=True)
            for update in updates[:keep]:
                body = " ".join(str(update.get("body") or "").split())
                if len(body) > BODY_CHARS:
                    body, cut = f"{body[:BODY_CHARS]} […+{len(body) - BODY_CHARS} chars]", True
                lines.append(f"    {_time(update.get('display_at') or update.get('created_at'))}  {update.get('status') or '(no status)'}: {body}")
            if len(updates) > keep:
                lines.append(f"    [{len(updates) - keep} earlier update{'s' if len(updates) - keep != 1 else ''} not listed]")
                cut = True
    return lines, cut


def _ms(text: Any) -> int | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return ms_of(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _time(text: Any) -> str:
    """A served time as UTC with its native value beside it; a time that cannot be read is printed as served."""
    ms = _ms(text)
    if ms is None:
        return str(text) if text else "(none)"
    return derive.stamp(ms, text) if not str(text).endswith("Z") else iso(ms)
