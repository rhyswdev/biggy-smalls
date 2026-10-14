"""The run record: the files a run leaves in `eval/runs/<run_id>/` (docs/decisions/tools-agent-build.md
section 7.2). Every eval layer reads these, and a replay needs nothing else.

| File                | Rows or keys                                                                       |
|---------------------|------------------------------------------------------------------------------------|
| run.json            | what the run was: id, arm, checkpoint, now, bundle, profile, commit, hashes, flags |
| events.jsonl        | every event; replayable with no model                                              |
| calls.jsonl         | one row per call, with its gate decision                                           |
| evidence.jsonl      | every row a tool showed, written at call time: what the model actually saw         |
| notes.jsonl         | the incident commander's notes and rejection reasons                               |
| board.json          | the board as it stood at the end, with its per-change history                      |
| report.json         | the report, with the fields code writes                                            |
| verdict.json        | the verifier's verdict and the revision diff                                       |
| usage.json          | per model call: tokens, cost, latency                                              |
| summary.json        | totals, gate waits, retries, process metrics                                       |
| checkpoints.sqlite  | the checkpointer; git-ignored                                                      |

A run record stores the names of the key variables that were set, never their values.

Where a record lives is a backend (seam S8 in docs/decisions/ui-build.md). `FileBackend` is the layout
above, under a runs folder, with the index in `index.sqlite` beside the run folders: the default, and what
the CLI, the eval and the scorer read. `SqliteBackend` keeps the same files as tables of one SQLite
database, one table per file and one for the index, keyed by run id, so a server can hold every run in
memory and write nothing to disk; `RunRecord.export` writes such a run out in the file layout. Everything
that reads a record (resume, carry-forward, replay, the next event number) reads through the backend.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Iterable

from smalls.agent.events import Event, parse_events

RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
KEY_NAME = re.compile(r"(API_KEY|TOKEN|SECRET|PASSWORD)", re.I)
PACKAGES = ("langgraph", "langgraph-checkpoint-sqlite", "langchain", "langchain-core", "langchain-anthropic", "pydantic")
FILES = ("run.json", "events.jsonl", "calls.jsonl", "evidence.jsonl", "notes.jsonl", "board.json", "report.json", "verdict.json",
         "usage.json", "summary.json")
CHECKPOINTS = "checkpoints.sqlite"
CALL_KEYS = ("call_id", "round", "by", "tool", "kind", "args", "rationale", "status", "shown", "referenced", "native_query",
             "window", "chars", "latency_ms", "repeat_of", "used_tag_input")


def new_run_id() -> str:
    """Minted here, never taken from a caller as a path."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:6]


def check_run_id(run_id: str) -> str:
    if not RUN_ID.match(run_id):
        raise SystemExit(f"not a run id: {run_id!r} (letters, digits, dot, dash and underscore only)")
    return run_id


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def key_names() -> list[str]:
    """The names of the key-like variables that are set. Names only."""
    return sorted(name for name, value in os.environ.items() if KEY_NAME.search(name) and value)


def commit() -> str:
    """The commit a run was made at: from SMALLS_COMMIT, which the Makefile sets, because the container has
    no git; then from git; then `unknown`."""
    given = os.environ.get("SMALLS_COMMIT", "").strip()
    if given:
        return given
    try:
        found = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        found = ""
    return found or "unknown"


def versions() -> dict[str, str]:
    out = {}
    for package in PACKAGES:
        try:
            out[package] = version(package)
        except PackageNotFoundError:
            out[package] = "not installed"
    return out


def scrub(text: str) -> str:
    """A text with the value of every key-like variable taken out, so an error message can be recorded."""
    for name, value in os.environ.items():
        if KEY_NAME.search(name) and value and len(value) >= 8:
            text = text.replace(value, f"<{name}>")
    return text


