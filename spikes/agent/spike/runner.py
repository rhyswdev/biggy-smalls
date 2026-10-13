"""Drives the graph, maps LangGraph stream modes to the event protocol, and writes the run record.

This is the only module that knows about stream modes. A FastAPI endpoint would call `Runner.drive()` and
forward each event as SSE; the CLI renders the same events; the run record stores them.

Stream modes used: `messages` (token chunks: thinking and text deltas), `updates` (node outputs: proposed calls,
gate decisions, tool results, board changes, report; plus `__interrupt__`), `custom` (what nodes write with
`get_stream_writer()`: model-call usage and latency, tool started, budget).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Any, Callable

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from .config import Config
from .events import Bus, JsonlSink, Sink
from .graph import SYSTEM_PROMPT, build_graph, partial_report, tool_definitions
from .models import make_model, reasoning_text
from .tools import TOOLS, RunContext

Decider = Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]]


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except Exception:
        return None


def headless(decision: str = "defer") -> Decider:
    """For eval runs and tests: answer every gate the same way, recorded as `headless`. The default defers, so a
    headless run never feeds the agent a human endorsement or a refusal it did not get."""
    def decide(calls: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        if decision == "defer":
            return {c["call_id"]: {"defer": True, "by": "headless", "reason": "headless run"} for c in calls}
        return {c["call_id"]: {"approve": decision == "approve", "by": "headless",
                               "reason": None if decision == "approve" else "headless policy rejects gated calls"}
                for c in calls}
    return decide


class Runner:
    def __init__(self, cfg: Config, run_id: str | None = None, *, sinks: list[Sink] | None = None,
                 decider: Decider | None = None, stop_at_gate: bool = False, stream: bool = True,
                 model_overrides: dict[str, Any] | None = None):
        self.cfg = cfg
        self.run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]
        self.dir = cfg.runs_dir / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        prior = (self.dir / "events.jsonl").read_text().count("\n") if (self.dir / "events.jsonl").exists() else 0
        self.jsonl = JsonlSink(self.dir / "events.jsonl")
        self.bus = Bus(self.run_id, [self.jsonl, *(sinks or [])], start_seq=prior)
        self.decider = decider or headless("defer")
        self.stop_at_gate = stop_at_gate
        self.stream = stream
        spec = cfg.investigator
        if model_overrides:
            spec = type(spec)(spec.profile, spec.provider, spec.model, {**spec.params, **model_overrides})
        self.model, bind = make_model(spec)
        self.conn = sqlite3.connect(self.dir / "checkpoints.sqlite", check_same_thread=False)
        self.graph = build_graph(self.model, bind, SqliteSaver(self.conn))
        self.config = {"configurable": {"thread_id": self.run_id}, "recursion_limit": 4 * cfg.tool_call_budget + 20}
        self._ctx: RunContext | None = None
        self._investigating = False

    # ------------------------------------------------------------ entry points

    def start(self, question: str, now: datetime) -> dict[str, Any]:
        self._ctx = RunContext(self.run_id, now, self.cfg.auto_approve, self.cfg.gate_actions_always,
                               self.cfg.tool_call_budget)
        record = {"run_id": self.run_id, "created": datetime.now(timezone.utc).isoformat(), "question": question,
                  "context": {**asdict(self._ctx), "now": now.isoformat()}, "config": self.cfg.public(),
                  "agent_commit": _git_commit(), "system_prompt_sha": _sha(SYSTEM_PROMPT),
                  "tools_sha": _sha(json.dumps(tool_definitions(), sort_keys=True)),
                  "versions": {p: version(p) for p in ["langgraph", "langgraph-checkpoint-sqlite", "langchain-core",
                                                       "langchain-anthropic"]}}
        (self.dir / "run.json").write_text(json.dumps(record, indent=1, default=str))
        self.bus.emit("run_started", thread_id=self.run_id, now=now.isoformat(), question=question,
                      model=f"{self.cfg.investigator.provider}:{self.cfg.investigator.model}",
                      auto_approve=self.cfg.auto_approve)
        self.bus.emit("phase", name="orient")
        return self._drive({"question": question})

    def resume(self, answers: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        """Resume a run paused at a gate, from a new process: the checkpointer has the state, run.json the
        context (LangGraph does not checkpoint runtime context)."""
        rec = json.loads((self.dir / "run.json").read_text())
        c = rec["context"]
        self._ctx = RunContext(c["run_id"], datetime.fromisoformat(c["now"]), c["auto_approve"],
                               c["gate_actions_always"], c["tool_call_budget"], c.get("max_rounds", 0))
        st = self.graph.get_state(self.config)
        if not st.interrupts:
            raise SystemExit(f"run {self.run_id} is not waiting at a gate")
        intr = st.interrupts[0]
        answers = answers or self.decider(intr.value["calls"])
        self.bus.emit("phase", name="resumed", interrupt_id=intr.id)
        return self._drive(Command(resume={intr.id: answers}))

    # ------------------------------------------------------------ the loop

    def _drive(self, inp: Any) -> dict[str, Any]:
        t_start = time.perf_counter()
        while True:
            try:
                if self.stream:
                    for mode, chunk in self.graph.stream(inp, self.config, context=self._ctx,
                                                         stream_mode=["messages", "updates", "custom"]):
                        self._map(mode, chunk)
                else:
                    self.graph.invoke(inp, self.config, context=self._ctx)
            except GraphRecursionError:
                # the graph cannot route to finalize after this is raised, so the runner renders the partial
                values = dict(self.graph.get_state(self.config).values)
                values.update(status="incomplete", report=partial_report(values, "recursion limit reached"))
                self.bus.emit("error", where="graph", message="recursion limit reached", next="partial report")
                return self._finish(values, time.perf_counter() - t_start)
            st = self.graph.get_state(self.config)
            if not st.interrupts:
                break
            intr = st.interrupts[0]
            if not self.stream:
                self.bus.emit("gate_pending", interrupt_id=intr.id, **intr.value)
            if self.stop_at_gate:
                self._persist_usage(st.values)
                self.bus.emit("run_paused", interrupt_id=intr.id, resume=f"uv run python -m spike resume {self.run_id}")
                return {"status": "waiting_for_gate", "run_id": self.run_id}
            answers = self.decider(intr.value["calls"])
            inp = Command(resume={intr.id: answers})
        return self._finish(st.values, time.perf_counter() - t_start)

    def _map(self, mode: str, chunk: Any) -> None:
        if mode == "messages":
            msg, meta = chunk
            if meta.get("langgraph_node") == "agent":
                r, t = reasoning_text(msg)
                if r:
                    self.bus.emit("thinking_delta", text=r, source="native")
                if t:
                    self.bus.emit("text_delta", text=t)
            return
        if mode == "custom":  # model_call, tool_started, tool_result (per call), hypothesis_update, budget
            data = dict(chunk)
            typ = data.pop("type")
            if typ == "model_call":
                data["cost_usd"] = self._cost(data)
            self.bus.emit(typ, **data)
            return
        for node, upd in chunk.items():
            if node == "__interrupt__":
                for intr in upd:
                    self.bus.emit("gate_pending", interrupt_id=intr.id, **intr.value)
            elif node == "agent":
                if not self._investigating:
                    self._investigating = True
                    self.bus.emit("phase", name="investigate")
                ai = upd["messages"][-1]
                for c in ai.tool_calls:
                    spec = TOOLS.get(c["name"])
                    self.bus.emit("tool_call_proposed", call_id=c["id"], tool=c["name"],
                                  kind=spec.kind if spec else "unknown", rationale=c["args"].get("rationale"),
                                  args={k: v for k, v in c["args"].items() if k != "rationale"})
                if any(c["name"] == "submit_report" for c in ai.tool_calls) or not ai.tool_calls:
                    self.bus.emit("phase", name="report")
            elif node in ("gate", "ask"):
                waits = {w["round"]: w["wait_ms"] for w in upd.get("gate_waits", [])}
                rows = upd["gate_log"]
                for d in rows:
                    wait = next(iter(waits.values())) if node == "ask" and waits else None
                    self.bus.emit("gate_decision", **d, wait_ms=wait)
            elif node == "tools":
                if upd.get("validation"):
                    self.bus.emit("validation", **upd["validation"])
            elif node == "finalize":
                self.bus.emit("report", report=upd["report"], status=upd["status"])

    def _cost(self, call: dict[str, Any]) -> float:
        """Priced by the model that served the call, so a verifier on another model is priced correctly.
        LangChain's input_tokens includes cached tokens; cache_creation is zeroed when a TTL split exists."""
        u = call.get("usage") or {}
        det = u.get("input_token_details") or {}
        read = det.get("cache_read", 0) or 0
        w5 = (det.get("cache_creation", 0) or 0) + (det.get("ephemeral_5m_input_tokens", 0) or 0)
        w1 = det.get("ephemeral_1h_input_tokens", 0) or 0
        uncached = max((u.get("input_tokens", 0) or 0) - read - w5 - w1, 0)
        p = self.cfg.price(call.get("model") or self.cfg.investigator.model)
        return round((uncached * p["input"] + read * p["cache_read"] + w5 * p["cache_write"]
                      + w1 * p.get("cache_write_1h", 2 * p["input"]) + (u.get("output_tokens", 0) or 0) * p["output"])
                     / 1e6, 6)

    def _persist_usage(self, values: dict[str, Any]) -> dict[str, Any]:
        """Built from the usage channel in state, so it is complete whether or not the run streamed, and across
        a pause and resume."""
        calls = [{**c, "cost_usd": self._cost(c)} for c in values.get("usage", [])]
        det = lambda c, k: ((c["usage"].get("input_token_details") or {}).get(k, 0) or 0)  # noqa: E731
        tot = {k: sum((c["usage"].get(k, 0) or 0) for c in calls) for k in ["input_tokens", "output_tokens"]}
        tot["cache_read"] = sum(det(c, "cache_read") for c in calls)
        tot["cache_write"] = sum(det(c, "cache_creation") + det(c, "ephemeral_5m_input_tokens")
                                 + det(c, "ephemeral_1h_input_tokens") for c in calls)
        tot["reasoning"] = sum(((c["usage"].get("output_token_details") or {}).get("reasoning", 0) or 0) for c in calls)
        tot["cost_usd"] = round(sum(c["cost_usd"] for c in calls), 6)
        tot["model_latency_ms"] = round(sum(c["latency_ms"] for c in calls), 1)
        (self.dir / "usage.json").write_text(json.dumps({"calls": calls, "totals": tot}, indent=1))
        return {"n": len(calls), **tot}

    def _finish(self, values: dict[str, Any], wall_s: float) -> dict[str, Any]:
        report = values.get("report")
        (self.dir / "report.json").write_text(json.dumps(report, indent=1))
        gl = {g["call_id"]: g for g in values.get("gate_log", [])}
        with (self.dir / "calls.jsonl").open("w") as f:
            for e in values.get("ledger", []):
                f.write(json.dumps({**e, "gate": gl.get(e["call_id"])}) + "\n")
        usage = self._persist_usage(values)
        self.bus.emit("phase", name="done")
        events = [json.loads(x) for x in (self.dir / "events.jsonl").read_text().splitlines()]
        t0 = datetime.fromisoformat(events[0]["t_wall"])
        counts: dict[str, int] = {}
        for e in events:
            counts[e["type"]] = counts.get(e["type"], 0) + 1
        gate: dict[str, int] = {}
        for g in values.get("gate_log", []):
            gate[f"{g['by']}:{g['decision']}"] = gate.get(f"{g['by']}:{g['decision']}", 0) + 1
        human_wait = sum(w["wait_ms"] for w in values.get("gate_waits", []))  # once per round, across processes
        summary = {"status": values.get("status"),
                   "wall_s_total": round((datetime.now(timezone.utc) - t0).total_seconds(), 3),
                   "wall_s_last_segment": round(wall_s, 3), "gate_wait_ms": round(human_wait, 1),
                   "model_calls": usage["n"], "tool_calls": values.get("tool_calls_used", 0), "gate": gate,
                   "validation": values.get("validation"), "tokens": {k: v for k, v in usage.items() if k != "n"},
                   "events": counts, "event_bytes": (self.dir / "events.jsonl").stat().st_size}
        (self.dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
        self.bus.emit("run_finished", status=values.get("status"), tool_calls=summary["tool_calls"],
                      model_calls=summary["model_calls"], tokens=summary["tokens"], wall_s=summary["wall_s_total"])
        return {"status": values.get("status"), "run_id": self.run_id, "report": report, "summary": summary}

    def close(self) -> None:
        self.jsonl.close()
        self.conn.close()
