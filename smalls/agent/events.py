"""The event protocol: one small vocabulary that the terminal renders, that a server can send unchanged as
server-sent events, and that the run record stores as `events.jsonl`, so a run replays with no model and
no key (docs/research/agent-design.md section 3).

The runner is the only code that knows the graph's stream modes; everything downstream sees these events.

Hardening (agent-design section 8.2): one writer numbers the events, so `seq` never repeats; a sink that
fails is dropped with a note and the run goes on; the reader skips a torn last line, which is what a
crashed run leaves behind and exactly the run someone wants to replay.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

EventType = Literal[
    "run_started",          # run id, now, profile, policy
    "phase",                # orient | sweep | investigate | report | verify | done | resumed
    "heartbeat",            # the model has been running for n seconds
    "thinking_delta",       # streamed reasoning text, the same shape for every provider; coalesced
    "thinking",             # the whole reasoning of one model call, once
    "text_delta",           # streamed visible text
    "model_call",           # one finished model call: usage, cost, latency, stop reason, attempt
    "tool_call_proposed",   # tool, kind, args, rationale {tests, expect}, who proposed it
    "gate_pending",         # calls waiting for a person
    "gate_decision",        # approved | rejected | deferred, reason, by {who, via}, wait
    "run_paused",           # the run stopped at a gate; not run_finished, so a stream stays open
    "tool_started",
    "tool_result",          # status, shown and referenced handles, native query, window, size, latency
    "hypothesis_update",    # the board changed
    "board_status",         # ready or not, stale ids, budget
    "stuck",                # nothing is coming back; the commander may want to steer
    "ic_note",              # a note from the incident commander
    "budget",               # calls used and left
    "draft_report",         # a submitted report, before the verifier
    "verifier_verdict",
    "validation",           # the validator's findings and what it dropped
    "report",               # the final report
    "cache_ratio_low",      # cached reads fell: cost is about to climb
    "error",                # where, message, what happens next
    "run_finished",         # status and totals
]


@dataclass
class Event:
    type: str
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
        """What a server would send per event."""
        return f"id: {self.seq}\nevent: {self.type}\ndata: {self.to_json()}\n\n"


class Sink(Protocol):
    def emit(self, ev: Event) -> None: ...


class JsonlSink:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = path.open("a", encoding="utf-8")

    def emit(self, ev: Event) -> None:
        self._f.write(ev.to_json() + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


class Bus:
    """Numbers events and fans them out. The one place `seq` is assigned."""

    def __init__(self, run_id: str, sinks: list[Sink], start_seq: int = 0):
        self.run_id = run_id
        self.sinks = list(sinks)
        self.seq = start_seq
        self.counts: dict[str, int] = {}
        self.dropped: list[str] = []         # sinks removed after they raised

    def emit(self, type: str, **data: Any) -> Event:
        self.seq += 1
        ev = Event(type=type, data=data, run_id=self.run_id, seq=self.seq)
        self.counts[type] = self.counts.get(type, 0) + 1
        for sink in list(self.sinks):
            try:
                sink.emit(ev)
            except Exception as error:      # a renderer that breaks must not take the run with it
                self.sinks.remove(sink)
                self.dropped.append(f"{type(sink).__name__}: {type(error).__name__}: {error}")
        return ev


def read_events(path: Path) -> list[Event]:
    """Every whole event in a log. A torn line is skipped, not raised on."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(line)
            out.append(Event(type=d["type"], data=d["data"], run_id=d["run_id"], seq=d["seq"], t_wall=d["t_wall"]))
        except (ValueError, KeyError, TypeError):
            continue
    return out


def last_seq(path: Path) -> int:
    """The highest sequence number in a log, so a resumed run goes on numbering where the last one stopped."""
    return max((e.seq for e in read_events(path)), default=0) if path.exists() else 0