class RunsIndex:
    """`eval/runs/index.sqlite`: one row per run, kept current at start, pause, finish and failure. It is
    rebuilt from the run folders if lost, and it is git-ignored. Under the SQLite backend the same rows are
    a table of that database."""

    COLUMNS = ("run_id", "status", "created", "last_event_at", "checkpoint", "arm", "now", "profile", "prompt_sha", "tools_sha",
               "commit_sha", "tags", "cost_usd", "stop_reason")

    def __init__(self, runs_dir: Path | None = None, *, conn: sqlite3.Connection | None = None, table: str = "runs",
                 lock: threading.Lock | None = None):
        self.path = Path(runs_dir) / "index.sqlite" if runs_dir is not None else None
        self.conn, self.table, self.lock = conn, table, lock or threading.Lock()

    def _ddl(self) -> str:
        return f"CREATE TABLE IF NOT EXISTS {self.table} ({', '.join(c + (' TEXT PRIMARY KEY' if c == 'run_id' else '') for c in self.COLUMNS)})"

    def upsert(self, **row: Any) -> None:
        sql = f"INSERT OR REPLACE INTO {self.table} ({', '.join(self.COLUMNS)}) VALUES ({', '.join('?' * len(self.COLUMNS))})"
        if self.conn is not None:
            with self.lock, self.conn:
                self.conn.execute(self._ddl())
                self.conn.execute(sql, tuple(row.get(c) for c in self.COLUMNS))
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        with conn:
            conn.execute(self._ddl())
            conn.execute(sql, tuple(row.get(c) for c in self.COLUMNS))
        conn.close()

    def rows(self, where: str = "1 = 1", params: tuple = ()) -> list[dict[str, Any]]:
        sql = f"SELECT {', '.join(self.COLUMNS)} FROM {self.table} WHERE {where} ORDER BY created"
        if self.conn is not None:
            with self.lock:
                self.conn.execute(self._ddl())
                return [dict(zip(self.COLUMNS, r)) for r in self.conn.execute(sql, params)]
        if not self.path.exists():
            return []
        conn = sqlite3.connect(self.path)
        found = [dict(zip(self.COLUMNS, r)) for r in conn.execute(sql, params)]
        conn.close()
        return found


class FileBackend:
    """The run record as files: a folder per run under `runs_dir`, the index in `index.sqlite` beside them,
    and each run's checkpoints in its folder (or under SMALLS_STATE_DIR)."""

    kind = "files"

    def __init__(self, runs_dir: Path | str):
        self.runs_dir = Path(runs_dir)

    def folder(self, run_id: str, create: bool = True) -> Path:
        folder = self.runs_dir / run_id
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        return folder

    def read_text(self, run_id: str, name: str) -> str | None:
        path = self.runs_dir / run_id / name
        return path.read_text(encoding="utf-8") if path.exists() else None

    def write_text(self, run_id: str, name: str, text: str) -> None:
        (self.folder(run_id) / name).write_text(text, encoding="utf-8")

    def append_lines(self, run_id: str, name: str, lines: Iterable[str]) -> None:
        with (self.folder(run_id) / name).open("a", encoding="utf-8") as f:
            for line in lines:
                f.write(line + "\n")

    def exists(self, run_id: str, name: str) -> bool:
        return (self.runs_dir / run_id / name).exists()

    def size(self, run_id: str, name: str) -> int:
        path = self.runs_dir / run_id / name
        return path.stat().st_size if path.exists() else 0

    def checkpoints(self, run_id: str) -> Path:
        """The checkpoint database. SMALLS_STATE_DIR moves it off the run folder if a mount proves slow."""
        elsewhere = os.environ.get("SMALLS_STATE_DIR")
        if not elsewhere:
            return self.folder(run_id) / CHECKPOINTS
        folder = Path(elsewhere) / run_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder / CHECKPOINTS

    def checkpointer(self, run_id: str) -> sqlite3.Connection:
        return sqlite3.connect(self.checkpoints(run_id), check_same_thread=False)

    def index(self) -> RunsIndex:
        return RunsIndex(self.runs_dir)

    def runs(self) -> list[str]:
        """Every run with a `run.json`, by id."""
        return sorted(p.parent.name for p in self.runs_dir.glob("*/run.json")) if self.runs_dir.is_dir() else []

    def close(self) -> None:
        pass


