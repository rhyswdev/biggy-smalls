"""Drives the graph, maps the framework's stream parts to the event protocol, and writes the run record.

This is the only module that knows about stream modes. A server would call `Runner.start()` and forward
each event; the terminal renders the same events; the run record stores them.

Stream modes used, as typed v2 parts `{type, ns, data}`: `messages` (token chunks: reasoning and text
deltas), `updates` (node outputs: proposed calls, gate decisions, validation, the report; and the
`__interrupt__` entry that carries a pending gate) and `custom` (what nodes write with the stream writer:
model-call usage, tool started and finished, board changes, the budget).

One process holds one store and one clock. A resume in a new process rebuilds the tools, which is
deterministic, and takes `now` and the rest of the runtime context from `run.json`, because the framework
does not checkpoint it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from langchain_core.messages import HumanMessage

from smalls.agent import carry, gate, metrics, prompts, verifier
from smalls.agent.config import SCRIPTED, Config
from smalls.agent.events import Bus, Sink
from smalls.agent.graph import FOLLOWUP, FOLLOWUP_TURNS, NOTE_ROW, build_graph, call_limit, iso, partial_report, rounds, turn_limit
from smalls.agent.internal_tools import toolset
from smalls.agent.models import ModelCallError, import_local, input_limit, make_model, provider_tools, reasoning_text
from smalls.agent.record import FileBackend, RunRecord, SqliteBackend, commit, cost, key_names, new_run_id, scrub, sha, usage_totals, versions
from smalls.contracts.handles import ic_note_handle
from smalls.agent.state import RunContext
from smalls.contracts.envelope import Envelope
from smalls.tools import registry
from smalls.tools.registry import ToolContext, ToolSpec

Decider = Callable[[list[dict[str, Any]]], dict[str, dict[str, Any]]]
THINKING_FLUSH_S = 0.2      # reasoning deltas are coalesced: one event at most this often, and the whole text once per call
STOPPED = "the incident commander stopped the run"


@dataclass
class Toolbox:
    """The read tools a run investigates with, and how to build their context. Injected, so offline tests
    run the agent over a fixture."""

    read_tools: dict[str, ToolSpec]
    inventory: Callable[[ToolContext], Envelope]
    context: Callable[[datetime, str], ToolContext]
    describe: dict[str, Any] = field(default_factory=dict)      # what run.json says about it


def module_toolbox(path: str) -> Toolbox:
    """A toolbox from a module that exposes `registry()` and `inventory(ctx)`, such as the test fixture."""
    module = import_local(path)
    return Toolbox(module.registry(), module.inventory, lambda now, run_id: ToolContext(store=None, now=now, tags=False, run_id=run_id),
                   {"registry": path, "bundle": None, "tags": False})


def bundle_toolbox(bundle: Path | str, tags: bool | None = None) -> Toolbox:
    """The real tools over a bundle. The store is built once, and its clock is set when the context is."""
    from smalls.tools import inventory, store

    tags = store.tags_enabled() if tags is None else tags
    built = store.build(bundle, tags=tags)

    def context(now: datetime, run_id: str) -> ToolContext:
        built.set_now(now)
        return ToolContext(store=built, now=now, tags=tags, run_id=run_id)

    return Toolbox(registry.registry(tags=tags), inventory.build, context,
                   {"registry": "bundle", "bundle": built.bundle.name, "tags": tags})


def resume_hint(run_id: str) -> str:
    """The command that resumes a paused run, for the way this run was started."""
    if os.environ.get("SMALLS_RUNNER") == "compose":
        return f"docker compose run --rm smalls smalls resume {run_id}"
    return f"uv run smalls resume {run_id}"


class Runner:
    def __init__(self, cfg: Config, toolbox: Toolbox, run_id: str | None = None, *, sinks: list[Sink] | None = None,
                 decider: Decider | None = None, stop_at_gate: bool = False, attended: bool = False, eval_run: bool = False,
                 script: Any = None, durability: str | None = None, fallback_script: Any = None, offered: tuple[str, ...] | None = None,
                 single_call: bool = False, verifier_scripts: dict[str, Any] | None = None,
                 backend: FileBackend | SqliteBackend | None = None, checkpointer: sqlite3.Connection | None = None,
                 via: str | None = None):
        """`backend` holds the run record: files under `cfg.runs_dir` by default, or a SQLite database
        (seam S8). `checkpointer` is a connection for the checkpointer, which the caller owns; without one
        the backend opens the run's own. `via` says where a person answers this run's gates (`cli` or
        `web`, seam S1); by default the terminal when the run is attended."""
        self.cfg = cfg
        self.toolbox = toolbox
        self.backend = backend or FileBackend(cfg.runs_dir)
        self.record = RunRecord(cfg.runs_dir, run_id or new_run_id(), backend=self.backend)
        self.run_id = self.record.run_id
        self.dir = self.record.dir
        self.events_sink = self.record.events_sink()
        self.bus = Bus(self.run_id, [self.events_sink, *(sinks or [])], start_seq=self.record.last_seq())
        self.attended = attended
        self.via = via or ("cli" if attended else "headless")
        self.eval_run = eval_run
        self.stop_at_gate = stop_at_gate
        self.durability = durability
        self.offered = tuple(offered) if offered else None
        self.single_call = single_call
        self.tools = toolset(toolbox.read_tools)
        # Who answers a gate when nobody is asked: an eval run approves and records; any other unattended
        # run takes the configured answer, which defers by default (J15).
        self.decider = decider or gate.headless("approve" if eval_run else cfg.headless)
        gate.guard_headless_approval(getattr(self.decider, "choice", ""), self.tools)
        spec = cfg.investigator
        self.script = script if script is not None else spec.params.get("script")
        if spec.provider == SCRIPTED:
            spec = spec.with_params(script=self.script)
        self.spec = spec
        self.model, bind = make_model(spec, timeout_s=cfg.timeout_s, cache_ttl="1h" if attended else None)
        self.cached = "cache_control" in bind
        fallback = None
        if cfg.fallback_profile and cfg.fallback_profile != spec.profile:        # used once, when the model declines to continue
            other = cfg.profiles[cfg.fallback_profile]
            other = other.with_params(script=fallback_script or self.script) if other.provider == SCRIPTED else other
            other_model, other_bind = make_model(other, timeout_s=cfg.timeout_s)
            fallback = (other_model, other.provider, other_bind)
        self.verifier_scripts = dict(verifier_scripts or {})
        self.verify, self.verifier_profiles = self._verifier()
        self._own_conn = checkpointer is None
        self.conn = checkpointer if checkpointer is not None else self.backend.checkpointer(self.run_id)
        self.graph = build_graph(self.model, toolbox.read_tools, toolbox.inventory, provider=spec.provider, bind_kwargs=bind,
                                 checkpointer=SqliteSaver(self.conn), strict=bool(spec.params.get("strict_tools", True)), fallback=fallback,
                                 offered=self.offered, verify=self.verify)
        self.index = self.backend.index()
        self._thinking: list[str] = []          # this model call's reasoning, whole
        self._unsent: list[str] = []            # the part not yet emitted as a delta
        self._last_flush = 0.0
        self.config = {"configurable": {"thread_id": self.run_id}, "recursion_limit": 6 * (cfg.call_budget + cfg.extra_turns) + 40}
        self._ctx: RunContext | None = None
        self._investigating = False
        self._carry: dict[str, Any] | None = None
        self._asking = False                    # a person is being asked at a gate
        self._stop = threading.Event()          # set from another thread to stop at the next stream part (S5)
        self._inbox: list[dict[str, Any]] = []  # notes added while the run runs, until a round takes them (S4)
        self._auto: bool | None = None          # the auto-approve switch when set during the run (S6)
        self._inbox_lock = threading.Lock()

    def _verifier(self) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """The verifier's passes, each a model bound to `submit_verdict` with its prompt, and the profile
        behind each. A live run takes the two profiles the config names; either may be left empty to cut
        that pass. A scripted run verifies only with the scripts it was given, so a run that brings none
        is not verified. A single-call run never is."""
        cfg = self.cfg
        if not cfg.verifier.get("enabled") or self.single_call:
            return None, {}
        definition = [registry.definition(verifier.spec())]

        def bound(spec: Any) -> Any:
            try:
                model, bind = make_model(spec, timeout_s=cfg.timeout_s)
            except ImportError as missing:
                raise SystemExit(f"the verifier profile {spec.profile!r} needs a client that is not installed ({missing.name}). "
                                 "Install its dependency group, or name another profile under [verifier].") from None
            return model.bind_tools(provider_tools(definition, spec.provider, strict=bool(spec.params.get("strict_tools", True))), **bind)

        passes: dict[str, Any] = {}
        profiles: dict[str, str] = {}
        for which, key in ((verifier.CLAIMS, "claims_profile"), (verifier.CONTRARIAN, "contrarian_profile")):
            if self.spec.provider == SCRIPTED:
                script = self.verifier_scripts.get(which)
                spec = self.spec.with_params(script=script) if script else None
            else:
                name = cfg.verifier.get(key)
                if name and name not in cfg.profiles:
                    raise SystemExit(f"[verifier] {key} names no profile: {name!r}")
                spec = cfg.profiles[name] if name else None
            if spec is not None:
                passes[which] = (bound(spec), prompts.load(f"verifier_{which}"))
                profiles[which] = spec.profile
        if not passes:
            return None, {}
        if verifier.CLAIMS in passes:       # the re-check after a revision is claim support again, on what changed
            passes[verifier.RECHECK] = passes[verifier.CLAIMS]
        return {**passes, "digest_tokens": int(cfg.verifier.get("digest_tokens", 1500))}, profiles

    # -- entry points --------------------------------------------------------------------------------------

    def start(self, trigger: str, now: datetime, *, checkpoint: str | None = None, arm: str = "C", channel: str | None = None,
              carry_from: str | None = None) -> dict[str, Any]:
        """`carry_from` names an earlier run of the same incident: this run starts from its board (J8)."""
        self._carry = carry.load(self.backend, carry_from) if carry_from else None
        if self._carry and self._carry["as_of"] >= iso(now):
            raise SystemExit(f"cannot carry forward from {carry_from}: its board is as of {self._carry['as_of']}, which is not before this run's now")
        self._ctx = self._context(now, checkpoint=checkpoint, channel=channel)
        definitions = [registry.definition(spec) for spec in self.tools.values() if self.offered is None or spec.name in self.offered]
        self.record.write_json("run.json", {
            "run_id": self.run_id, "created": gate.now_iso(), "arm": arm, "checkpoint": checkpoint, "now": iso(now), "trigger": trigger,
            **self.toolbox.describe, "profile": self.spec.profile, "provider": self.spec.provider, "model": self.spec.model,
            "script": self.script if isinstance(self.script, str) else None, "commit": commit(),
            "prompt_sha": sha("\n".join(prompts.load(name) for name in prompts.NAMES)),
            "tools_sha": sha(json.dumps(definitions, sort_keys=True)), "sweep": self.cfg.sweep, "versions": versions(),
            "key_variables": key_names(), "runner": os.environ.get("SMALLS_RUNNER", "host"), "eval_run": self.eval_run,
            "durability": self.durability or "async", "read_tools": list(self.toolbox.read_tools), "offered": list(self.offered) if self.offered else None,
            "verifier": self.verifier_profiles or None,
            "carried_from": {"run_id": self._carry["run_id"], "as_of": self._carry["as_of"]} if self._carry else None,
            "verifier_scripts": {k: v for k, v in self.verifier_scripts.items() if isinstance(v, str)} or None,
            "config": self.cfg.public(),
            "context": {"now": now.isoformat(), "checkpoint": checkpoint, "channel": channel, "call_budget": self._ctx.call_budget,
                        "extra_turns": self._ctx.extra_turns, "revision_calls": self._ctx.revision_calls,
                        "stale_rounds": self._ctx.stale_rounds, "stuck_empty": self._ctx.stuck_empty,
                        "context_fraction": self._ctx.context_fraction, "max_input_tokens": self._ctx.max_input_tokens,
                        "max_tokens": self._ctx.max_tokens, "auto_approve": self._ctx.auto_approve, "sweep": self._ctx.sweep,
                        "verifier": self._ctx.verifier, "single_call": self._ctx.single_call}})
        self.bus.emit("run_started", now=iso(now), trigger=trigger, checkpoint=checkpoint, profile=self.spec.profile,
                      auto_approve=self._ctx.auto_approve, attended=self.attended, tags=self.toolbox.describe.get("tags"))
        self.bus.emit("phase", name="orient")
        self._meta = {"checkpoint": checkpoint, "arm": arm, "now": iso(now)}
        self._index("running")
        return self._drive({"trigger": trigger})

    def resume(self, answers: dict[str, dict[str, Any]] | None = None, note: str | None = None) -> dict[str, Any]:
        """Resume a paused run from a new process: the checkpointer has the state, and `run.json` has the
        context. A run pauses at a gate, or where a provider failure or an interrupt stopped it; either
        way it goes on from its last checkpoint. `note` is a note from the incident commander: it does
        not block, and the model sees it at its next round as a citable row."""
        run = self.record.read_json("run.json")
        saved = run["context"]
        self._meta = {"checkpoint": run.get("checkpoint"), "arm": run.get("arm"), "now": run.get("now")}
        self._ctx = self._context(datetime.fromisoformat(saved["now"]), saved=saved)
        restarted = {**self.config, "configurable": {"thread_id": f"{self.run_id}.1"}}
        if self.graph.get_state(restarted).values:        # the run was restarted from its board: its live thread is the new one (J16)
            self.config = restarted
        state = self.graph.get_state(self.config)
        notes = self._take_inbox()      # notes added while it ran that no round took, then this one
        if note:
            at = gate.now_iso()
            notes.append({"handle": ic_note_handle(self.run_id, at, note), "text": note, "submitted_at": at, "by": "incident commander", "call_id": None})
        for row in notes:
            self.bus.emit("ic_note", **row)
        if state.interrupts:
            pending = state.interrupts[0]
            answers = answers or self.decider(pending.value["calls"])
            self.bus.emit("phase", name="resumed", interrupt_id=pending.id)
            self._index("running")
            return self._drive(Command(resume={pending.id: answers}, update={"ic_notes": notes} if notes else None))
        if not (state.next or state.tasks):
            raise SystemExit(f"run {self.run_id} is not waiting at a gate and has nothing left to run")
        if notes:
            self.graph.update_state(self.config, {"ic_notes": notes})
        self.bus.emit("phase", name="resumed", after=list(state.next) or [task.name for task in state.tasks])
        self._index("running")
        return self._drive(None)

    def reopen(self, note: str | None = None, *, calls: int | None = None) -> dict[str, Any]:
        """Continue a finished run with a follow-up from the incident commander (seam S3). The note becomes a
        citable `ic:note` row and the run goes back to the investigator on the same thread, with the same
        conversation and board, and `calls` more gated calls (a revision's worth by default). Neither the
        orientation nor the opening sweep runs again. A turn with no tool call ends the follow-up, and its
        text is the answer: the earlier report stands. A report submitted again goes through the validator
        and, when the run is verified, the changed-claims re-check."""
        run = self.record.read_json("run.json")
        saved = run["context"]
        self._meta = {"checkpoint": run.get("checkpoint"), "arm": run.get("arm"), "now": run.get("now")}
        self._ctx = self._context(datetime.fromisoformat(saved["now"]), saved=saved)
        restarted = {**self.config, "configurable": {"thread_id": f"{self.run_id}.1"}}
        if self.graph.get_state(restarted).values:
            self.config = restarted
        state = self.graph.get_state(self.config)
        values = dict(state.values)
        if state.interrupts or state.next or state.tasks:
            raise SystemExit(f"run {self.run_id} has not finished: resume it instead")
        if values.get("status") not in ("completed", "incomplete") or not values.get("report"):
            raise SystemExit(f"run {self.run_id} has no finished report to follow up on (status {values.get('status')})")
        calls = self.cfg.revision_calls if calls is None else calls
        rows = self._take_inbox()       # notes added while it ran that no round took, then this one (S4)
        if note:
            at = gate.now_iso()
            rows.append({"handle": ic_note_handle(self.run_id, at, note), "text": note, "submitted_at": at, "by": "incident commander", "call_id": None})
        if not rows:
            raise SystemExit("a follow-up needs a note")
        row, at = rows[-1], rows[-1]["submitted_at"]
        round_now, used = rounds(values), values.get("calls_used", 0)
        followup = {"n": len(values.get("followups") or []) + 1, "note": row["handle"], "notes": [r["handle"] for r in rows], "round": round_now, "calls": calls,
                    "calls_until": used + calls, "turns_until": round_now + calls + FOLLOWUP_TURNS, "report": values["report"],
                    "status": values["status"], "stop_reason": values.get("stop_reason"), "started_at": at}
        text = FOLLOWUP.format(row="\n".join(NOTE_ROW.format(handle=r["handle"], at=r["submitted_at"], text=r["text"]) for r in rows),
                               calls=calls, turns=calls + FOLLOWUP_TURNS)
        for r in rows:
            self.bus.emit("ic_note", **r)
        self.bus.emit("phase", name="followup", note=row["handle"], calls=calls, turns=calls + FOLLOWUP_TURNS)
        # Entered as if the verifier had sent the report back: the next node is the investigator, and the
        # route to it needs no runtime context.
        self.graph.update_state(self.config, {
            "messages": [HumanMessage(text)], "ic_notes": rows, "notes_shown": len(values.get("ic_notes") or []) + len(rows), "followup": followup,
            "first_report": values["report"], "report": None, "verify_call": None, "stop_reason": None, "status": "investigating",
            "report_attempts": 0, "proposed": []}, as_node="verify")
        self._investigating = True
        self._index("running")
        return self._drive(None)

    def _context(self, now: datetime, *, checkpoint: str | None = None, channel: str | None = None, saved: dict[str, Any] | None = None) -> RunContext:
        cfg, saved = self.cfg, saved or {}
        return RunContext(
            run_id=self.run_id, now=now, tool_ctx=self.toolbox.context(now, self.run_id),
            call_budget=saved.get("call_budget", cfg.call_budget), extra_turns=saved.get("extra_turns", cfg.extra_turns),
            revision_calls=saved.get("revision_calls", cfg.revision_calls), stale_rounds=saved.get("stale_rounds", cfg.stale_rounds),
            stuck_empty=saved.get("stuck_empty", cfg.stuck_empty), context_fraction=saved.get("context_fraction", cfg.context_fraction),
            max_input_tokens=saved.get("max_input_tokens", input_limit(self.spec, self.model)),
            max_tokens=saved.get("max_tokens", int(self.spec.params.get("max_tokens") or 0)),
            auto_approve=saved.get("auto_approve", cfg.auto_approve), via=self.via,
            checkpoint=saved.get("checkpoint", checkpoint), channel=saved.get("channel", channel),
            sweep=saved.get("sweep", dict(cfg.sweep)), verifier=bool(self.verify) and saved.get("verifier", True),
            single_call=saved.get("single_call", self.single_call), carry=self._carry, evidence=self.record.evidence,
            read_evidence=lambda: self.record.read_jsonl("evidence.jsonl"), inbox=self.pending_notes, auto_approve_now=self._auto_now)

    def add_note(self, text: str) -> dict[str, Any]:
        """A note from the incident commander while the run is running (seam S4). It does not block: the next
        round's results carry it to the model as a citable row, and the runner emits `ic_note` when it does.
        A note that no round took before the run paused or finished stays in `pending_notes()`, and a
        resume or a follow-up delivers it. Safe to call from any thread."""
        at = gate.now_iso()
        note = {"handle": ic_note_handle(self.run_id, at, text), "text": text, "submitted_at": at, "by": "incident commander", "call_id": None}
        with self._inbox_lock:
            self._inbox.append(note)
        return note

    def pending_notes(self) -> list[dict[str, Any]]:
        with self._inbox_lock:
            return list(self._inbox)

    def _take_inbox(self) -> list[dict[str, Any]]:
        with self._inbox_lock:
            taken, self._inbox = self._inbox, []
        return taken

    def _delivered(self, handles: set[str]) -> None:
        with self._inbox_lock:
            self._inbox = [n for n in self._inbox if n["handle"] not in handles]

    def set_auto_approve(self, on: bool) -> None:
        """Turn auto-approval of reads on or off while the run is running or paused (seam S6). The gate reads
        the switch once per round, so it applies from the next round, never to a card already asked.
        Actions always ask. Safe to call from any thread."""
        self._auto = bool(on)
        self.bus.emit("auto_approve", on=self._auto)

    def _auto_now(self) -> bool:
        return self._auto if self._auto is not None else bool(self._ctx and self._ctx.auto_approve)

    def request_stop(self) -> None:
        """Ask a running run to stop (seam S5). It is checked between stream parts, so the run stops within a
        token or a tool call: the node that was running is dropped and runs again on resume, and the run
        ends `paused`, with its partial board as the report, resumable from its last checkpoint. Safe to
        call from any thread, and a no-op on a run that is not running."""
        self._stop.set()

    # -- the loop ------------------------------------------------------------------------------------------

    def _heartbeat(self, stop: threading.Event) -> None:
        """While the run is working and has shown nothing for `heartbeat_s`, say so, and again each time
        that long passes. Never while a person is being asked: the silence is theirs then."""
        every = self.cfg.heartbeat_s
        if every <= 0:
            return
        last_beat = None        # the last heartbeat of the silence that is going on now
        while not stop.wait(min(every / 4, 1.0)):
            quiet = time.monotonic() - self.bus.last_news
            if self._asking or quiet < every:
                last_beat = None
            elif last_beat is None or time.monotonic() - last_beat >= every:
                last_beat = time.monotonic()
                self.bus.emit("heartbeat", seconds=round(quiet))

    def _drive(self, inp: Any) -> dict[str, Any]:
        stop = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(stop,), daemon=True)
        beat.start()
        try:
            return self._drive_loop(inp)
        finally:
            stop.set()
            beat.join(timeout=2)

    def _drive_loop(self, inp: Any) -> dict[str, Any]:
        started = time.perf_counter()
        extra = {"durability": self.durability} if self.durability else {}
        while True:
            try:
                for part in self.graph.stream(inp, self.config, context=self._ctx, stream_mode=["messages", "updates", "custom"],
                                              version="v2", **extra):
                    self._map(part["type"], part["data"])
                    if self._stop.is_set():
                        break
            except (Exception, KeyboardInterrupt) as failure:
                if not isinstance(failure, GraphRecursionError):
                    return self._stopped(failure, time.perf_counter() - started)
                # The graph cannot route to finalize once this is raised, so the runner renders the partial.
                values = dict(self.graph.get_state(self.config).values)
                values.update(status="incomplete", stop_reason="the recursion limit was reached",
                              report=partial_report(values, "the recursion limit was reached"))
                self.bus.emit("error", where="graph", message="recursion limit reached", next="partial report")
                self.bus.emit("report", report=values["report"], status="incomplete")
                return self._finish(values, time.perf_counter() - started)
            finally:
                self._flush_thinking()
            state = self.graph.get_state(self.config)
            if self._stop.is_set():
                self._stop.clear()
                if state.next or state.tasks or state.interrupts:
                    return self._halted(dict(state.values), time.perf_counter() - started)
            if not state.interrupts and state.values.get("restart_pending"):
                inp = self._restart(dict(state.values))
                continue
            if not state.interrupts:
                break
            pending = state.interrupts[0]
            if self.stop_at_gate:
                values = dict(state.values)
                values["status"] = "paused"
                self._write_derived(values, time.perf_counter() - started)
                self._index("paused", values)
                self.bus.emit("run_paused", interrupt_id=pending.id, resume=resume_hint(self.run_id))
                return {"status": "paused", "run_id": self.run_id}
            self._asking = True
            try:
                answers = self.decider(pending.value["calls"])
            finally:
                self._asking = False
                self.bus.last_news = time.monotonic()
            inp = Command(resume={pending.id: answers})
        return self._finish(dict(state.values), time.perf_counter() - started)

    def _restart(self, values: dict[str, Any]) -> None:
        """Restart the conversation from the board (J16): a new thread, `<run_id>.<n>`, seeded with every
        channel except the messages, whose first turn is the first-turn template with the board as it
        stands. The run enters at the sweep, so the opening sweep follows the first turn as it did before.
        The ledger, the board, the budget and the usage all carry over; the repeat index starts again."""
        ctx = self._ctx
        n = values.get("restarts", 0) + 1
        hypotheses = values.get("hypotheses") or {}
        cited = {h for hyp in hypotheses.values() for name in carry.LISTS for h in hyp.get(name) or []}
        cited |= {h for row in values.get("timeline") or [] for h in row.get("evidence") or []}
        rows = {r["handle"]: r["text"].split("\n", 1)[0] for r in self.record.read_jsonl("evidence.jsonl") if r["handle"] in cited}
        round_now = rounds(values)
        first = prompts.first_turn(
            now=iso(ctx.now), inventory=self.toolbox.inventory(ctx.tool_ctx).text, trigger=values.get("trigger", ""),
            budget=f"{call_limit(values, ctx) - values.get('calls_used', 0)} tool calls, {turn_limit(values, ctx) - round_now} model turns",
            prior_board=carry.render(hypotheses, values.get("timeline") or [], values.get("open_questions") or [], head=carry.RESTARTED, rows=rows))
        seeded = {k: v for k, v in values.items() if k not in ("messages", "proposed", "decisions", "pending", "restart_pending")}
        seeded.update(messages=[HumanMessage(first)], hypotheses={hid: {**h, "_seed": True} for hid, h in hypotheses.items()}, restarts=n,
                      restart_round=round_now, restart_pending=False, proposed=[], status="investigating")
        thread = f"{self.run_id}.{n}"
        self.config = {**self.config, "configurable": {"thread_id": thread}}
        self.graph.update_state(self.config, seeded, as_node="orient")
        self.bus.emit("restart", thread=thread, round=round_now, reason="the context limit was reached", first_turn_chars=len(first))
        return None

    def _halted(self, values: dict[str, Any], wall_s: float) -> dict[str, Any]:
        """A run the commander stopped: a pause like any other, with the partial board as its report."""
        values.update(status="paused", stop_reason=STOPPED)
        values.setdefault("as_of", iso(self._ctx.now))
        if not values.get("report"):
            values["report"] = partial_report(values, STOPPED)
        self.bus.emit("report", report=values["report"], status="paused")
        summary = self._write_derived(values, wall_s)
        self._index("paused", values)
        self.bus.emit("run_paused", resume=resume_hint(self.run_id), reason=STOPPED, stopped=True)
        return {"status": "paused", "run_id": self.run_id, "report": values["report"], "summary": summary, "stopped": True}

    def _stopped(self, failure: BaseException, wall_s: float) -> dict[str, Any]:
        """A run that stopped on a failure: a provider error after its retries, an interrupt, a fault. The
        checkpoint is intact, so this is a pause when anything is left to run: the record is written in
        full, with the partial report rendered from the board, and the reader gets a resume line."""
        model_error = isinstance(failure, ModelCallError)
        kind = failure.kind if model_error else type(failure).__name__
        where = "interrupt" if isinstance(failure, KeyboardInterrupt) else "model" if model_error else "run"
        message = str(failure) if model_error else scrub(f"{kind}: {failure}")[:300]
        try:
            state = self.graph.get_state(self.config)
            # A node that was running when an interrupt arrived may still have finished: then nothing is
            # "next", but its task holds writes that a resume applies. Either way there is something to resume.
            values, resumable = dict(state.values), bool(state.next or state.tasks)
        except Exception:
            values, resumable = {}, False
        why = "the run was interrupted" if where == "interrupt" else f"the {where} failed ({kind})"
        values.update(status="paused" if resumable else "failed", stop_reason=why)
        values.setdefault("as_of", iso(self._ctx.now))
        if not values.get("report"):
            values["report"] = partial_report(values, why)
        self.bus.emit("error", where=where, kind=kind, message=message, next="resume" if resumable else "nothing: no checkpoint to resume from")
        self.bus.emit("report", report=values["report"], status=values["status"])
        summary = self._write_derived(values, wall_s)
        self._index(values["status"], values)
        if resumable:
            self.bus.emit("run_paused", resume=resume_hint(self.run_id), reason=why)
        else:
            self.bus.emit("run_finished", status="failed", stop_reason=why, tool_calls=summary["tool_calls"], model_calls=summary["model_calls"],
                          tokens=summary["tokens"], wall_s=summary["wall_s_total"])
        return {"status": values["status"], "run_id": self.run_id, "report": values["report"], "summary": summary, "error": message}

    def _flush_thinking(self, whole: bool = False) -> None:
        if self._unsent:
            self.bus.emit("thinking_delta", text="".join(self._unsent))
            self._unsent, self._last_flush = [], time.perf_counter()
        if whole and self._thinking:
            self.bus.emit("thinking", text="".join(self._thinking))
            self._thinking = []

    def _index(self, status: str, values: dict[str, Any] | None = None) -> None:
        """Keep the runs index current, so "what is stuck" is a query. The index is never worth a run."""
        try:
            run = self.record.read_json("run.json")
            usage = self._usage_calls(values or {})
            self.index.upsert(run_id=self.run_id, status=status, last_event_at=gate.now_iso(), created=run.get("created"),
                              checkpoint=run.get("checkpoint"), arm=run.get("arm"), now=run.get("now"), profile=run.get("profile"),
                              prompt_sha=run.get("prompt_sha"), tools_sha=run.get("tools_sha"), commit_sha=run.get("commit"),
                              tags=run.get("tags"), cost_usd=round(sum(c["cost_usd"] for c in usage), 6),
                              stop_reason=(values or {}).get("stop_reason"))
        except Exception as problem:
            self.bus.dropped.append(f"runs index: {type(problem).__name__}")

    def _map(self, mode: str, data: Any) -> None:
        if mode == "messages":
            message, meta = data
            if meta.get("langgraph_node") == "agent":
                reasoning, text = reasoning_text(message)
                if reasoning:       # coalesced: a delta per token inflates the log many times over
                    self._thinking.append(reasoning)
                    self._unsent.append(reasoning)
                    if time.perf_counter() - self._last_flush >= THINKING_FLUSH_S:
                        self._flush_thinking()
                if text:
                    self.bus.emit("text_delta", text=text)
            return
        if mode == "custom":    # model_call, tool_started, tool_result, hypothesis_update, board_status, budget, stuck
            data = dict(data)
            kind = data.pop("type")
            if kind == "model_call":
                self._flush_thinking(whole=True)        # the whole reasoning of the call, once
                data["cost_usd"] = cost(data, self.cfg.price(data.get("model") or self.spec.model))
                self.bus.emit(kind, **data)
                usage = data.get("usage") or {}
                read = (usage.get("input_token_details") or {}).get("cache_read", 0) or 0
                ratio = read / usage["input_tokens"] if usage.get("input_tokens") else 0.0
                # Only the investigator's own calls: each verifier pass is one call with its own prompt, so it has no cache to read.
                if self.cached and data.get("node") == "agent" and data.get("round", 0) >= 2 and ratio < self.cfg.cache_ratio_floor:
                    self.bus.emit("cache_ratio_low", node="agent", round=data.get("round"), ratio=round(ratio, 3), floor=self.cfg.cache_ratio_floor)
                return
            self.bus.emit(kind, **data)
            return
        for node, update in data.items():
            if node == "__interrupt__":
                for pending in update:
                    self.bus.emit("gate_pending", interrupt_id=pending.id, **pending.value)
            elif node == "sweep":
                self.bus.emit("phase", name="sweep")
                for call in update.get("proposed") or []:
                    self.bus.emit("tool_call_proposed", call_id=call["call_id"], tool=call["tool"], by="harness", kind="read",
                                  rationale=call.get("rationale"), args=call["args"])
            elif node == "agent":
                if not self._investigating:
                    self._investigating = True
                    self.bus.emit("phase", name="investigate")
                proposed = update.get("proposed") or []
                for call in proposed:
                    spec = self.tools.get(call["tool"])
                    self.bus.emit("tool_call_proposed", call_id=call["call_id"], tool=call["tool"], by=call.get("by", "agent"),
                                  kind=spec.kind if spec else "unknown", rationale=call.get("rationale"), args=call["args"])
                answer = (update.get("followup") or {}).get("answer")
                if answer is not None:      # a follow-up answered in text: the report it was asked about stands
                    self.bus.emit("answer", text=answer, note=update["followup"].get("note"))
                elif not proposed or any(call["tool"] == "submit_report" for call in proposed):
                    self.bus.emit("phase", name="report")
            elif node in ("gate", "ask"):
                waits = [w["wait_ms"] for w in update.get("gate_waits", [])]
                for decision in update.get("gate_log", []):
                    self.bus.emit("gate_decision", **decision, wait_ms=waits[0] if node == "ask" and waits else None)
                for note in update.get("ic_notes", []):
                    self.bus.emit("ic_note", **note)
            elif node == "tools":
                for note in update.get("ic_notes") or []:       # a note from the inbox, now in front of the model
                    self.bus.emit("ic_note", **note)
                self._delivered({n["handle"] for n in update.get("ic_notes") or []})
                if update.get("validation"):
                    self.bus.emit("validation", **update["validation"])
                if update.get("report"):
                    self.bus.emit("draft_report", report=update["report"])
                if update.get("verify_call"):
                    self.bus.emit("phase", name="verify")
            elif node == "finalize":
                for done in update.get("followups") or []:
                    self.bus.emit("followup_finished", **done)
                self.bus.emit("report", report=update["report"], status=update["status"])

    # -- the record ----------------------------------------------------------------------------------------

    def _usage_calls(self, values: dict[str, Any]) -> list[dict[str, Any]]:
        """From the usage channel in state, so it is complete across a pause and a resume."""
        return [{**call, "cost_usd": cost(call, self.cfg.price(call.get("model") or self.spec.model))} for call in values.get("usage", [])]

    def _write_derived(self, values: dict[str, Any], wall_s: float) -> dict[str, Any]:
        usage_calls = self._usage_calls(values)
        events = self.record.events()
        counts: dict[str, int] = {}
        for event in events:
            counts[event.type] = counts.get(event.type, 0) + 1
        decided: dict[str, int] = {}
        for g in values.get("gate_log", []):
            key = f"{g['by']['who']}:{g['decision']}"
            decided[key] = decided.get(key, 0) + 1
        statuses: dict[str, int] = {}
        for entry in values.get("ledger", []):
            statuses[entry["status"]] = statuses.get(entry["status"], 0) + 1
        first = datetime.fromisoformat(events[0].t_wall) if events else datetime.now(timezone.utc)
        summary = {"run_id": self.run_id, "status": values.get("status"), "stop_reason": values.get("stop_reason"),
                   "wall_s_total": round((datetime.now(timezone.utc) - first).total_seconds(), 3), "wall_s_last_segment": round(wall_s, 3),
                   "gate_wait_ms": round(sum(w["wait_ms"] for w in values.get("gate_waits", [])), 1),   # once per round, across processes
                   "model_calls": len(usage_calls), "retries": sum(1 for c in usage_calls if (c.get("attempt") or 1) > 1),
                   "tool_calls": values.get("calls_used", 0), "calls_by_status": statuses, "gate": decided,
                   # The report's validation is the final one: the verify node adds the verdict and the disputed claims to it.
                   "validation": (values.get("report") or {}).get("validation") or values.get("validation"), "revisions": values.get("revisions", 0),
                   "tokens": usage_totals(usage_calls), "events": counts,
                   "process": metrics.process(values, usage_calls, counts, budget=call_limit(values, self._ctx),
                                              turn_cap=turn_limit(values, self._ctx)) if self._ctx else None,
                   "event_bytes": self.record.size("events.jsonl"), "dropped_sinks": self.bus.dropped}
        self.record.write_derived(values, usage_calls, summary)
        return summary

    def _finish(self, values: dict[str, Any], wall_s: float) -> dict[str, Any]:
        self.bus.emit("phase", name="done")
        summary = self._write_derived(values, wall_s)
        self._index(values.get("status") or "unknown", values)
        self.bus.emit("run_finished", status=values.get("status"), stop_reason=values.get("stop_reason"),
                      tool_calls=summary["tool_calls"], model_calls=summary["model_calls"], tokens=summary["tokens"],
                      wall_s=summary["wall_s_total"])
        return {"status": values.get("status"), "run_id": self.run_id, "report": values.get("report"), "summary": summary}

    def state(self) -> dict[str, Any]:
        return self.graph.get_state(self.config).values

    def close(self) -> None:
        self.events_sink.close()
        if self._own_conn:
            self.conn.close()
