"""Process metrics for one run (PLAN section 6.1, layer 5): how the agent spent its calls and tokens. They
are counted from the run's own state, by code, and go into `summary.json`.

Nothing here knows which hypothesis is right. "Time to the first correct hypothesis" needs the answer
key, which only the scorer reads; what is written here is when each hypothesis first appeared and when it
first led, with the tool calls and tokens spent by then, so the scorer can read the row for the one it
judges correct.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from smalls.agent import validator


def _tokens(call: dict[str, Any]) -> int:
    usage = call.get("usage") or {}
    return (usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0)


def process(values: dict[str, Any], usage_calls: list[dict[str, Any]], events: dict[str, int], *, budget: int, turn_cap: int) -> dict[str, Any]:
    ledger = values.get("ledger") or []
    gated = [e for e in ledger if e.get("by") == "agent" and e.get("kind") in ("read", "action")]
    ran = [e for e in gated if e.get("status") == "ok" and not e.get("repeat_of")]
    report = values.get("report") or {}
    cited = validator.report_handles(report) if report else set()
    shown = {h for e in ledger for h in e.get("shown") or []}
    total = sum(_tokens(call) for call in usage_calls)
    through: dict[int, int] = {}            # tokens spent by the end of each round's model call
    spent = 0
    for call in usage_calls:
        spent += _tokens(call)
        through[call.get("round") or 0] = spent
    turns = sum(1 for call in usage_calls if call.get("node") == "agent")
    used = values.get("calls_used", 0)

    def by(round_no: int | None) -> dict[str, Any] | None:
        """What had been spent when a board write of this round was made: the calls of earlier rounds, and
        the tokens through this round's model call."""
        if round_no is None:
            return None
        return {"round": round_no, "tool_calls_before": sum(1 for e in ran if (e.get("round") or 0) < round_no),
                "tokens_through": through.get(round_no, max((n for r, n in through.items() if r <= round_no), default=0))}

    hypotheses = []
    for hid, h in (values.get("hypotheses") or {}).items():
        history = h.get("history") or []
        led = next((row.get("round") for row in history if (row.get("to") or {}).get("status") == "leading"), None)
        hypotheses.append({"id": hid, "subject": h.get("subject"), "role": h.get("role"), "status": h.get("status"), "source": h.get("source"),
                           "changes": len(history), "first_seen": by(history[0].get("round") if history else None), "first_leading": by(led)})
    return {
        "tool_calls": used,
        "calls_by_tool": dict(sorted(Counter(e["tool"] for e in ran).items())),
        "repeated_calls": sum(1 for e in gated if e.get("repeat_of") or e.get("status") == "repeat"),
        "empty_results": sum(1 for e in ran if e.get("empty")),
        "error_results": sum(1 for e in gated if e.get("status") == "error"),
        "tag_input_calls": sum(1 for e in gated if e.get("used_tag_input")),
        "refused_by_policy": sum(1 for g in values.get("gate_log") or [] if (g.get("by") or {}).get("who") == "policy" and g.get("decision") != "approved"),
        "model_turns": turns,
        "shown_handles": len(shown),
        "cited_handles": len(cited),
        "cited_share_of_shown": round(len(cited & shown) / len(shown), 4) if shown else None,
        "tokens_total": total,
        "tokens_per_cited_handle": round(total / len(cited), 1) if cited else None,
        "budget": {"calls": budget, "calls_used": used, "hit": used >= budget > 0, "turn_cap": turn_cap, "turns_used": turns,
                   "turn_cap_hit": turns >= turn_cap > 0},
        "stuck_events": events.get("stuck", 0),
        "quiet_board": quiet_board(values),
        "restarts": events.get("restart", 0),
        "hypotheses": hypotheses,
    }


def quiet_board(values: dict[str, Any]) -> dict[str, Any]:
    """The rounds whose status line nudged a quiet board (J14, amended), and how many of those nudges the
    board answered by moving in the very next round."""
    nudged = sorted(set(values.get("nudges") or []))
    changed = {row.get("round") for h in (values.get("hypotheses") or {}).values() for row in h.get("history") or []}
    return {"nudged_rounds": nudged, "moved_next_round": sum(1 for r in nudged if r + 1 in changed)}

