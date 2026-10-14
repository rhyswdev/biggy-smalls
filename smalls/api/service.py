"""What the API holds for the life of the process, and the run service (docs/decisions/ui-build.md sections 6.1
and 6.2): the settings, the database, the agent's config, the worker pool the runs execute on, and the
runners of the runs that are still going.

How a run goes:
- It starts on a worker with its own toolbox, because one store holds one clock (U3). A chat run is
  attended: it stops at every gate (`stop_at_gate`), and nothing holds a thread while a person reads. A
  "Run agent" run is unattended: reads run on the switch and an action is deferred and recorded as
  policy's, as J15 makes every unattended run do (U6).
- A gate is answered once. The answer names the interrupt it answers and is applied only while that
  interrupt is still pending: a conditional update in the database, so a second answer, from a second tab
  or a held key, changes nothing and gets 409 (U5). A gate never expires (Q1).
- A note goes to the run as it stands: into a running run's inbox (S4), with the next answer at a gate,
  or, once the run has finished, as a follow-up that continues the same run (S3, U9).
- The auto-approve switch is the chat's. A run reads it at every round (S6); turned on while a gate of
  reads waits, it answers that gate as the commander's standing choice. An action is never answered by
  the switch (U6).
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from smalls.agent.config import Config, load_config
from smalls.agent.graph import iso
import json
from pathlib import Path

from smalls.agent.record import FILES, RunRecord, new_run_id
from smalls.agent.runner import Runner, Toolbox, bundle_toolbox, module_toolbox
from smalls.api.db import Database, dumps, loads
from smalls.api.problems import Problem
from smalls.api.reader import Reader, note_of
from smalls.api.settings import Settings
from smalls.api.sink import AppDbSink

# Every seam of section 8 is built (rows R0c), so the UI shows each feature that stands on one.
SEAMS = {"S1": "web decisions", "S2": "approve with edit", "S3": "follow-ups continue the run", "S4": "notes while running",
         "S5": "stop", "S6": "auto-approve mid-run", "S7": "native query before a read", "S8": "run records in the database"}
FINISHED = ("completed", "incomplete", "failed")


def utcnow() -> datetime:
    """The wall clock, in one place, so a test can move it."""
    return datetime.now(timezone.utc)


def stamp() -> str:
    return utcnow().isoformat(timespec="milliseconds")


class Service:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.uri)
        self.cfg: Config = load_config(profile="scripted" if settings.mode == "scripted" else None)
        if self.cfg.investigator.provider == "scripted" and settings.script_delay:
            self.cfg = replace(self.cfg, investigator=self.cfg.investigator.with_params(delay_s=settings.script_delay))
        self.now: datetime = settings.now()
        self.pool = ThreadPoolExecutor(max_workers=settings.workers, thread_name_prefix="smalls-run")
        self.runners: dict[str, Runner] = {}            # run id -> the runner of a run kept in memory
        self.locks: dict[str, threading.Lock] = {}      # run id -> the lock every start, resume and follow-up of it takes
        self.futures: dict[str, Future] = {}            # run id -> its latest piece of work on the pool
        self.guard = threading.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.listeners: dict[str, set[asyncio.Event]] = {}
        self.readers: dict[str, Reader] = {}            # one toolbox per `now`, for the API's own reads
        self.closing = False
        self.recorded = self.load_recorded()

    # -- capabilities and the stream's listeners -------------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        latest = next((self.recorded_summary(f"run-{run_id}"[:64]) for run_id in reversed(self.recorded)), None)
        return {"mode": self.settings.mode, "checkpoint": self.settings.checkpoint, "now": iso(self.now),
                "profile": self.cfg.investigator.profile, "seams": {k: True for k in SEAMS}, "seam_names": SEAMS,
                "last_recorded": latest}

    def notify(self, chat_id: str) -> None:
        """Wake the stream's listeners for a chat. Called from a run's threads."""
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        for event in list(self.listeners.get(chat_id, ())):
            loop.call_soon_threadsafe(event.set)

    # -- committed runs --------------------------------------------------------------------------------------

    def load_recorded(self) -> list[str]:
        """Load each committed run with a report as a finished, read-only `past` chat (6.5, U12). A run the
        scripted provider made shows the plumbing, not the agent, and is skipped. Nothing is re-run or
        re-streamed, no model is called and no key is read: the files are copied into the database."""
        root = Path(self.settings.runs_dir)
        loaded: list[str] = []
        for folder in sorted(root.glob("*/report.json")) if root.is_dir() else []:
            folder = folder.parent
            try:
                run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
                summary = json.loads((folder / "summary.json").read_text(encoding="utf-8")) if (folder / "summary.json").is_file() else {}
            except (OSError, ValueError):
                continue
            if run.get("provider") == "scripted" and not self.settings.load_scripted:
                continue
            run_id = run.get("run_id") or folder.name
            if self.db.records.read_text(run_id, "run.json") is not None:
                continue
            for name in FILES:
                if (folder / name).is_file():
                    self.db.records.write_text(run_id, name, (folder / name).read_text(encoding="utf-8"))
            self._past_chat(run_id, run, summary, folder)
            loaded.append(run_id)
        return loaded

    def _past_chat(self, run_id: str, run: dict[str, Any], summary: dict[str, Any], folder: Path) -> None:
        record = RunRecord(None, run_id, backend=self.db.records)
        chat_id = f"run-{run_id}"[:64]
        trigger = run.get("trigger") or ""
        body = note_of(trigger)["text"] or trigger
        title = (body.strip().splitlines() or [run_id])[0][:120]
        status = summary.get("status") or "completed"
        events = record.events()        # read before the app's transaction: one connection's write blocks another's read
        with self.db.write() as conn:
            conn.execute("INSERT INTO chat (id, origin, title, incident, now, created, auto_approve, status, recorded) "
                         "VALUES (?, 'past', ?, NULL, ?, ?, ?, ?, ?)",
                         (chat_id, title, run.get("now"), run.get("created"), int(bool((run.get("context") or {}).get("auto_approve"))),
                          status, run.get("created")))
            conn.execute("INSERT INTO run (run_id, chat_id, trigger, status, started, finished, folder) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (run_id, chat_id, trigger, status if status in ("completed", "incomplete", "failed", "paused") else "completed",
                          run.get("created"), run.get("created"), str(folder)))
            conn.executemany("INSERT INTO event (chat_id, seq, run_id, agent_seq, type, data, t_wall) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             [(chat_id, n, run_id, e.seq, e.type, dumps(e.data), e.t_wall) for n, e in enumerate(events, 1)])

    def recorded_summary(self, chat_id: str) -> dict[str, Any] | None:
        """A recorded run's measured numbers, from its own `summary.json`: what the dashboard shows beside "Run agent"."""
        run = self.latest_run(chat_id)
        if run is None:
            return None
        record = RunRecord(None, run["run_id"], backend=self.db.records)
        try:
            summary = json.loads(record.read_text("summary.json") or "{}")
            meta = json.loads(record.read_text("run.json") or "{}")
        except ValueError:
            return None
        return {"run_id": run["run_id"], "created": meta.get("created"), "profile": meta.get("profile"), "wall_s": summary.get("wall_s_total"),
                "tool_calls": summary.get("tool_calls"), "model_calls": summary.get("model_calls"), "gate": summary.get("gate"),
                "cost_usd": (summary.get("tokens") or {}).get("cost_usd")}

    def export(self, run_id: str) -> dict[str, Any]:
        """Write a run from the database to the runs folder in the file layout, so it can be committed. The one
        call that writes to disk. A folder that already exists is never overwritten."""
        record = RunRecord(None, run_id, backend=self.db.records)
        if record.read_text("run.json") is None:
            raise Problem(404, "Not found", f"No run {run_id}.")
        target = Path(self.settings.runs_dir) / run_id
        if target.exists():
            raise Problem(409, "Already exported", f"{target} exists; a run record on disk is never overwritten.")
        return {"run_id": run_id, "folder": str(record.export(self.settings.runs_dir)), "files": record.missing() == [] and list(FILES)}

    # -- reads -----------------------------------------------------------------------------------------------

    def reader(self, now: datetime | str | None = None) -> Reader:
        when = now if isinstance(now, datetime) else datetime.fromisoformat(now.replace("Z", "+00:00")) if now else self.now
        key = iso(when)
        with self.guard:
            if key not in self.readers:
                self.readers[key] = Reader(self.toolbox(), when)
            return self.readers[key]

    def incident(self) -> dict[str, Any]:
        return self.reader().incident(self.settings.trigger(), named=self.settings.incident)

    def records(self, chat_id: str) -> list[RunRecord]:
        return [RunRecord(None, run["run_id"], backend=self.db.records) for run in self.runs(chat_id)]

    def report(self, chat_id: str) -> dict[str, Any] | None:
        """The chat's latest report, as its last `report` event carried it."""
        row = self.db.row("SELECT data FROM event WHERE chat_id = ? AND type = 'report' ORDER BY seq DESC LIMIT 1", (chat_id,))
        return (loads(row["data"]) or {}).get("report") if row else None

    def events(self, chat_id: str, after: int = 0) -> list[dict[str, Any]]:
        return [{**e, "data": loads(e["data"])} for e in
                self.db.rows("SELECT seq, run_id, agent_seq, type, data, t_wall FROM event WHERE chat_id = ? AND seq > ? ORDER BY seq", (chat_id, after))]

    # -- chats -----------------------------------------------------------------------------------------------

    def create_chat(self, origin: str, *, trigger: str | None, auto_approve: bool, title: str | None = None,
                    incident: str | None = None) -> dict[str, Any]:
        if self.closing:
            raise Problem(503, "Shutting down", "The API is shutting down and takes no new runs.")
        chat_id = uuid.uuid4().hex[:12]
        text = (trigger if trigger is not None else self.settings.trigger()).strip()
        if not text:
            raise Problem(422, "Invalid request", "A chat starts from a question or a note; this one is empty.")
        headless = origin == "headless"
        with self.db.write() as conn:
            conn.execute("INSERT INTO chat (id, origin, title, incident, now, created, auto_approve, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (chat_id, origin, title or text.splitlines()[0][:120], incident, iso(self.now), stamp(),
                          int(auto_approve or headless), "running"))
        self.start_run(chat_id, text)
        return self.chat(chat_id)

    def chat(self, chat_id: str) -> dict[str, Any]:
        row = self.db.row("SELECT * FROM chat WHERE id = ?", (chat_id,))
        if row is None:
            raise Problem(404, "Not found", f"No chat {chat_id}.")
        row["auto_approve"] = bool(row["auto_approve"])
        return row

    def runs(self, chat_id: str) -> list[dict[str, Any]]:
        return self.db.rows("SELECT * FROM run WHERE chat_id = ? ORDER BY started", (chat_id,))

    def latest_run(self, chat_id: str) -> dict[str, Any] | None:
        runs = self.runs(chat_id)
        return runs[-1] if runs else None

    def pending_gate(self, chat_id: str) -> dict[str, Any] | None:
        gate = self.db.row("SELECT * FROM gate WHERE chat_id = ? AND decided_at IS NULL ORDER BY asked_at DESC LIMIT 1", (chat_id,))
        if gate:
            gate["cards"] = loads(gate["cards"])
        return gate

    def status(self, chat_id: str) -> str:
        """What the chats list says: waiting (on the commander at a gate), running, stopped, failed, resolved or finished."""
        run = self.latest_run(chat_id)
        if run is None:
            return "finished"
        if run["status"] == "running":
            return "running"
        if self.pending_gate(chat_id):
            return "waiting"
        if self.db.row("SELECT 1 AS x FROM resolution WHERE chat_id = ?", (chat_id,)):
            return "resolved"
        return {"paused": "stopped", "stopped": "stopped", "failed": "failed"}.get(run["status"], "finished")

    # -- runs ------------------------------------------------------------------------------------------------

    def toolbox(self) -> Toolbox:
        if self.settings.registry:
            return module_toolbox(self.settings.registry)
        return bundle_toolbox(self.settings.bundle, self.settings.tags)

    def runner(self, chat: dict[str, Any], run_id: str) -> Runner:
        """The runner of a run: the one kept in memory, or a new one over the same record and checkpoints."""
        with self.guard:
            if run_id in self.runners:
                return self.runners[run_id]
        headless = chat["origin"] == "headless"
        cfg = replace(self.cfg, auto_approve=bool(chat["auto_approve"]) or headless)
        sink = AppDbSink(self.db, chat["id"], run_id, attended=not headless, notify=self.notify)
        runner = Runner(cfg, self.toolbox(), run_id, sinks=[sink], stop_at_gate=not headless, attended=not headless,
                        via="headless" if headless else "web", script=self.settings.script, backend=self.db.records)
        with self.guard:
            self.runners[run_id] = runner
            self.locks.setdefault(run_id, threading.Lock())
        return runner

    def start_run(self, chat_id: str, trigger: str) -> str:
        chat = self.chat(chat_id)
        run_id = new_run_id()
        with self.db.write() as conn:
            conn.execute("INSERT INTO run (run_id, chat_id, trigger, status, started) VALUES (?, ?, ?, 'running', ?)",
                         (run_id, chat_id, trigger, stamp()))
            conn.execute("UPDATE chat SET status = 'running' WHERE id = ?", (chat_id,))
        runner = self.runner(chat, run_id)
        self._submit(run_id, chat_id, lambda: runner.start(trigger, self.now, checkpoint=self.settings.checkpoint))
        return run_id

    def _submit(self, run_id: str, chat_id: str, work: Callable[[], dict[str, Any]]) -> Future:
        if self.closing:
            raise Problem(503, "Shutting down", "The API is shutting down and takes no new work.")
        with self.db.write() as conn:
            conn.execute("UPDATE run SET status = 'running', finished = NULL WHERE run_id = ?", (run_id,))
        lock = self.locks.setdefault(run_id, threading.Lock())

        def job() -> dict[str, Any]:
            with lock:
                try:
                    result = work()
                except BaseException as failure:        # a run never takes the worker down with it
                    result = {"status": "failed", "error": f"{type(failure).__name__}"}
                self._settle(chat_id, run_id, result)
                return result

        future = self.pool.submit(job)
        self.futures[run_id] = future
        return future

    def _settle(self, chat_id: str, run_id: str, result: dict[str, Any]) -> None:
        """Record how a piece of work on a run ended, and deliver a note no round took (S4) as a follow-up."""
        status = "stopped" if result.get("stopped") else result.get("status") or "failed"
        with self.db.write() as conn:
            conn.execute("UPDATE run SET status = ?, finished = ? WHERE run_id = ?",
                         (status, stamp() if status in FINISHED else None, run_id))
            conn.execute("UPDATE chat SET status = ? WHERE id = ?", (status, chat_id))
        self.notify(chat_id)
        runner = self.runners.get(run_id)
        if runner is not None and status in ("completed", "incomplete") and runner.pending_notes() and not self.closing:
            self._submit(run_id, chat_id, lambda: runner.reopen())

    def wait(self, chat_id: str, timeout: float = 30) -> None:
        """Until no work on the chat's runs is queued or running: for tests and for shutdown."""
        for run in self.runs(chat_id):
            while True:
                future = self.futures.get(run["run_id"])
                if future is None:
                    break
                future.result(timeout=timeout)
                if self.futures.get(run["run_id"]) is future:
                    break

    # -- gates -----------------------------------------------------------------------------------------------

    def answer_gate(self, chat_id: str, interrupt_id: str, answers: dict[str, dict[str, Any]], note: str | None,
                    *, standing: bool = False) -> dict[str, Any]:
        """Apply the commander's answer to a pending gate, once (U5)."""
        chat = self.chat(chat_id)
        gate = self.db.row("SELECT * FROM gate WHERE chat_id = ? AND interrupt_id = ?", (chat_id, interrupt_id))
        if gate is None:
            raise Problem(404, "Not found", "No gate with that id in this chat.")
        cards = {c["call_id"]: c for c in loads(gate["cards"])}
        unknown = sorted(set(answers) - set(cards))
        if unknown:
            raise Problem(422, "Invalid request", f"These calls are not on the gate: {', '.join(unknown)}.")
        runner = self.runner(chat, gate["run_id"])
        for call_id, answer in answers.items():
            if answer.get("args") is not None:
                problem = self._edit_problem(runner, cards[call_id], answer["args"])
                if problem:
                    raise Problem(422, "Invalid request", problem)
        with self.db.write() as conn:       # the compare-and-set: only a gate still pending is answered
            changed = conn.execute("UPDATE gate SET decided_at = ?, answers = ?, standing = ? WHERE run_id = ? AND interrupt_id = ? "
                                   "AND decided_at IS NULL", (stamp(), dumps(answers), int(standing), gate["run_id"], interrupt_id)).rowcount
        if not changed:
            raise Problem(409, "Already answered", "This gate was already answered; the run went on with the first answer.")
        if note:
            self._record_note(chat_id, gate["run_id"], runner.add_note(note))
        self._submit(gate["run_id"], chat_id, lambda: runner.resume(answers))
        return {"status": "resumed", "run_id": gate["run_id"]}

    @staticmethod
    def _edit_problem(runner: Runner, card: dict[str, Any], args: dict[str, Any]) -> str | None:
        from smalls.agent import gate

        _, refused = gate.edited(card, {"approve": True, "args": args}, runner.toolbox.read_tools.get(card["tool"]))
        return refused

    # -- notes, stop, the switch, more runs ------------------------------------------------------------------

    def _record_note(self, chat_id: str, run_id: str | None, note: dict[str, Any]) -> None:
        with self.db.write() as conn:
            conn.execute("INSERT INTO note (chat_id, run_id, handle, text, submitted_at) VALUES (?, ?, ?, ?, ?)",
                         (chat_id, run_id, note["handle"], note["text"], note["submitted_at"]))

    def add_note(self, chat_id: str, text: str) -> dict[str, Any]:
        """A note goes to the run as it stands: running, waiting at a gate, or finished (U9)."""
        chat = self.chat(chat_id)
        if chat["origin"] == "past":
            raise Problem(409, "Read-only", "A recorded run is read-only. Start a new chat to investigate again.")
        run = self.latest_run(chat_id)
        if run is None:
            raise Problem(409, "No run", "This chat has no run to send a note to.")
        if run["status"] == "failed":
            raise Problem(409, "Run failed", "The latest run failed and has nothing to continue. Start another run.")
        runner = self.runner(chat, run["run_id"])
        note = runner.add_note(text)
        self._record_note(chat_id, run["run_id"], note)
        if run["status"] in ("completed", "incomplete"):        # finished: the note continues the same run (S3)
            self._submit(run["run_id"], chat_id, lambda: runner.reopen())
            return {**note, "delivery": "follow-up"}
        if run["status"] in ("paused", "stopped") and not self.pending_gate(chat_id):
            self._submit(run["run_id"], chat_id, lambda: runner.resume())       # a stopped run goes on, with the note
            return {**note, "delivery": "resume"}
        return {**note, "delivery": "next round" if run["status"] == "running" else "with the answer"}

    def stop(self, chat_id: str) -> dict[str, Any]:
        run = self.latest_run(chat_id)
        if run is None or run["status"] != "running":
            raise Problem(409, "Not running", "No run in this chat is running.")
        self.runner(self.chat(chat_id), run["run_id"]).request_stop()
        return {"status": "stopping", "run_id": run["run_id"]}

    def set_auto_approve(self, chat_id: str, on: bool) -> dict[str, Any]:
        chat = self.chat(chat_id)
        with self.db.write() as conn:
            conn.execute("UPDATE chat SET auto_approve = ? WHERE id = ?", (int(on), chat_id))
        run = self.latest_run(chat_id)
        if run is not None and run["status"] not in FINISHED:
            self.runner(chat, run["run_id"]).set_auto_approve(on)
        answered = None
        gate = self.pending_gate(chat_id) if on else None
        if gate and all(c["kind"] == "read" for c in gate["cards"]):        # the standing choice; an action always waits for a person
            answered = self.answer_gate(chat_id, gate["interrupt_id"], {c["call_id"]: {"approve": True} for c in gate["cards"]}, None, standing=True)
        return {"auto_approve": on, "answered": answered}

    def another_run(self, chat_id: str, *, resume: bool = False) -> dict[str, Any]:
        chat = self.chat(chat_id)
        if chat["origin"] == "past":
            raise Problem(409, "Read-only", "A recorded run is read-only. Start a new chat to investigate again.")
        run = self.latest_run(chat_id)
        if run is not None and run["status"] == "running":
            raise Problem(409, "Running", "A run in this chat is still running.")
        if resume:
            if run is None or run["status"] not in ("paused", "stopped") or self.pending_gate(chat_id):
                raise Problem(409, "Nothing to resume", "The latest run is not stopped.")
            runner = self.runner(chat, run["run_id"])
            self._submit(run["run_id"], chat_id, lambda: runner.resume())
            return {"run_id": run["run_id"], "status": "resumed"}
        trigger = run["trigger"] if run else self.settings.trigger()
        return {"run_id": self.start_run(chat_id, trigger), "status": "started"}

    # -- shutdown --------------------------------------------------------------------------------------------

    def close(self) -> None:
        """Stop taking runs and let the paused ones go: they live only in this process's database (U2)."""
        self.closing = True
        for runner in list(self.runners.values()):
            try:
                runner.request_stop()
            except Exception:
                pass
        self.pool.shutdown(wait=True, cancel_futures=True)
        for runner in list(self.runners.values()):
            try:
                runner.close()
            except Exception:
                pass
        self.runners.clear()
        self.db.close()
