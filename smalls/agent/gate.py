"""The gate policy: pure functions and the decision vocabulary (docs/research/agent-design.md axis 3;
J15 in docs/decisions/tools-agent-build.md).

The gate node applies the policy once and writes what needs a person to state; the ask node only
interrupts. So changing a flag while a run is paused cannot change what was asked.

| Call                         | Auto-approve on (default)      | Interactive |
|------------------------------|--------------------------------|-------------|
| Read                         | approved, by `auto`            | ask         |
| Action (mock)                | ask                            | ask         |
| Internal                     | approved, by `policy`          | same        |
| Over budget, or board stale  | rejected, by `policy`          | same        |
| Unknown tool                 | rejected, by `policy`          | same        |

An action asks even when reads do not: logs, chat and tickets are untrusted input, so the gate is the
boundary that stops text inside them from triggering anything without a person. When nobody is there to
ask, the runner's headless decider answers, and the answer is recorded as policy's, never as a person's.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from smalls.agent.internal_tools import SUBMIT_REPORT
from smalls.agent.state import Call, GateBy, GateDecision
from smalls.tools.registry import ToolSpec

REJECTED = "REJECTED by {who}: {reason}. The call was not run. Re-plan; do not repeat it unchanged."
DEFERRED = ("DEFERRED: the incident commander has not decided. The call was not run. Continue without "
            "assuming it happened.")
EDITED = "EDITED by the incident commander: the call ran with {edited} in place of the arguments you proposed, {proposed}."
NOT_EDITABLE = "only a read can be run with edited arguments"
BAD_EDIT = "the edited arguments are not valid for {tool}: {problem}"
SUBMIT_NOW = "submit now"
NOTE_KEY = "__note__"       # in a resume payload: a note from the incident commander, beside the decisions
OVER_BUDGET = f"the call budget is spent; {SUBMIT_NOW}: call {SUBMIT_REPORT} as the only call in your turn"
NOT_ALONE = f"{SUBMIT_REPORT} must be the only call in its turn; read the other results first, then submit"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def decision(call: Call, verdict: str, who: str, via: str, reason: str | None = None, reason_text: str | None = None) -> GateDecision:
    return {"call_id": call["call_id"], "tool": call["tool"], "decision": verdict, "reason": reason, "reason_text": reason_text,
            "by": GateBy(who=who, via=via), "decided_at": now_iso()}


def apply(calls: Iterable[Call], tools: dict[str, ToolSpec], *, used: int, budget: int, auto_approve: bool, via: str,
          refuse_gated: str | None = None) -> tuple[dict[str, GateDecision], list[dict[str, Any]]]:
    """Decide every call that policy can decide, and return the rest as cards for a person.

    `used` is the number of reads and actions that have run; the count goes up per call here, so a
    parallel batch cannot overshoot the budget. A call from the harness is never charged (J5).
    `refuse_gated` is a reason to refuse every read and action this round (a stale board, J14)."""
    calls = list(calls)
    decisions: dict[str, GateDecision] = {}
    ask: list[dict[str, Any]] = []
    alone = len(calls) == 1
    for call in calls:
        spec = tools.get(call["tool"])
        if call.get("invalid"):                     # unparseable arguments: the tools node answers with the error
            decisions[call["call_id"]] = decision(call, "approved", "policy", via)
        elif spec is None:
            decisions[call["call_id"]] = decision(call, "rejected", "policy", via, f"no tool named {call['tool']}; the tools are: {', '.join(tools)}")
        elif call["tool"] == SUBMIT_REPORT and not alone:
            decisions[call["call_id"]] = decision(call, "rejected", "policy", via, NOT_ALONE)
        elif spec.kind == "internal":
            decisions[call["call_id"]] = decision(call, "approved", "policy", via)
        elif refuse_gated and call.get("by") == "agent":
            decisions[call["call_id"]] = decision(call, "rejected", "policy", via, refuse_gated)
        elif call.get("by") == "agent" and used >= budget:
            decisions[call["call_id"]] = decision(call, "rejected", "policy", via, OVER_BUDGET)
        else:
            if call.get("by") == "agent":
                used += 1
            if spec.kind == "read" and auto_approve:
                decisions[call["call_id"]] = decision(call, "approved", "auto", via)
            else:
                ask.append({"call_id": call["call_id"], "tool": call["tool"], "kind": spec.kind, "by": call.get("by", "agent"),
                            "args": call["args"], "rationale": call.get("rationale")})
    return decisions, ask


def answer(card: dict[str, Any], reply: dict[str, Any] | None) -> tuple[str, str, str | None]:
    """Read one resume payload as (decision, who, reason). Our code sets who decided: a payload cannot claim
    to be policy. Only the runner's own headless decider marks its answers, with `via: headless`, and those
    are recorded as policy's. A missing answer is a deferral."""
    reply = reply or {"defer": True, "reason": "no decision given"}
    who = "policy" if reply.get("via") == "headless" else "human"
    if reply.get("defer"):
        return "deferred", who, reply.get("reason")
    if reply.get("approve"):
        return "approved", who, None
    return "rejected", who, reply.get("reason") or "no reason given"


def edited(card: dict[str, Any], reply: dict[str, Any] | None, spec: ToolSpec | None) -> tuple[dict[str, Any] | None, str | None]:
    """The arguments a person approved a read with in place of the proposed ones (seam S2): (the arguments,
    None) when they validate against the tool's own argument model, (None, why not) when they do not or the
    call is not a read, and (None, None) when the answer carries none."""
    if not reply or not reply.get("approve") or reply.get("args") is None:
        return None, None
    if spec is None or card.get("kind") != "read":
        return None, NOT_EDITABLE
    args = {k: v for k, v in dict(reply["args"]).items() if k != "rationale"}
    try:
        spec.args.model_validate(args)
    except Exception as problem:
        first = str(problem).splitlines()
        return None, BAD_EDIT.format(tool=spec.name, problem=" ".join(first[1:3]).strip() or first[0])
    return args, None


def headless(choice: str):
    """The decider for a run nobody is watching: answer every gate the same way."""
    if choice not in ("approve", "defer", "reject"):
        raise ValueError(f"a headless decider approves, defers or rejects, not {choice!r}")

    def decide(cards: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        if choice == "defer":
            return {c["call_id"]: {"defer": True, "via": "headless", "reason": "unattended run"} for c in cards}
        if choice == "approve":
            return {c["call_id"]: {"approve": True, "via": "headless"} for c in cards}
        return {c["call_id"]: {"approve": False, "via": "headless", "reason": "unattended run: gated calls are rejected"} for c in cards}

    decide.choice = choice
    return decide


def guard_headless_approval(choice: str, tools: dict[str, ToolSpec]) -> None:
    """The hard guard (agent-design ruling 8.5): an unattended run may approve an action only while every
    action tool is a mock. With a real executor behind the gate, a poisoned log line could otherwise reach
    it with nobody looking."""
    real = [t.name for t in tools.values() if t.kind == "action" and not t.mock]
    if choice == "approve" and real:
        raise SystemExit(f"refusing headless approval: these action tools are not mocks: {', '.join(real)}")
