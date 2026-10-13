"""Typed investigation state and its reducers.

Rule: `messages` is append-only. Some providers bind thinking blocks to the exact conversation prefix, so
editing or deleting an earlier message can invalidate later thinking (and always breaks the prompt cache).
Everything the UI, the validator or a compaction step needs lives in typed channels instead.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class Hypothesis(TypedDict):
    id: str
    statement: str
    status: Literal["leading", "open", "rejected"]
    confidence: float
    support: list[str]
    against: list[str]
    history: list[dict[str, Any]]


class LedgerEntry(TypedDict):
    """One tool call, whatever happened to it. The scorer's `handles[]` per call comes from here."""

    call_id: str
    tool: str
    kind: str
    args: dict[str, Any]
    rationale: str | None
    status: Literal["ok", "error", "rejected", "deferred"]
    handles: list[str]
    native_query: str | None
    window: list[str] | None
    chars: int
    latency_ms: float


class GateDecision(TypedDict):
    call_id: str
    tool: str
    decision: Literal["approved", "rejected", "deferred"]
    reason: str | None
    by: Literal["auto", "human", "policy", "headless"]


def merge_hypotheses(old: dict[str, Hypothesis] | None, new: dict[str, Hypothesis] | None) -> dict[str, Hypothesis]:
    """Field-wise upsert by id: an update carries only the fields the model set, so a confidence-only update
    cannot erase support. A history row (round, call, from, to, because) is added only when something moved,
    so "how did belief move, and on what evidence" is a query, not a reading exercise."""
    out = dict(old or {})
    tracked = ("status", "confidence", "support", "against")
    for hid, upd in (new or {}).items():
        prev = out.get(hid) or {"support": [], "against": [], "history": []}
        merged = {**prev, **{k: v for k, v in upd.items() if k not in ("round", "call_id", "because")}}
        if any(merged.get(k) != prev.get(k) for k in tracked):
            merged["history"] = [*prev.get("history", []), {
                "round": upd.get("round"), "call_id": upd.get("call_id"),
                "from": {k: prev.get(k) for k in ("status", "confidence")} if "status" in prev else None,
                "to": {k: merged.get(k) for k in ("status", "confidence")}, "because": upd.get("because", [])}]
        out[hid] = merged
    return out


class InvestigationState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    as_of: str
    question: str
    hypotheses: Annotated[dict[str, Hypothesis], merge_hypotheses]
    ledger: Annotated[list[LedgerEntry], operator.add]
    gate_log: Annotated[list[GateDecision], operator.add]
    decisions: dict[str, GateDecision]   # this round's decisions, overwritten each round
    pending: dict[str, Any] | None       # calls awaiting a human: {round, asked_at, calls}; decided once by the gate
    gate_waits: Annotated[list[dict[str, Any]], operator.add]  # one row per human round: asked_at, decided_at, wait
    usage: Annotated[list[dict[str, Any]], operator.add]       # one row per model call, priced by the runner
    tool_calls_used: int
    report: dict[str, Any] | None
    report_attempts: int
    validation: dict[str, Any] | None
    status: str
