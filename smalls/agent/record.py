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
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Iterable

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
    rebuilt from the run folders if lost, and it is git-ignored."""

    COLUMNS = ("run_id", "status", "created", "last_event_at", "checkpoint", "arm", "now", "profile", "prompt_sha", "tools_sha",
               "commit_sha", "tags", "cost_usd", "stop_reason")

    def __init__(self, runs_dir: Path):
        self.path = Path(runs_dir) / "index.sqlite"

    def upsert(self, **row: Any) -> None:
        import sqlite3

        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        with conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS runs ({', '.join(c + (' TEXT PRIMARY KEY' if c == 'run_id' else '') for c in self.COLUMNS)})")
            conn.execute(f"INSERT OR REPLACE INTO runs ({', '.join(self.COLUMNS)}) VALUES ({', '.join('?' * len(self.COLUMNS))})",
                         tuple(row.get(c) for c in self.COLUMNS))
        conn.close()

    def rows(self, where: str = "1 = 1", params: tuple = ()) -> list[dict[str, Any]]:
        import sqlite3

        if not self.path.exists():
            return []
        conn = sqlite3.connect(self.path)
        found = [dict(zip(self.COLUMNS, r)) for r in conn.execute(f"SELECT {', '.join(self.COLUMNS)} FROM runs WHERE {where} ORDER BY created", params)]
        conn.close()
        return found


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


class RunRecord:
    def __init__(self, runs_dir: Path, run_id: str):
        self.run_id = check_run_id(run_id)
        self.dir = Path(runs_dir) / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self._evidence_seen: set[tuple[str, str, str]] | None = None

    def path(self, name: str) -> Path:
        return self.dir / name

    def checkpoints(self) -> Path:
        """The checkpoint database. SMALLS_STATE_DIR moves it off the run folder if a mount proves slow."""
        elsewhere = os.environ.get("SMALLS_STATE_DIR")
        if not elsewhere:
            return self.dir / CHECKPOINTS
        folder = Path(elsewhere) / self.run_id
        folder.mkdir(parents=True, exist_ok=True)
        return folder / CHECKPOINTS

    def write_json(self, name: str, data: Any) -> None:
        self.path(name).write_text(json.dumps(data, indent=1, default=str, ensure_ascii=False) + "\n", encoding="utf-8")

    def read_json(self, name: str) -> Any:
        return json.loads(self.path(name).read_text(encoding="utf-8"))

    def write_jsonl(self, name: str, rows: Iterable[dict[str, Any]]) -> None:
        with self.path(name).open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")

    def read_jsonl(self, name: str) -> list[dict[str, Any]]:
        path = self.path(name)
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
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
        with self.path("evidence.jsonl").open("a", encoding="utf-8") as f:
            for row in rows:
                digest = hashlib.sha256(row.text.encode("utf-8")).hexdigest()
                if (call_id, row.handle, digest) in self._evidence_seen:
                    continue
                self._evidence_seen.add((call_id, row.handle, digest))
                f.write(json.dumps({"call_id": call_id, "handle": row.handle, "t_known": row.t_known, "sha256": digest,
                                    "text": row.text}, ensure_ascii=False) + "\n")

    def write_derived(self, values: dict[str, Any], usage_calls: list[dict[str, Any]], summary: dict[str, Any]) -> None:
        """Everything that is read from state: written at the end of a run, and at a pause, and on every
        failed exit, so a failed run does not leave the thinnest record."""
        gate = {g["call_id"]: g for g in values.get("gate_log", [])}
        self.write_jsonl("calls.jsonl", ({**{k: e.get(k) for k in CALL_KEYS}, "gate": gate.get(e["call_id"])}
                                         for e in values.get("ledger", [])))
        self.write_jsonl("notes.jsonl", values.get("ic_notes", []))
        if not self.path("evidence.jsonl").exists():
            self.path("evidence.jsonl").touch()
        self.write_json("board.json", {"as_of": values.get("as_of"), "hypotheses": list((values.get("hypotheses") or {}).values()),
                                       "timeline": values.get("timeline", []), "open_questions": values.get("open_questions", [])})
        self.write_json("report.json", values.get("report"))
        self.write_json("verdict.json", {"verdict": values.get("verdict"), "revisions": values.get("revisions", 0)})
        self.write_json("usage.json", {"calls": usage_calls, "totals": usage_totals(usage_calls)})
        self.write_json("summary.json", summary)

    def missing(self) -> list[str]:
        return [name for name in FILES if not self.path(name).exists()]
