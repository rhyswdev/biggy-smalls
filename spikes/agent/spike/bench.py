"""Framework overheads, measured with the scripted model (zero model latency), so every millisecond here is
LangGraph, the checkpointer, the gate and the event mapping. Token overheads are characters / 4, an estimate."""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import load_config
from .graph import tool_definitions
from .runner import Runner, headless
from .tools import TOOLS

Q = "The nightly report-export job failed at 02:10 UTC. What happened?"
NOW = datetime.fromisoformat("2026-03-02T02:20:00+00:00")


def _one(cfg, stream: bool, n: int) -> dict[str, Any]:
    walls, steps, db, ev_bytes, ev_n = [], [], [], [], []
    for i in range(n):
        r = Runner(cfg, f"bench-{stream}-{cfg.gate_actions_always}-{i}", stream=stream, decider=headless("approve"))
        t0 = time.perf_counter()
        r.start(Q, NOW)
        walls.append((time.perf_counter() - t0) * 1000)
        steps.append(sum(1 for _ in r.graph.get_state_history(r.config)))
        r.close()
        db.append((r.dir / "checkpoints.sqlite").stat().st_size)
        ev_bytes.append((r.dir / "events.jsonl").stat().st_size)
        ev_n.append(sum(1 for _ in (r.dir / "events.jsonl").open()))
    walls = walls[1:]  # first run pays imports and SQLite warm-up
    return {"median_ms": round(statistics.median(walls), 1), "p90_ms": round(sorted(walls)[int(0.9 * len(walls)) - 1], 1),
            "checkpoints": steps[0], "sqlite_kb": round(db[0] / 1024, 1),
            "events": ev_n[0], "event_kb": round(ev_bytes[0] / 1024, 1)}


def bench(n: int = 21) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as d:
        base = load_config(runs_dir=Path(d))
        no_gate = replace(base, gate_actions_always=False)        # auto-approve everything: no interrupt
        with_gate = replace(base, gate_actions_always=True)       # the rollback interrupts and resumes once
        res = {
            "invoke_no_interrupt": _one(no_gate, stream=False, n=n),
            "stream_no_interrupt": _one(no_gate, stream=True, n=n),
            "stream_one_interrupt": _one(with_gate, stream=True, n=n),
        }
        # token overheads from the last run's record
        run = sorted(Path(d).glob("bench-True-True-*"))[-1]
        calls = [json.loads(x) for x in (run / "calls.jsonl").read_text().splitlines()]
    with_r = len(json.dumps(tool_definitions()))
    without_r = len(json.dumps([t.model_schema(with_rationale=False) for t in TOOLS.values()]))
    gated = [c for c in calls if c["kind"] != "internal"]
    res["streaming_overhead_ms"] = round(res["stream_no_interrupt"]["median_ms"] - res["invoke_no_interrupt"]["median_ms"], 1)
    res["interrupt_resume_overhead_ms"] = round(res["stream_one_interrupt"]["median_ms"] - res["stream_no_interrupt"]["median_ms"], 1)
    res["tokens_est"] = {
        "tool_schemas_total": with_r // 4,
        "rationale_in_schemas": (with_r - without_r) // 4,
        "rationale_per_call_mean": round(statistics.mean(len(c["rationale"] or "") for c in gated) / 4, 1),
        "rejection_message": len("REJECTED by human: <reason>. The call was not run. Re-plan; do not repeat it unchanged.") // 4,
        "budget_note_per_round": len("\n[budget: 7 of 12 tool calls used, 5 left]") // 4,
    }
    return res