def _table(name: str) -> str:
    return "run_" + name.split(".", 1)[0]


class SqliteBackend:
    """The run record as tables of one SQLite database: a JSON file is one row per run (`run_id`, `body`),
    a JSON-lines file is one row per line (`run_id`, `n`, `line`), and the index is `run_index`. Opened by
    URI, so a server can give it the shared in-memory database (`file:/<name>?vfs=memdb`), and each run's
    checkpointer gets its own connection to the same database. Every statement on the backend's own
    connection is taken under one lock, because a run writes events from more than one thread."""

    kind = "sqlite"

    def __init__(self, uri: str, *, timeout: float = 5.0):
        self.uri, self.timeout = uri, timeout
        self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=timeout)
        self.lock = threading.Lock()
        with self.lock, self.conn:
            for name in FILES:
                if name.endswith(".jsonl"):
                    self.conn.execute(f"CREATE TABLE IF NOT EXISTS {_table(name)} (run_id TEXT NOT NULL, n INTEGER NOT NULL, line TEXT NOT NULL, "
                                      "PRIMARY KEY (run_id, n))")
                else:
                    self.conn.execute(f"CREATE TABLE IF NOT EXISTS {_table(name)} (run_id TEXT PRIMARY KEY, body TEXT NOT NULL)")

    def folder(self, run_id: str, create: bool = True) -> None:
        return None

    def _lines(self, run_id: str, name: str) -> list[str]:
        return [r[0] for r in self.conn.execute(f"SELECT line FROM {_table(name)} WHERE run_id = ? ORDER BY n", (run_id,))]

    def read_text(self, run_id: str, name: str) -> str | None:
        with self.lock:
            if name.endswith(".jsonl"):
                lines = self._lines(run_id, name)
                return "".join(line + "\n" for line in lines) if lines or self._known(run_id) else None
            row = self.conn.execute(f"SELECT body FROM {_table(name)} WHERE run_id = ?", (run_id,)).fetchone()
            return row[0] if row else None

    def _known(self, run_id: str) -> bool:
        """A JSON-lines file exists, perhaps empty, once its run has a `run.json` or any event."""
        return bool(self.conn.execute("SELECT 1 FROM run_run WHERE run_id = ? UNION ALL SELECT 1 FROM run_events WHERE run_id = ? LIMIT 1",
                                      (run_id, run_id)).fetchone())

    def write_text(self, run_id: str, name: str, text: str) -> None:
        with self.lock, self.conn:
            if name.endswith(".jsonl"):
                self.conn.execute(f"DELETE FROM {_table(name)} WHERE run_id = ?", (run_id,))
                self.conn.executemany(f"INSERT INTO {_table(name)} (run_id, n, line) VALUES (?, ?, ?)",
                                      [(run_id, n, line) for n, line in enumerate(text.splitlines(), 1) if line.strip()])
            else:
                self.conn.execute(f"INSERT OR REPLACE INTO {_table(name)} (run_id, body) VALUES (?, ?)", (run_id, text))

    def append_lines(self, run_id: str, name: str, lines: Iterable[str]) -> None:
        with self.lock, self.conn:
            last = self.conn.execute(f"SELECT coalesce(max(n), 0) FROM {_table(name)} WHERE run_id = ?", (run_id,)).fetchone()[0]
            self.conn.executemany(f"INSERT INTO {_table(name)} (run_id, n, line) VALUES (?, ?, ?)",
                                  [(run_id, last + i, line) for i, line in enumerate(lines, 1)])

    def exists(self, run_id: str, name: str) -> bool:
        return self.read_text(run_id, name) is not None

    def size(self, run_id: str, name: str) -> int:
        return len((self.read_text(run_id, name) or "").encode("utf-8"))

    def checkpointer(self, run_id: str) -> sqlite3.Connection:
        """A connection of the run's own to the same database: the checkpointer keeps its own lock and its own
        tables (`checkpoints`, `writes`), and SQLite's locking keeps the two apart."""
        return sqlite3.connect(self.uri, uri=True, check_same_thread=False, timeout=self.timeout)

    def index(self) -> RunsIndex:
        return RunsIndex(conn=self.conn, table="run_index", lock=self.lock)

    def runs(self) -> list[str]:
        with self.lock:
            return [r[0] for r in self.conn.execute("SELECT run_id FROM run_run ORDER BY run_id")]

    def close(self) -> None:
        self.conn.close()


