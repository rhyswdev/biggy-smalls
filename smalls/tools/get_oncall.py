"""`get_oncall`: who is on call at `now`, and how a page escalates (PLAN section 4; section 6.4 and K1 of
docs/decisions/tools-agent-build.md).

`service` or `policy` names one paging service or one escalation policy, by its name, its id or its
handle. Exactly one is given; with neither, the visible services and policies are listed.

The answer is the service, its escalation policy with each level's targets and the minutes before a page
moves up a level, and the shifts that hold at `now`: those that started at or before `now` and have not
ended. A shift's end is a scheduled time, known in advance, so it is printed as planned. A service's
status is derived at `now` from the incidents open on it, never read from the served field.

A shift that ended before `now` is not who is on call: it is counted, and its handle is referenced.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from smalls.tools import derive, errors
from smalls.tools.parts import Line, Parts
from smalls.tools.registry import ToolContext
from smalls.tools.windows import iso, ms_of

SERVICES, POLICIES, SHIFTS = "services", "escalation_policies", "oncalls"
BOTH = "give service or policy, not both: a service has one policy, and a policy can serve several services"


def run(args: Any, ctx: ToolContext) -> Parts:
    service, policy = (args.service or "").strip(), (args.policy or "").strip()
    if service and policy:
        raise errors.Refusal(errors.bad_arguments("get_oncall", BOTH))
    rows = derive.query(ctx, "SELECT handle, source, dataset, native_id, title, t_known, attrs FROM visible "
                             "WHERE kind = 'config' AND dataset IN (?, ?, ?) ORDER BY dataset, title, handle", (SERVICES, POLICIES, SHIFTS))
    for row in rows:
        row["attrs"] = derive.loads(row["attrs"])
    services = [r for r in rows if r["dataset"] == SERVICES]
    policies = [r for r in rows if r["dataset"] == POLICIES]
    shifts = [r for r in rows if r["dataset"] == SHIFTS]
    source = rows[0]["source"] if rows else "paging"
    if not service and not policy:
        lines = [_service(ctx, s) for s in services] + [_policy(p, levels=False) for p in policies]
        return Parts(rows=lines, what="services and policies", native_label=source, native_query="GET /services, GET /escalation_policies",
                     empty_hint="No paging service or escalation policy is visible.",
                     footer=["[call again with service= or policy= one of these names for who is on call at now]"] if lines else [])
    if service:
        chosen = _find("service", service, services)
        owner = [p for p in policies if p["native_id"] == chosen["attrs"].get("escalation_policy_id")]
        lines, policy_ids = [_service(ctx, chosen)], [chosen["attrs"].get("escalation_policy_id")]
    else:
        chosen = _find("policy", policy, policies)
        owner = [chosen]
        lines, policy_ids = [], [chosen["native_id"]]
    lines += [_policy(p, levels=True) for p in owner]
    if policy:
        lines += [_service(ctx, s) for s in services if s["attrs"].get("escalation_policy_id") == chosen["native_id"]]
    now = ctx.store.now
    mine = [s for s in shifts if s["attrs"].get("escalation_policy_id") in policy_ids]
    current = sorted((s for s in mine if _holds(s, now)), key=lambda s: (_level(s), s["title"] or ""))
    ended = [s for s in mine if not _holds(s, now)]
    lines += [_shift(s) for s in current]
    footer = []
    levels = {int(rule.get("level") or 0) for p in owner for rule in p["attrs"].get("rules") or []}
    empty = sorted(levels - {_level(s) for s in current})
    if empty:
        footer.append(f"[no visible shift holds at now for level{'s' if len(empty) != 1 else ''} {', '.join(map(str, empty))}]")
    if ended:
        footer.append(f"[{len(ended)} earlier shift{'s' if len(ended) != 1 else ''} ended before now and {'are' if len(ended) != 1 else 'is'} not listed]")
    return Parts(rows=lines, footer=footer, referenced=[s["handle"] for s in ended], what="rows", native_label=source,
                 # with no since and no until, the paging API answers for the moment of the call
                 native_query="GET /oncalls?" + "&".join(f"escalation_policy_ids[]={pid}" for pid in policy_ids))


def _find(field: str, value: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    wanted = value.lower()
    for row in rows:
        if wanted in ((row["title"] or "").lower(), (row["native_id"] or "").lower(), row["handle"].lower()):
            return row
    raise errors.Refusal(errors.unknown_value(field, value, sorted(r["title"] or r["handle"] for r in rows)))


def _ms(text: Any) -> int | None:
    try:
        return ms_of(datetime.fromisoformat(str(text).replace("Z", "+00:00"))) if text else None
    except ValueError:
        return None


def _holds(shift: dict[str, Any], now: int) -> bool:
    """A shift holds at now when it has started and has not ended. One with no end is open-ended."""
    start, end = _ms(shift["attrs"].get("start")), _ms(shift["attrs"].get("end"))
    return (start is None or start <= now) and (end is None or now < end)


def _level(shift: dict[str, Any]) -> int:
    try:
        return int(shift["attrs"].get("level") or 0)
    except (TypeError, ValueError):
        return 0


def _service(ctx: ToolContext, row: dict[str, Any]) -> Line:
    attrs = row["attrs"]
    status, why = derive.service_status(ctx, row["native_id"])
    cells = [row["handle"], f"service {row['title']}", f"status {status} (derived at now: {why})", f"urgency {attrs.get('urgency') or '(none)'}",
             f"escalation policy {attrs.get('escalation_policy') or '(none)'}"]
    if attrs.get("auto_resolve_timeout"):
        cells.append(f"auto-resolves after {int(attrs['auto_resolve_timeout']) // 60} min")
    return Line(row["handle"], "  ".join(cells), row["t_known"])


def _policy(row: dict[str, Any], *, levels: bool) -> Line:
    attrs = row["attrs"]
    rules = sorted(attrs.get("rules") or [], key=lambda r: r.get("level") or 0)
    cells = [row["handle"], f"escalation policy {row['title']}", f"services {', '.join(attrs.get('services') or []) or '(none)'}",
             f"{len(rules)} level{'s' if len(rules) != 1 else ''}", f"repeats {attrs.get('num_loops') or 0} times"]
    lines = ["  ".join(cells)]
    for n, rule in enumerate(rules):
        if not levels:
            break
        after = (f"escalates to level {rules[n + 1].get('level')} after {rule.get('delay_minutes')} min without acknowledgement" if n + 1 < len(rules)
                 else f"last level; the policy waits {rule.get('delay_minutes')} min here")
        lines.append(f"    level {rule.get('level')}: targets {', '.join(str(t) for t in rule.get('targets') or []) or '(none)'}; {after}")
    return Line(row["handle"], "\n".join(lines), row["t_known"])


def _shift(row: dict[str, Any]) -> Line:
    attrs = row["attrs"]
    start, end = _ms(attrs.get("start")), _ms(attrs.get("end"))
    cells = [row["handle"], "on call at now", f"level {attrs.get('level')}", str(attrs.get("user") or "(nobody named)"),
             f"policy {attrs.get('escalation_policy') or '(none)'}", f"schedule {attrs.get('schedule') or '(none)'}",
             f"since {iso(start) if start is not None else '(no start)'}", f"planned end {iso(end)}" if end is not None else "no end set"]
    return Line(row["handle"], "  ".join(cells), row["t_known"])
