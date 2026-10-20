"""The per-check outcomes behind "How Smalls got here" (docs/v4-plan.md 5.1): one summarising call after a run
(and after a follow-up that ran checks), on a fast profile the settings name, through the agent's model
adapter. Its input is each check as the run's events recorded it; its output is one clause per check, checked
by code here: one clause, at most 14 words, no handle, no markdown. A clause that fails is left out, and the
web app writes that check's clause from code instead, as it does for every check without a key (`code`, which
is scripted mode's default) or when the call fails. Nothing here steers the investigation: it runs after.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from smalls.agent.config import SCRIPTED, Config
from smalls.agent.models import as_model_call_error, make_model, reasoning_text
from smalls.agent.record import cost
from smalls.contracts.handles import is_handle

PROMPT = Path(__file__).parent / "prompts" / "outcomes.md"
TIMEOUT_S = 60
MAX_WORDS = 14
MAX_CHECKS = 40
MARKDOWN = re.compile(r"[*_`#\[\]<>|]")


def checks(events: list[dict[str, Any]], done: set[str]) -> list[dict[str, Any]]:
    """The agent's checks in a chat's events that have no outcome yet, each as the summariser is given it: what
    it ran, why, whether it ran, its result's first line and the board changes that followed it in its round."""
    proposed: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for e in events:
        d = e["data"]
        if e["type"] == "tool_call_proposed" and d.get("kind") == "read" and d.get("by") == "agent" and not d.get("hidden"):
            cid = d.get("call_id")
            if cid and cid not in done and cid not in proposed:
                proposed[cid] = {"id": cid, "tool": d.get("tool"), "args": d.get("args") or {}, "why": (d.get("rationale") or {}).get("tests"),
                                 "ran": None, "result": None, "board_after": [], "round": d.get("round")}
                order.append(cid)
        elif e["type"] == "gate_decision" and d.get("call_id") in proposed:
            proposed[d["call_id"]]["ran"] = d.get("decision") == "approved"
        elif e["type"] == "tool_result" and d.get("call_id") in proposed:
            c = proposed[d["call_id"]]
            c["result"] = d.get("first_line") if d.get("status") == "ok" else f"error: {d.get('first_line')}"
            c["ran"] = True
        elif e["type"] == "hypothesis_update":
            last = (d.get("history") or [{}])[-1] or {}
            to = last.get("to") or {}
            ran = [proposed[c] for c in order if proposed[c].get("result") is not None]
            if ran and to:
                was = last.get("from") or {}
                ran[-1]["board_after"].append(f"{d.get('statement')}: {was.get('status', 'new')} -> {to.get('status')} {to.get('confidence')}")
    out = []
    for cid in order:
        c = {k: v for k, v in proposed[cid].items() if not k.startswith("_") and k != "round"}
        if c["result"] is not None:
            out.append(c)
    return out[:MAX_CHECKS]


def valid(clause: Any) -> bool:
    """One clause of plain words: at most 14 of them, no sentence break, no handle, no markdown."""
    if not isinstance(clause, str):
        return False
    text = clause.strip().rstrip(".")
    words = text.split()
    if not words or len(words) > MAX_WORDS or "\n" in text or MARKDOWN.search(text):
        return False
    if re.search(r"[.!?]\s+\S", text):
        return False
    return not any(is_handle(w.strip(",;:()\"'“”‘’")) for w in words)


def parse(text: str) -> dict[str, Any]:
    found = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not found:
        return {}
    try:
        body = json.loads(found.group(1))
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class Summariser:
    """One profile's summariser. `summarise` never raises: a failure comes back as an error and no clauses."""

    def __init__(self, cfg: Config, profile: str, script: Any = None):
        if profile not in cfg.profiles:
            raise SystemExit(f"SMALLS_API_OUTCOMES_PROFILE names no profile in the agent's config: {profile!r}")
        spec = cfg.profiles[profile]
        self.spec = spec.with_params(script=script) if spec.provider == SCRIPTED else spec
        self.cfg = cfg
        self.prompt = PROMPT.read_text(encoding="utf-8")

    @property
    def profile(self) -> str:
        return self.spec.profile

    def summarise(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        if not items:
            return {"clauses": {}, "error": None}
        started = time.monotonic()
        turn = f"CHECKS:\n```json\n{json.dumps(items, ensure_ascii=False, indent=1)}\n```\n"
        try:
            model, bind = make_model(self.spec, timeout_s=min(self.cfg.timeout_s, TIMEOUT_S))
            message = (model.bind(**bind) if bind else model).invoke([SystemMessage(self.prompt), HumanMessage(turn)])
        except Exception as error:      # described by kind, never with a key value it may quote
            return {"clauses": {}, "error": str(as_model_call_error(error))}
        name = message.response_metadata.get("model_name") or self.spec.model
        usage = dict(message.usage_metadata or {})
        body = parse(reasoning_text(message)[1])
        ids = {c["id"] for c in items}
        clauses = {k: v.strip().rstrip(".") for k, v in body.items() if k in ids and valid(v)}
        return {"clauses": clauses, "dropped": sorted(set(body) & ids - set(clauses)), "error": None if body else "no clauses came back",
                "usage": usage, "model": name, "cost_usd": cost({"usage": usage}, self.cfg.price(name)),
                "latency_ms": round((time.monotonic() - started) * 1000, 1)}