def cost(call: dict[str, Any], price: dict[str, float]) -> float:
    """One model call priced by the model that served it. The input count includes cached tokens, and the
    plain cache-write count is zeroed when the per-lifetime split is present, so each token is priced once."""
    usage = call.get("usage") or {}
    details = usage.get("input_token_details") or {}
    read = details.get("cache_read", 0) or 0
    write_5m = (details.get("cache_creation", 0) or 0) + (details.get("ephemeral_5m_input_tokens", 0) or 0)
    write_1h = details.get("ephemeral_1h_input_tokens", 0) or 0
    uncached = max((usage.get("input_tokens", 0) or 0) - read - write_5m - write_1h, 0)
    total = (uncached * price["input"] + read * price["cache_read"] + write_5m * price["cache_write"]
             + write_1h * price["cache_write_1h"] + (usage.get("output_tokens", 0) or 0) * price["output"])
    return round(total / 1e6, 6)


def usage_totals(calls: list[dict[str, Any]]) -> dict[str, Any]:
    def detail(call: dict[str, Any], key: str) -> int:
        return ((call.get("usage") or {}).get("input_token_details") or {}).get(key, 0) or 0

    totals: dict[str, Any] = {k: sum(((c.get("usage") or {}).get(k, 0) or 0) for c in calls) for k in ("input_tokens", "output_tokens")}
    totals["cache_read"] = sum(detail(c, "cache_read") for c in calls)
    totals["cache_write"] = sum(detail(c, "cache_creation") + detail(c, "ephemeral_5m_input_tokens") + detail(c, "ephemeral_1h_input_tokens")
                                for c in calls)
    totals["reasoning"] = sum((((c.get("usage") or {}).get("output_token_details") or {}).get("reasoning", 0) or 0) for c in calls)
    totals["cost_usd"] = round(sum(c.get("cost_usd", 0.0) for c in calls), 6)
    totals["model_latency_ms"] = round(sum(c.get("latency_ms", 0.0) for c in calls), 1)
    return totals


class RecordSink:
    """The events file of a record, as an event sink: one line per event, written through the backend."""

    def __init__(self, record: "RunRecord"):
        self.record = record

    def emit(self, ev: Event) -> None:
        self.record.backend.append_lines(self.record.run_id, "events.jsonl", [ev.to_json()])

    def close(self) -> None:
        pass


