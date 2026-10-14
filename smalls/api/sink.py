"""The event sink a run writes to the app database (docs/decisions/ui-build.md section 6.2): every event of the
agent's protocol, unchanged, numbered per chat, plus the bookkeeping the app needs from a few of them. The
agent's own record still gets every event through its backend; this copy is what the browser reads.

It is called from the run's threads (the worker, and the heartbeat), so it writes under the database's lock,
and tells the stream's listeners on the event loop that something arrived.
"""

from __future__ import annotations

from typing import Any, Callable

from smalls.agent.events import Event
from smalls.api.db import Database, dumps


class AppDbSink:
    def __init__(self, db: Database, chat_id: str, run_id: str, *, attended: bool, notify: Callable[[str], None] | None = None):
        self.db, self.chat_id, self.run_id, self.attended, self.notify = db, chat_id, run_id, attended, notify

    def emit(self, ev: Event) -> None:
        data: dict[str, Any] = ev.data
        with self.db.write() as conn:
            seq = conn.execute("SELECT coalesce(max(seq), 0) + 1 FROM event WHERE chat_id = ?", (self.chat_id,)).fetchone()[0]
            conn.execute("INSERT INTO event (chat_id, seq, run_id, agent_seq, type, data, t_wall) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (self.chat_id, seq, self.run_id, ev.seq, ev.type, dumps(data), ev.t_wall))
            if ev.type == "gate_pending" and self.attended:
                # A gate a person must answer. An unattended run answers its own gates, and its events say how.
                conn.execute("INSERT OR IGNORE INTO gate (chat_id, run_id, interrupt_id, round, cards, asked_at) VALUES (?, ?, ?, ?, ?, ?)",
                             (self.chat_id, self.run_id, data["interrupt_id"], data.get("round"), dumps(data.get("calls") or []),
                              data.get("asked_at") or ev.t_wall))
            elif ev.type == "ic_note" and data.get("handle"):
                conn.execute("UPDATE note SET delivered_at = ? WHERE chat_id = ? AND handle = ? AND delivered_at IS NULL",
                             (ev.t_wall, self.chat_id, data["handle"]))
            elif ev.type == "run_started":
                conn.execute("UPDATE run SET status = 'running' WHERE run_id = ?", (self.run_id,))
        if self.notify:
            self.notify(self.chat_id)
