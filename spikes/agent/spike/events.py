"""The event protocol. One small typed vocabulary that the terminal renders now, that a FastAPI SSE endpoint
can serve later unchanged (`event: <type>` / `data: <json>`), and that the run record stores as events.jsonl
so a run can be replayed without a model or a key.

The runner is the only code that knows LangGraph stream modes; everything downstream sees these events.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

EventType = Literal[
    "run_started",        # run_id, thread_id, now, model, config digest
    "phase",              # orient | investigate | report | done
    "thinking_delta",     # streamed reasoning text, normalised across providers
    "text_delta",         # streamed visible text
    "model_call",         # one finished model call: usage, cost, latency, stop reason
    "tool_call_proposed", # tool, args, rationale, kind (read | action | internal)
    "gate_pending",       # calls awaiting a human decision
    "run_paused",         # the run stopped at a gate; resume later (not run_finished, so SSE clients stay)
    "gate_decision",      # approved | rejected, reason, by (auto | human), wait time
    "tool_started",
    "tool_result",        # status, first line, handles, native query, window, size, latency
    "hypothesis_update",  # the investigation board changed
    "verifier_verdict",   # designed, not built in the spike
    "budget",             # tool calls used and left
    "report",             # validated report
    "validation",         # report validator outcome (unknown handles, retry)
    "error",              # where, message, what happens next
    "run_finished",       # status and totals
]


@dataclass
class Event:
    type: EventType
    data: dict[str, Any]
    run_id: str = ""
    seq: int = 0
    t_wall: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
    t_mono: float = field(default_factory=time.perf_counter)

    def to_json(self) -> str:
        d = asdict(self)
        d.pop("t_mono")
        return json.dumps(d, default=str, ensure_ascii=False)

    def to_sse(self) -> str:
        """What a FastAPI StreamingResponse would yield per event."""
        return f"id: {self.seq}\nevent: {self.type}\ndata: {self.to_json()}\n\n"


class Sink(Protocol):
    def emit(self, ev: Event) -> None: ...


class JsonlSink:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = path.open("a", encoding="utf-8")

    def emit(self, ev: Event) -> None:
        # thinking and text deltas are many and small; keep them, they are the replayable trace
        self._f.write(ev.to_json() + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


class Bus:
    """Numbers events and fans them out to sinks."""

    def __init__(self, run_id: str, sinks: list[Sink], start_seq: int = 0):
        self.run_id = run_id
        self.sinks = sinks
        self.seq = start_seq
        self.counts: dict[str, int] = {}

    def emit(self, type: EventType, **data: Any) -> Event:
        self.seq += 1
        ev = Event(type=type, data=data, run_id=self.run_id, seq=self.seq)
        self.counts[type] = self.counts.get(type, 0) + 1
        for s in self.sinks:
            s.emit(ev)
        return ev


def read_events(path: Path) -> list[Event]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        out.append(Event(type=d["type"], data=d["data"], run_id=d["run_id"], seq=d["seq"], t_wall=d["t_wall"]))
    return out