class RunRecord:
    def __init__(self, runs_dir: Path | None, run_id: str, backend: FileBackend | SqliteBackend | None = None):
        self.run_id = check_run_id(run_id)
        self.backend = backend or FileBackend(runs_dir)
        self.dir = self.backend.folder(self.run_id, create=False)       # None when the record is not files; made on first write
        self._evidence_seen: set[tuple[str, str, str]] | None = None

    def path(self, name: str) -> Path:
        if self.dir is None:
            raise TypeError(f"run {self.run_id} is held in a {self.backend.kind} backend and has no files")
        return self.dir / name

    def checkpoints(self) -> Path:
        return self.backend.checkpoints(self.run_id)

    def read_text(self, name: str) -> str | None:
        return self.backend.read_text(self.run_id, name)

    def write_json(self, name: str, data: Any) -> None:
        self.backend.write_text(self.run_id, name, json.dumps(data, indent=1, default=str, ensure_ascii=False) + "\n")

    def read_json(self, name: str) -> Any:
        text = self.read_text(name)
        if text is None:
            raise FileNotFoundError(f"run {self.run_id} has no {name}")
        return json.loads(text)

    def write_jsonl(self, name: str, rows: Iterable[dict[str, Any]]) -> None:
        self.backend.write_text(self.run_id, name, "".join(json.dumps(row, default=str, ensure_ascii=False) + "\n" for row in rows))

    def events_sink(self):
        """The sink a run's bus writes its events through: an open file for files, rows for SQLite."""
        if self.dir is not None:
            from smalls.agent.events import JsonlSink

            return JsonlSink(self.path("events.jsonl"))
        return RecordSink(self)

    def events(self) -> list[Event]:
        return parse_events(self.read_text("events.jsonl") or "")

    def last_seq(self) -> int:
        """The highest event number so far, so a resumed run goes on numbering where the last one stopped."""
        return max((e.seq for e in self.events()), default=0)

    def size(self, name: str) -> int:
        return self.backend.size(self.run_id, name)

    def export(self, runs_dir: Path | str) -> Path:
        """Write this record out in the file layout, under `runs_dir`, so a run held in SQLite can be
        committed and read by the CLI, the eval and the scorer. The checkpoints are not exported: they are
        git-ignored in the file layout too."""
        folder = Path(runs_dir) / self.run_id
        folder.mkdir(parents=True, exist_ok=True)
        for name in FILES:
            text = self.read_text(name)
            if text is not None:
                (folder / name).write_text(text, encoding="utf-8")
        return folder

    def read_jsonl(self, name: str) -> list[dict[str, Any]]:
        text = self.read_text(name)
        if text is None:
            return []
        out = []
        for line in text.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:      # a torn last line from a crashed run
                continue
        return out

    def evidence(self, call_id: str, rows: Iterable[Any]) -> None:
        """Append the rows one call showed: `{call_id, handle, t_known, sha256, text}`. Written at call
        time, so it is what the model saw even if the data later changes. A node that re-runs after a crash
        writes nothing twice."""
        if self._evidence_seen is None:
            self._evidence_seen = {(r["call_id"], r["handle"], r["sha256"]) for r in self.read_jsonl("evidence.jsonl")}
        lines = []
        for row in rows:
            digest = hashlib.sha256(row.text.encode("utf-8")).hexdigest()
            if (call_id, row.handle, digest) in self._evidence_seen:
                continue
            self._evidence_seen.add((call_id, row.handle, digest))
            lines.append(json.dumps({"call_id": call_id, "handle": row.handle, "t_known": row.t_known, "sha256": digest,
                                     "text": row.text}, ensure_ascii=False))
        self.backend.append_lines(self.run_id, "evidence.jsonl", lines)

    def write_derived(self, values: dict[str, Any], usage_calls: list[dict[str, Any]], summary: dict[str, Any]) -> None:
        """Everything that is read from state: written at the end of a run, and at a pause, and on every
        failed exit, so a failed run does not leave the thinnest record."""
        gate = {g["call_id"]: g for g in values.get("gate_log", [])}
        self.write_jsonl("calls.jsonl", ({**{k: e.get(k) for k in CALL_KEYS}, "gate": gate.get(e["call_id"])}
                                         for e in values.get("ledger", [])))
        self.write_jsonl("notes.jsonl", values.get("ic_notes", []))
        if not self.backend.exists(self.run_id, "evidence.jsonl"):
            self.backend.write_text(self.run_id, "evidence.jsonl", "")
        self.write_json("board.json", {"as_of": values.get("as_of"), "hypotheses": list((values.get("hypotheses") or {}).values()),
                                       "timeline": values.get("timeline", []), "open_questions": values.get("open_questions", [])})
        self.write_json("report.json", values.get("report"))
        self.write_json("verdict.json", {"verdict": values.get("verdict"), "revisions": values.get("revisions", 0)})
        self.write_json("usage.json", {"calls": usage_calls, "totals": usage_totals(usage_calls)})
        self.write_json("summary.json", summary)

    def missing(self) -> list[str]:
        return [name for name in FILES if not self.backend.exists(self.run_id, name)]
