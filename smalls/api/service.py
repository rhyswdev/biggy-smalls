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
- Stakeholder messages are drafted per audience by the drafter (U8), side by side, and kept with who wrote
  each one. "Copied" belongs to the exact text copied: the row keeps that text's hash, so any edit clears it.
"""

from __future__ import annotations

import asyncio
import hashlib
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
from smalls.api.drafter import Drafter, packet
from smalls.api.models import AUDIENCES
from smalls.api.problems import Problem
from smalls.api.reader import Reader, note_of
from smalls.api.settings import CODE, Settings
from smalls.api.sink import AppDbSink

# Every seam of section 8 is built (rows R0c), so the UI shows each feature that stands on one.
SEAMS = {"S1": "web decisions", "S2": "approve with edit", "S3": "follow-ups continue the run", "S4": "notes while running",
         "S5": "stop", "S6": "auto-approve mid-run", "S7": "native query before a read", "S8": "run records in the database"}
FINISHED = ("completed", "incomplete", "failed")


def cited(report: dict[str, Any] | None) -> set[str]:
    """Every handle a report cites, in its claims and its alert verdicts."""
    from smalls.agent import validator

    return set(validator.report_handles(report)) if report else set()


def utcnow() -> datetime:
    """The wall clock, in one place, so a test can move it."""
    return datetime.now(timezone.utc)


def stamp() -> str:
    return utcnow().isoformat(timespec="milliseconds")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
        self.sinks: dict[str, AppDbSink] = {}           # run id -> the sink its events go to the app database through
        self.locks: dict[str, threading.Lock] = {}      # run id -> the lock every start, resume and follow-up of it takes
        self.futures: dict[str, Future] = {}            # run id -> its latest piece of work on the pool
        self.guard = threading.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.listeners: dict[str, set[asyncio.Event]] = {}
        self.readers: dict[str, Reader] = {}            # one toolbox per `now`, for the API's own reads
        drafter = settings.drafter_profile
        self.drafter: Drafter | None = None if drafter == CODE else Drafter(self.cfg, drafter, settings.draft_script)
        self.closing = False
        self.recorded = self.load_recorded()

    # -- capabilities and the stream's listeners -------------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        latest = next((self.recorded_summary(f"run-{run_id}"[:64]) for run_id in reversed(self.recorded)), None)
        return {"mode": self.settings.mode, "checkpoint": self.settings.checkpoint, "now": iso(self.now),
                "profile": self.cfg.investigator.profile, "drafter": self.settings.drafter_profile,
                "seams": {k: True for k in SEAMS}, "seam_names": SEAMS,
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
        title, incident = self._incident_of(trigger, run.get("now") or self.now)
        title = title or (body.strip().splitlines() or [run_id])[0][:120]
        status = summary.get("status") or "completed"
        events = record.events()        # read before the app's transaction: one connection's write blocks another's read
        with self.db.write() as conn:
            conn.execute("INSERT INTO chat (id, origin, title, incident, now, created, auto_approve, status, recorded) "
                         "VALUES (?, 'past', ?, ?, ?, ?, ?, ?, ?)",
                         (chat_id, title, incident, run.get("now"), run.get("created"), int(bool((run.get("context") or {}).get("auto_approve"))),
                          status, run.get("created")))
            conn.execute("INSERT INTO run (run_id, chat_id, trigger, status, started, finished, folder) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (run_id, chat_id, trigger, status if status in ("completed", "incomplete", "failed", "paused") else "completed",
                          run.get("created"), run.get("created"), str(folder)))
            conn.executemany("INSERT INTO event (chat_id, seq, run_id, agent_seq, type, data, t_wall) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             [(chat_id, n, run_id, e.seq, e.type, dumps(e.data), e.t_wall) for n, e in enumerate(events, 1)])

    def tallies(self, chat_id: str) -> dict[str, int]:
        """What the chats list and the tally count: reads the agent proposed, ran, the commander rejected or skipped.
        A read counts as run when it came back ok; one refused for its arguments before the gate is not."""
        row = self.db.row("""
            SELECT
              sum(type = 'tool_call_proposed' AND json_extract(data, '$.kind') = 'read' AND json_extract(data, '$.by') = 'agent'
                  AND json_extract(data, '$.hidden') IS NULL) AS proposed,
              sum(type = 'tool_result' AND json_extract(data, '$.kind') = 'read' AND json_extract(data, '$.by') = 'agent'
                  AND json_extract(data, '$.status') = 'ok') AS run,
              sum(type = 'gate_decision' AND json_extract(data, '$.decision') = 'rejected' AND json_extract(data, '$.by.who') = 'human') AS rejected,
              sum(type = 'gate_decision' AND json_extract(data, '$.decision') = 'deferred' AND json_extract(data, '$.by.who') = 'human') AS skipped
            FROM event WHERE chat_id = ?""", (chat_id,))
        return {k: int(v or 0) for k, v in (row or {}).items()}

    def recorded_summary(self, chat_id: str) -> dict[str, Any] | None:
        """A recorded run's measured numbers, from its own `summary.json`: what the dashboard shows beside "Run agent".
        `reads` is the chats list's count of reads run, so the dashboard and the list agree; `tool_calls` is the
        budget's count, which also charges the actions it proposed."""
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
                "reads": self.tallies(chat_id)["run"],
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
        trigger = self.settings.trigger()
        return {**self.reader().incident(trigger, named=self.settings.incident), "trigger": trigger}

    def _incident_of(self, trigger: str, now: datetime | str) -> tuple[str | None, str | None]:
        """The title and number of the incident a trigger belongs to, when served data can say."""
        try:
            card = self.reader(now).incident(trigger, named=self.settings.incident).get("incident") or {}
        except Exception:
            return None, None
        return card.get("short_description"), card.get("number")

    # -- the service desk (v4 plan 5.2, 5.3, 5.5) -------------------------------------------------------------

    def imported(self) -> list[dict[str, Any]]:
        """The incidents imported into Smalls, the latest first, each with its card at `now` and its diagnosis chat."""
        out = []
        for row in self.db.rows("SELECT number, chat_id, imported_at FROM incident ORDER BY imported_at DESC, rowid DESC"):
            card = self.reader().find_incident(row["number"])
            if card is None:
                continue
            resolution = self.db.row("SELECT resolved_at, resolved_by FROM resolution WHERE chat_id = ?", (row["chat_id"],))
            out.append({**card, "chat_id": row["chat_id"], "imported_at": row["imported_at"], "status": self.status(row["chat_id"]),
                        "next_update_due": self.settings.next_update(card["priority_code"], card["updated"]), "resolution": resolution})
        return out

    def open_incidents(self) -> dict[str, Any]:
        """The open P1 and P2 ServiceNow incidents at `now`, each marked imported or not, read afresh every time,
        so a new one appears without anyone importing it."""
        reader = self.reader()
        chats = {r["number"]: r["chat_id"] for r in self.db.rows("SELECT number, chat_id FROM incident")}
        return {"connected": reader.connected,
                "incidents": [{**c, "imported": c["number"] in chats, "chat_id": chats.get(c["number"])} for c in reader.open_incidents()]}

    @staticmethod
    def incident_number(text: str) -> str:
        """The incident number in what was typed: the number itself, or a link that carries it."""
        import re

        found = re.search(r"\bINC\d{3,}\b", text, re.IGNORECASE)
        return (found[0] if found else text.strip()).upper()

    def import_incident(self, text: str) -> dict[str, Any]:
        """Import a ServiceNow incident: find it at `now`, create its diagnosis chat and start it (5.3). A
        second import of the same number returns the chat the first one made."""
        number = self.incident_number(text)
        existing = self.db.row("SELECT chat_id FROM incident WHERE number = ?", (number,))
        if existing:
            return {"number": number, "chat_id": existing["chat_id"], "already": True}
        reader = self.reader()
        card = reader.find_incident(number)
        if card is None:
            raise Problem(404, "Not found", f"ServiceNow has no incident {number} that Smalls can see. Check the number and try again.")
        chat = self.create_chat("headless", trigger=self.trigger_for(card), auto_approve=False, title=card.get("short_description") or number,
                                incident=number)
        with self.db.write() as conn:
            conn.execute("INSERT OR IGNORE INTO incident (number, chat_id, imported_at) VALUES (?, ?, ?)", (number, chat["id"], stamp()))
        return {"number": number, "chat_id": chat["id"], "already": False}

    def trigger_for(self, card: dict[str, Any]) -> str:
        """What an imported incident's first run starts from: the checkpoint's own note when it belongs to this
        incident, else the ticket's latest work note under its author's name, after the short description."""
        trigger = self.settings.trigger()
        mine = (self.reader().incident(trigger, named=self.settings.incident).get("incident") or {}).get("number")
        if mine == card["number"]:
            return trigger
        notes = self.reader().work_notes(card["handle"])
        latest = notes[0] if notes else None
        head = f"From: {latest['author']}\nTime: {latest['utc'][11:16]}\n\n" if latest else ""
        body = "\n\n".join(t for t in (card.get("short_description"), latest["text"] if latest else None) if t)
        return f"{head}{card['number']}: {body}"

    def ticket(self, number: str) -> dict[str, Any]:
        found = self.reader().ticket(self.incident_number(number))
        if found is None:
            raise Problem(404, "Not found", f"ServiceNow has no incident {number} that Smalls can see.")
        return found

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
        if origin != "blank" and (title is None or incident is None):     # a chat about the incident is named after it
            found = self._incident_of(text, self.now)
            title, incident = title or found[0], incident or found[1]
        title = title or (note_of(text)["text"] or text).strip().splitlines()[0][:120]
        with self.db.write() as conn:
            conn.execute("INSERT INTO chat (id, origin, title, incident, now, created, auto_approve, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (chat_id, origin, title, incident, iso(self.now), stamp(), int(auto_approve), "running"))
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
            self.sinks[run_id] = sink
            self.locks.setdefault(run_id, threading.Lock())
        return runner

    def attend(self, chat: dict[str, Any], runner: Runner) -> Runner:
        """Before a follow-up: a headless chat's first run went unattended, with its reads on (5.3), but what
        follows is the person's, so the run now stops at its gates for them and the chat's own switch, off by
        default, decides its reads. Only the API's policy changes; the agent is the same."""
        if chat["origin"] != "headless" or runner.attended:
            return runner
        runner.attended, runner.stop_at_gate, runner.via = True, True, "web"
        if runner.run_id in self.sinks:
            self.sinks[runner.run_id].attended = True
        runner.set_auto_approve(bool(chat["auto_approve"]))
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
            self.attend(self.chat(chat_id), runner)
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
            self.attend(chat, runner)
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

    # -- after the investigation: resolution, write-up, knowledge base -------------------------------------

    def resolve(self, chat_id: str, path: str, note: str, resolved_at: str, resolved_by: str) -> dict[str, Any]:
        """Mark the incident resolved in this chat. The write-up starts as version 1: under the agent's path it
        keeps every handle the report cites, under the commander's own path none (section 4, Summary)."""
        chat = self.chat(chat_id)
        if chat["origin"] == "past":
            raise Problem(409, "Read-only", "A recorded run is read-only.")
        report = self.report(chat_id)
        if report is None and path == "agent":
            raise Problem(409, "No conclusion", "This chat has no report, so Smalls' conclusion cannot be the fix.")
        with self.db.write() as conn:
            conn.execute("INSERT OR REPLACE INTO resolution (chat_id, path, note, resolved_at, resolved_by) VALUES (?, ?, ?, ?, ?)",
                         (chat_id, path, note.strip(), resolved_at, resolved_by.strip()))
            if not conn.execute("SELECT 1 FROM writeup WHERE chat_id = ?", (chat_id,)).fetchone():
                kept = sorted(cited(report)) if path == "agent" and report else []
                conn.execute("INSERT INTO writeup (chat_id, version, kept, body) VALUES (?, 1, ?, '{}')", (chat_id, dumps(kept)))
            conn.execute("UPDATE chat SET status = 'resolved' WHERE id = ?", (chat_id,))
        return self.writeup(chat_id)

    def writeup(self, chat_id: str) -> dict[str, Any]:
        self.chat(chat_id)
        resolution = self.db.row("SELECT path, note, resolved_at, resolved_by FROM resolution WHERE chat_id = ?", (chat_id,))
        row = self.db.row("SELECT version, kept, saved_at FROM writeup WHERE chat_id = ? ORDER BY version DESC LIMIT 1", (chat_id,))
        report = self.report(chat_id)
        rows: dict[str, str] = {}
        for record in self.records(chat_id):          # what the run was shown of each handle, its first line
            for r in record.read_jsonl("evidence.jsonl"):
                rows.setdefault(r["handle"], r["text"].split("\n", 1)[0][:300])
        return {"resolution": resolution, "report": report, "rows": rows, "incident": self.chat(chat_id).get("incident"), "cited": sorted(cited(report)),
                "writeup": {"version": row["version"], "kept": loads(row["kept"]), "saved_at": row["saved_at"]} if row else None}

    def edit_writeup(self, chat_id: str, kept: list[str], note: str | None) -> dict[str, Any]:
        """Any edit unsaves the write-up until it is saved again."""
        row = self.db.row("SELECT version FROM writeup WHERE chat_id = ? ORDER BY version DESC LIMIT 1", (chat_id,))
        if row is None:
            raise Problem(409, "Not resolved", "Mark the incident resolved first.")
        with self.db.write() as conn:
            conn.execute("UPDATE writeup SET kept = ?, saved_at = NULL WHERE chat_id = ? AND version = ?", (dumps(sorted(set(kept))), chat_id, row["version"]))
            if note is not None:
                conn.execute("UPDATE resolution SET note = ? WHERE chat_id = ?", (note.strip(), chat_id))
            conn.execute("DELETE FROM kb_entry WHERE chat_id = ?", (chat_id,))
        return self.writeup(chat_id)

    def regenerate(self, chat_id: str) -> dict[str, Any]:
        """A new version from the chat's latest report, keeping the note and the evidence choices."""
        row = self.db.row("SELECT version, kept FROM writeup WHERE chat_id = ? ORDER BY version DESC LIMIT 1", (chat_id,))
        if row is None:
            raise Problem(409, "Not resolved", "Mark the incident resolved first.")
        with self.db.write() as conn:
            conn.execute("INSERT INTO writeup (chat_id, version, kept, body) VALUES (?, ?, ?, '{}')", (chat_id, row["version"] + 1, row["kept"]))
            conn.execute("DELETE FROM kb_entry WHERE chat_id = ?", (chat_id,))
        return self.writeup(chat_id)

    def save_writeup(self, chat_id: str, version: int, title: str, body: str, terms: list[str]) -> dict[str, Any]:
        resolution = self.db.row("SELECT note FROM resolution WHERE chat_id = ?", (chat_id,))
        if not resolution or not resolution["note"].strip():
            raise Problem(409, "No note", "Say how it was resolved before saving.")
        current = self.db.row("SELECT version FROM writeup WHERE chat_id = ? ORDER BY version DESC LIMIT 1", (chat_id,))
        if current is None or current["version"] != version:
            raise Problem(409, "Out of date", "The write-up changed since this page was loaded. Reload it and save again.")
        with self.db.write() as conn:
            conn.execute("UPDATE writeup SET saved_at = ?, body = ? WHERE chat_id = ? AND version = ?", (stamp(), dumps({"title": title, "body": body}), chat_id, version))
            conn.execute("DELETE FROM kb_entry WHERE chat_id = ?", (chat_id,))
            conn.execute("INSERT INTO kb_entry (chat_id, title, body, terms, saved_at) VALUES (?, ?, ?, ?, ?)",
                         (chat_id, title, body, " ".join(dict.fromkeys(t.strip() for t in terms if t.strip())), stamp()))
        return self.writeup(chat_id)

    def kb(self, q: str) -> list[dict[str, Any]]:
        """Knowledge-base search over saved entries: every word of the query, quoted, any one enough (U11)."""
        import re

        words = re.findall(r"\w+", q)
        if not words:
            return [dict(r) for r in self.db.rows("SELECT id, chat_id, title, terms, saved_at FROM kb_entry ORDER BY saved_at DESC LIMIT 20")]
        match = " OR ".join(f'"{w}"' for w in words)
        return self.db.rows("SELECT k.id, k.chat_id, k.title, k.terms, k.saved_at, snippet(kb_fts, 1, '', '', ' … ', 24) AS snippet, bm25(kb_fts) AS score "
                            "FROM kb_fts JOIN kb_entry k ON k.id = kb_fts.rowid WHERE kb_fts MATCH ? ORDER BY score LIMIT 20", (match,))

    # -- stakeholder messages (U8) ---------------------------------------------------------------------------

    def messages(self, chat_id: str) -> dict[str, Any]:
        """The chat's drafts in audience order, whether each one's text is the text last copied, who drafts, and
        the saved knowledge-base entry."""
        self.chat(chat_id)
        order = {k: i for i, k in enumerate(AUDIENCES)}
        rows = sorted(self.db.rows("SELECT * FROM message WHERE chat_id = ?", (chat_id,)), key=lambda r: order.get(r["audience"], len(order)))
        entry = self.db.row("SELECT title, body, terms, saved_at FROM kb_entry WHERE chat_id = ?", (chat_id,))
        return {"drafter": self.settings.drafter_profile, "messages": [self._message_view(r) for r in rows], "entry": entry}

    @staticmethod
    def _message_view(row: dict[str, Any]) -> dict[str, Any]:
        return {"audience": row["audience"], "draft": row["draft"], "text": row["text"], "copied": row["copied_hash"] == digest(row["text"]),
                "drafted_by": row["drafted_by"], "why_code": row["why_code"], "cost_usd": row["cost_usd"], "drafted_at": row["drafted_at"]}

    def draft_messages(self, chat_id: str, audiences: list[dict[str, Any]]) -> dict[str, Any]:
        """Draft each audience named, replacing its draft, its text and its copied mark. One model call per audience,
        side by side; an audience whose call fails keeps the code-rendered draft the request carries, and says why."""
        chat = self.chat(chat_id)
        if chat["origin"] == "past":
            raise Problem(409, "Read-only", "A recorded run is read-only.")
        report = self.report(chat_id)
        if report is None:
            raise Problem(409, "No report", "There is no report to draft from yet.")
        data = self.writeup(chat_id)
        kept = (data["writeup"] or {}).get("kept") or []
        pk = packet(chat.get("incident"), report, data["resolution"], [data["rows"][h] for h in kept if h in data["rows"]])
        drafter = self.drafter
        if drafter is None:
            results: list[dict[str, Any]] = [{"text": None, "error": None} for _ in audiences]
        else:
            with ThreadPoolExecutor(max_workers=len(audiences), thread_name_prefix="smalls-draft") as pool:
                results = list(pool.map(lambda a: drafter.draft(pk, a), audiences))
        with self.db.write() as conn:
            for spec, got in zip(audiences, results):
                by_model = drafter is not None and bool(got.get("text"))
                text = got["text"] if by_model else spec["fallback"]
                why = None if by_model or drafter is None else f"The {drafter.profile} drafter failed ({got.get('error')}), so code wrote this draft."
                conn.execute("INSERT OR REPLACE INTO message (chat_id, audience, draft, text, copied_hash, drafted_by, why_code, usage, cost_usd, drafted_at) "
                             "VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?)",
                             (chat_id, spec["key"], text, text, drafter.profile if by_model and drafter else CODE, why,
                              dumps(got["usage"]) if got.get("usage") else None, got.get("cost_usd"), stamp()))
        return self.messages(chat_id)

    def _drafted(self, chat_id: str, audience: str) -> None:
        self.chat(chat_id)
        if not self.db.row("SELECT 1 AS found FROM message WHERE chat_id = ? AND audience = ?", (chat_id, audience)):
            raise Problem(404, "Not drafted", f"This chat has no {audience} draft yet.")

    def edit_message(self, chat_id: str, audience: str, text: str) -> dict[str, Any]:
        """The commander's text. The copied mark stays only if this is the exact text they copied."""
        self._drafted(chat_id, audience)
        with self.db.write() as conn:
            conn.execute("UPDATE message SET text = ? WHERE chat_id = ? AND audience = ?", (text, chat_id, audience))
        return self._message_view(self.db.row("SELECT * FROM message WHERE chat_id = ? AND audience = ?", (chat_id, audience)) or {})

    def copied_message(self, chat_id: str, audience: str, text: str) -> dict[str, Any]:
        """Mark this exact text copied."""
        self._drafted(chat_id, audience)
        with self.db.write() as conn:
            conn.execute("UPDATE message SET text = ?, copied_hash = ? WHERE chat_id = ? AND audience = ?", (text, digest(text), chat_id, audience))
        return self._message_view(self.db.row("SELECT * FROM message WHERE chat_id = ? AND audience = ?", (chat_id, audience)) or {})

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
