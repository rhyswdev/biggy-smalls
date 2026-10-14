"""Row R1: the app starts on a fresh in-memory database, the checkpointer sets up on it beside the app, two
starts give identical empty databases, errors are problem responses, and nothing is written to disk."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from smalls.api.app import create_app
from smalls.api.settings import Settings


def files_under(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def test_health_and_capabilities(client):
    assert client.get("/health").json() == {"status": "ok"}
    caps = client.get("/capabilities").json()
    assert caps["mode"] == "scripted" and caps["profile"] == "scripted" and set(caps["seams"]) == {f"S{n}" for n in range(1, 9)}
    assert caps["now"].endswith("Z") and caps["checkpoint"]


def test_two_starts_give_identical_empty_databases_and_write_nothing(settings, tmp_path, monkeypatch):
    from dataclasses import replace

    monkeypatch.chdir(tmp_path)
    before = files_under(tmp_path)
    shapes = []
    for _ in range(2):
        app = create_app(replace(settings, db_name=Settings.__dataclass_fields__["db_name"].default_factory()))
        with TestClient(app):
            service = app.state.service
            shapes.append(service.db.schema())
            assert set(service.db.counts().values()) == {0}
            assert service.db.records.runs() == []
    assert shapes[0] == shapes[1]
    assert files_under(tmp_path) == before                     # the committed-runs folder is read, never written


def test_the_checkpointer_sets_up_on_the_shared_database(client):
    service = client.app.state.service
    other = sqlite3.connect(service.db.uri, uri=True)
    names = {r[0] for r in other.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    other.close()
    assert {"checkpoints", "writes", "chat", "event", "run_run", "run_events"} <= names       # the index table is made on first use


def test_an_unknown_path_is_a_problem_response(client):
    r = client.get("/nothing-here")
    assert r.status_code == 404 and r.headers["content-type"] == "application/problem+json"
    assert r.json()["status"] == 404 and r.json()["instance"] == "/nothing-here"
