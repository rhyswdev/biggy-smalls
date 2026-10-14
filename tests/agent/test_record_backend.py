"""Seam S8 (docs/decisions/ui-build.md section 8): run records and checkpoints in SQLite. A scripted run over the
fixture registry on a shared in-memory database writes no file; exported to the file layout it replays the
same as a run that wrote files; and resume, carry-forward, the index and the next event number all read
through the backend."""

from __future__ import annotations

import io
import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from rich.console import Console

import fixture_registry as fx
import fixture_script
from smalls.agent import __main__ as cli
from smalls.agent.config import load_config
from smalls.agent.record import CHECKPOINTS, FILES, RunRecord, SqliteBackend
from smalls.agent.render import Renderer
from smalls.agent.runner import Runner, module_toolbox

UNSTABLE = {"t_wall", "latency_ms", "wall_s", "wall_s_total", "wall_s_last_segment", "seconds", "asked_at", "decided_at", "wait_ms",
            "submitted_at", "interrupt_id", "created", "model_latency_ms"}


@pytest.fixture
def uri():
    """A fresh shared in-memory database, kept alive by one connection for the test, as the API keeps it."""
    name = f"file:/s8-{uuid.uuid4().hex[:8]}?vfs=memdb"
    holder = sqlite3.connect(name, uri=True)
    yield name
    holder.close()


def approve(cards):
    return {c["call_id"]: {"approve": True} for c in cards}


def files_under(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def stable(node):
    if isinstance(node, dict):
        return {k: stable(v) for k, v in node.items() if k not in UNSTABLE}
    if isinstance(node, list):
        return [stable(v) for v in node]
    return node


def steady(node):
    """The same tree with every timing value set to zero, so two renders of one run can be compared."""
    if isinstance(node, dict):
        return {k: (0 if k in UNSTABLE and isinstance(v, (int, float)) else steady(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [steady(v) for v in node]
    return node


def rendered(events) -> str:
    from dataclasses import replace

    out = io.StringIO()
    renderer = Renderer(Console(file=out, width=120, highlight=False, color_system=None))
    for event in events:
        renderer.emit(replace(event, data=steady(event.data)))
    return out.getvalue()


def test_a_run_on_a_shared_in_memory_database_writes_no_file_and_exports_to_the_same_replay(tmp_path, monkeypatch, uri):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SMALLS_STATE_DIR", raising=False)
    runs, elsewhere = tmp_path / "runs", tmp_path / "files"
    cfg = load_config(runs_dir=runs)
    backend = SqliteBackend(uri)
    r = Runner(cfg, module_toolbox("fixture_registry"), "s8", script=fixture_script.turn, decider=approve, backend=backend)
    assert r.start(fx.TRIGGER, fx.NOW)["status"] == "completed"
    r.close()
    assert files_under(tmp_path) == [] and r.dir is None and r.record.missing() == []          # nothing on disk, every file held
    assert backend.runs() == ["s8"] and backend.index().rows()[0]["status"] == "completed"
    exported = r.record.export(tmp_path / "exported")
    assert sorted(p.name for p in exported.iterdir()) == sorted(FILES)

    file_cfg = load_config(runs_dir=elsewhere)
    f = Runner(file_cfg, module_toolbox("fixture_registry"), "s8", script=fixture_script.turn, decider=approve)
    f.start(fx.TRIGGER, fx.NOW)
    f.close()
    assert sorted(p.name for p in f.dir.iterdir()) == sorted([*FILES, CHECKPOINTS])
    from_sql = RunRecord(tmp_path / "exported", "s8").events()
    from_files = RunRecord(elsewhere, "s8").events()
    assert [(e.seq, e.type, stable(e.data)) for e in from_sql] == [(e.seq, e.type, stable(e.data)) for e in from_files]
    assert rendered(from_sql) == rendered(from_files)
    for name in ("report.json", "board.json", "calls.jsonl"):
        mine, theirs = (tmp_path / "exported" / "s8" / name).read_text(), (elsewhere / "s8" / name).read_text()
        as_json = (lambda t: [json.loads(x) for x in t.splitlines()]) if name.endswith(".jsonl") else json.loads
        assert stable(as_json(mine)) == stable(as_json(theirs)), name
    capsys_out = io.StringIO()
    monkeypatch.setattr("sys.stdout", capsys_out)
    assert cli.main(["replay", str(tmp_path / "exported" / "s8"), "--no-thinking"]) == 0        # the CLI replays the export
    assert "run_finished" not in capsys_out.getvalue() and capsys_out.getvalue()
    backend.close()


def test_pause_and_resume_read_the_record_and_checkpoints_through_the_backend(tmp_path, monkeypatch, uri):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(runs_dir=tmp_path / "runs")
    first = SqliteBackend(uri)
    r1 = Runner(cfg, module_toolbox("fixture_registry"), "s8-gate", script=fixture_script.turn, stop_at_gate=True, attended=True, backend=first)
    assert r1.start(fx.TRIGGER, fx.NOW)["status"] == "paused"
    paused_at = r1.record.last_seq()
    r1.close()
    first.close()
    second = SqliteBackend(uri)          # a new connection to the same database, as a later request would open
    r2 = Runner(cfg, module_toolbox("fixture_registry"), "s8-gate", script=fixture_script.turn, attended=True, decider=approve, backend=second)
    assert r2.bus.seq == paused_at                                                            # numbering goes on from the backend
    assert r2.resume()["status"] == "completed"
    events = r2.record.events()
    r2.close()
    assert [e.seq for e in events] == list(range(1, len(events) + 1)) and events[-1].type == "run_finished"
    assert files_under(tmp_path) == []
    second.close()


def test_carry_forward_reads_the_earlier_run_through_the_backend(tmp_path, monkeypatch, uri):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(runs_dir=tmp_path / "runs")
    backend = SqliteBackend(uri)
    early = Runner(cfg, module_toolbox("fixture_registry"), "s8-early", script=fixture_script.turn, decider=approve, backend=backend)
    early.start(fx.TRIGGER, fx.EARLY)
    early.close()
    later = Runner(cfg, module_toolbox("fixture_registry"), "s8-later", script=fixture_script.turn, decider=approve, backend=backend)
    assert later.start(fx.TRIGGER, fx.NOW, carry_from="s8-early")["status"] == "completed"
    state = later.state()
    later.close()
    assert state["carried"]["run_id"] == "s8-early" and any(e["by"] == "carry_forward" for e in state["ledger"])
    assert files_under(tmp_path) == []
    backend.close()


def test_a_checkpointer_connection_the_caller_owns_is_used_and_left_open(tmp_path, monkeypatch, uri):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(runs_dir=tmp_path / "runs")
    backend = SqliteBackend(uri)
    mine = sqlite3.connect(uri, uri=True, check_same_thread=False)
    r = Runner(cfg, module_toolbox("fixture_registry"), "s8-conn", script=fixture_script.turn, decider=approve, backend=backend, checkpointer=mine)
    r.start(fx.TRIGGER, fx.NOW)
    r.close()
    assert mine.execute("SELECT count(*) FROM checkpoints WHERE thread_id = 's8-conn'").fetchone()[0] > 0      # still open, and it holds them
    mine.close()
    backend.close()


def test_reading_a_record_creates_no_folder(tmp_path):
    record = RunRecord(tmp_path, "absent")
    assert record.read_text("run.json") is None and record.events() == [] and not (tmp_path / "absent").exists()
