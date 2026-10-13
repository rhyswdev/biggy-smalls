"""Typed investigation state and its reducers (docs/research/agent-design.md axis 2 and section 3).

Rule: `messages` is append-only. Some providers bind reasoning blocks to the exact conversation prefix, so
editing or deleting an earlier message can invalidate later reasoning, and always breaks the prompt cache.
Everything the terminal, the validator or a restart needs lives in typed channels instead.

There is no evidence map in state: a checkpoint stores every channel in full, so the rows a tool showed go
to the run record's evidence file at call time, not here.

What LangGraph does not checkpoint is the runtime context (`RunContext`): the run record keeps what it
needs of it, and a resume builds it again.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Callable, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from smalls.tools.registry import ToolContext


class Hypothesis(TypedDict, total=False):
    id: str
    statement: str
    role: str
    status: Literal["leading", "open", "rejected"]
    subject: str
    entities: list[str]
    changes: list[str]
    confidence: float
    support: list[str]
    against: list[str]
    source: Literal["stated", "derived"]
    discriminating: list[str]
    history: list[dict[str, Any]]       # one row per change: {round, call_id, from, to, because[]}


class Call(TypedDict, total=False):
    """One proposed call, from the model or from the harness. The gate and the tools node read these from
    state, not from the last model message, so the opening sweep goes through the same policy (J12)."""

    call_id: str
    tool: str
    args: dict[str, Any]                # the tool's own arguments, without the rationale
    rationale: dict[str, Any] | None
    by: Literal["agent", "harness", "carry_forward"]
    invalid: str | None                 # set when the model's arguments could not be parsed at all


class LedgerEntry(TypedDict, total=False):
    """One call, whatever happened to it. `calls.jsonl` is this, with the gate decision beside it."""

    call_id: str
    round: int
    by: str
    tool: str
    kind: str
    args: dict[str, Any]
    rationale: dict[str, Any] | None
    status: Literal["ok", "error", "rejected", "deferred", "repeat"]
    shown: list[str]
    referenced: list[str]
    t_known: dict[str, str | None]      # per shown handle, from the envelope rows; the carry-forward check reads it
    native_query: str | None
    window: list[str] | None
    chars: int
    latency_ms: float
    repeat_of: str | None
    used_tag_input: bool
    empty: bool
    identity: str | None                # what makes two calls the same call (J4)


class GateBy(TypedDict):
    who: Literal["policy", "auto", "human"]
    via: Literal["cli", "headless"]


class GateDecision(TypedDict, total=False):
    call_id: str
    tool: str
    decision: Literal["approved", "rejected", "deferred"]
    reason: str | None                  # an `ic:note/` handle when a person gave one; a policy's own words otherwise
    reason_text: str | None             # what the person wrote, for the message that goes back to the model
    by: GateBy
    decided_at: str


class Note(TypedDict):
    handle: str
    text: str
    submitted_at: str
    by: str
    call_id: str | None


def merge_hypotheses(old: dict[str, Hypothesis] | None, new: dict[str, dict[str, Any]] | None) -> dict[str, Hypothesis]:
    """Field-wise upsert by id: an update carries only the fields the model set, so a confidence-only update
    cannot erase support. A history row is added only when something moved, so "how did belief move, and on
    what evidence" is a query, not a reading exercise."""
    out = dict(old or {})
    tracked = ("status", "confidence", "role", "support", "against")
    for hid, upd in (new or {}).items():
        prev = out.get(hid) or {"entities": [], "changes": [], "support": [], "against": [], "discriminating": [], "history": []}
        merged = {**prev, **{k: v for k, v in upd.items() if k not in ("round", "call_id", "because")}, "id": hid}
        if any(merged.get(k) != prev.get(k) for k in tracked):
            merged["history"] = [*prev.get("history", []), {
                "round": upd.get("round"), "call_id": upd.get("call_id"),
                "from": {k: prev.get(k) for k in ("status", "confidence")} if "status" in prev else None,
                "to": {k: merged.get(k) for k in ("status", "confidence")}, "because": list(upd.get("because") or [])}]
        out[hid] = merged
    return out


def merge_timeline(old: list[dict[str, Any]] | None, new: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Upsert by time and event, kept in time order."""
    rows = {(r["t_utc"], r["event"]): r for r in [*(old or []), *(new or [])]}
    return sorted(rows.values(), key=lambda r: (r["t_utc"], r["event"]))


def add_unique(old: list[str] | None, new: list[str] | None) -> list[str]:
    return list(dict.fromkeys([*(old or []), *(new or [])]))


class InvestigationState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]                 # append-only
    as_of: str
    checkpoint: str | None
    trigger: str
    hypotheses: Annotated[dict[str, Hypothesis], merge_hypotheses]
    timeline: Annotated[list[dict[str, Any]], merge_timeline]
    open_questions: Annotated[list[str], add_unique]
    ledger: Annotated[list[LedgerEntry], operator.add]                  # every call: agent, harness or carry_forward
    ic_notes: Annotated[list[Note], operator.add]                       # citable as ic:note/<h8>
    gate_log: Annotated[list[GateDecision], operator.add]
    proposed: list[Call]                                                # this round's calls, written by agent or sweep
    decisions: dict[str, GateDecision]                                  # this round only
    pending: dict[str, Any] | None                                      # {round, asked_at, calls}, written by gate
    gate_waits: Annotated[list[dict[str, Any]], operator.add]           # asked_at, decided_at, wait, once per round
    usage: Annotated[list[dict[str, Any]], operator.add]                # per model call; survives pauses and crashes
    calls_used: int                                                     # reads and actions that ran (J5)
    swept: list[str]                                                    # the alert streams the sweep showed (J7)
    report: dict[str, Any] | None
    report_attempts: int                                                # per submission cycle
    validation: dict[str, Any] | None
    verdict: dict[str, Any] | None
    revisions: int
    status: Literal["investigating", "verifying", "completed", "incomplete", "paused"]
    stop_reason: str | None                                             # why an incomplete run stopped


@dataclass
class RunContext:
    """Per-run values the harness injects. Never a tool parameter, and not checkpointed by LangGraph, so it
    may hold live objects: the tool context with its store, and the run record's evidence writer."""

    run_id: str
    now: datetime
    tool_ctx: ToolContext
    call_budget: int
    extra_turns: int = 6
    revision_calls: int = 5
    stale_rounds: int = 3
    stuck_empty: int = 3
    context_fraction: float = 0.85
    max_input_tokens: int | None = None         # the profile's, else the model's own
    max_tokens: int = 0
    auto_approve: bool = True                   # reads; actions always ask
    via: Literal["cli", "headless"] = "headless"
    checkpoint: str | None = None
    channel: str | None = None
    sweep: dict[str, str] = field(default_factory=dict)
    verifier: bool = False
    evidence: Callable[[str, Any], None] | None = None      # (call_id, envelope rows) -> the evidence file

    def turn_cap(self, revisions: int = 0) -> int:
        """Model turns are capped at the budget plus `extra_turns`; a revision adds 7 (J5)."""
        return self.call_budget + self.extra_turns + 7 * revisions

    def budget(self, revisions: int = 0) -> int:
        return self.call_budget + self.revision_calls * revisions
