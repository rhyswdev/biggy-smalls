"""The app database (docs/decisions/ui-build.md section 6.1): one in-memory SQLite database per process,
`file:/<name>?vfs=memdb`, rebuilt at every start and gone when the process ends.

Three users share it, each on a connection of its own, so SQLite's locking keeps them apart:
- the app, through `Database`, whose one connection is taken under one lock;
- the agent's run records (seam S8), through `SqliteBackend`, which owns the `run_*` tables;
- each run's checkpointer, LangGraph's `SqliteSaver`, which owns `checkpoints` and `writes`.
The app reads the `run_*` tables and never writes them.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from smalls.agent.record import SqliteBackend

SCHEMA = """
CREATE TABLE chat (
  id TEXT PRIMARY KEY, origin TEXT NOT NULL CHECK (origin IN ('chat', 'headless', 'blank', 'past')), title TEXT NOT NULL,
  incident TEXT, now TEXT NOT NULL, created TEXT NOT NULL, auto_approve INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
  recorded TEXT                                   -- for a past chat: when its run was made
);
CREATE TABLE run (
  run_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES chat(id), trigger TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running', 'paused', 'completed', 'incomplete', 'stopped', 'failed')),
  started TEXT NOT NULL, finished TEXT, folder TEXT
);
CREATE TABLE event (
  chat_id TEXT NOT NULL, seq INTEGER NOT NULL, run_id TEXT NOT NULL, agent_seq INTEGER NOT NULL, type TEXT NOT NULL,
  data TEXT NOT NULL, t_wall TEXT NOT NULL, PRIMARY KEY (chat_id, seq)
);
CREATE TABLE gate (
  chat_id TEXT NOT NULL, run_id TEXT NOT NULL, interrupt_id TEXT NOT NULL, round INTEGER, cards TEXT NOT NULL, asked_at TEXT NOT NULL,
  decided_at TEXT, answers TEXT, standing INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (run_id, interrupt_id)
);
CREATE TABLE note (
  id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, run_id TEXT, handle TEXT, text TEXT NOT NULL, submitted_at TEXT NOT NULL,
  delivered_at TEXT
);
CREATE TABLE resolution (
  chat_id TEXT PRIMARY KEY, path TEXT NOT NULL CHECK (path IN ('agent', 'own')), note TEXT NOT NULL, resolved_at TEXT NOT NULL,
  resolved_by TEXT NOT NULL
);
CREATE TABLE writeup (
  chat_id TEXT NOT NULL, version INTEGER NOT NULL, kept TEXT NOT NULL, body TEXT NOT NULL, saved_at TEXT,
  PRIMARY KEY (chat_id, version)
);
CREATE TABLE message (
  chat_id TEXT NOT NULL, audience TEXT NOT NULL, draft TEXT NOT NULL, text TEXT NOT NULL, copied_hash TEXT, drafted_by TEXT NOT NULL,
  usage TEXT, cost_usd REAL, PRIMARY KEY (chat_id, audience)
);
CREATE TABLE kb_entry (
  id INTEGER PRIMARY KEY, chat_id TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, terms TEXT NOT NULL, saved_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE kb_fts USING fts5(title, body, terms, content='kb_entry', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER kb_entry_ai AFTER INSERT ON kb_entry BEGIN
  INSERT INTO kb_fts (rowid, title, body, terms) VALUES (new.id, new.title, new.body, new.terms);
END;
CREATE TRIGGER kb_entry_ad AFTER DELETE ON kb_entry BEGIN
  INSERT INTO kb_fts (kb_fts, rowid, title, body, terms) VALUES ('delete', old.id, old.title, old.body, old.terms);
END;
"""
APP_TABLES = ("chat", "run", "event", "gate", "note", "resolution", "writeup", "message", "kb_entry")


def connect(uri: str) -> sqlite3.Connection:
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


class Database:
    """The app's connection and lock, the run-record backend, and the schema."""

    def __init__(self, uri: str):
        self.uri = uri
        self.conn = connect(uri)        # opened first: it keeps the in-memory database alive for the process
        self.lock = threading.RLock()
        with self.lock, self.conn:
            self.conn.executescript(SCHEMA)
        self.records = SqliteBackend(uri)       # the agent's run records: the run_* tables (S8)
        saver = connect(uri)                    # the checkpointer's tables exist from the start, so a reader never races setup
        from langgraph.checkpoint.sqlite import SqliteSaver

        SqliteSaver(saver).setup()
        saver.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One transaction on the app's connection, under its lock."""
        with self.lock, self.conn:
            yield self.conn

    def rows(self, sql: str, params: tuple | dict = ()) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params)]

    def row(self, sql: str, params: tuple | dict = ()) -> dict[str, Any] | None:
        found = self.rows(sql, params)
        return found[0] if found else None

    def schema(self) -> list[tuple[str, str]]:
        """Every object in the database with its SQL, for comparing two starts."""
        return [(r["name"], r["sql"] or "") for r in self.rows("SELECT name, sql FROM sqlite_master ORDER BY type, name")]

    def counts(self) -> dict[str, int]:
        return {t: self.row(f"SELECT count(*) AS n FROM {t}")["n"] for t in APP_TABLES}

    def close(self) -> None:
        self.records.close()
        self.conn.close()


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def loads(text: str | None) -> Any:
    return json.loads(text) if text else None
