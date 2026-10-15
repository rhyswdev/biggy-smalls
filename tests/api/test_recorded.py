"""Row R5: committed runs. A live run is loaded at start as a finished, read-only past chat, labelled as recorded
with its date; a scripted run is skipped. A run made through the API, exported to the file layout and loaded
back (with the scripted-provider guard off) gives the same event list as the chat it came from."""

from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

import fixture_registry as fx
import fixture_script
from smalls.agent.config import load_config
from smalls.agent.record import FILES
from smalls.agent.runner import Runner, module_toolbox
from smalls.api.app import create_app

COMMITTED = Path(__file__).resolve().parents[2] / "eval" / "runs"


def a_live_run() -> Path:
    """A committed live run: the first one under eval/runs whose run.json names a provider other than scripted."""
    for run in sorted(COMMITTED.glob("*/run.json")):
        if json.loads(run.read_text())["provider"] != "scripted":
            return run.parent
    raise AssertionError("no committed live run")


def test_a_live_run_is_loaded_as_a_recorded_chat_and_a_scripted_one_is_skipped(settings, tmp_path):
    live = a_live_run()
    shutil.copytree(live, settings.runs_dir / live.name)
    scripted = Runner(load_config(runs_dir=settings.runs_dir), module_toolbox("fixture_registry"), "scripted-one",
                      script=fixture_script.turn, decider=lambda cards: {c["call_id"]: {"approve": True} for c in cards})
    scripted.start(fx.TRIGGER, fx.NOW)
    scripted.close()
    with TestClient(create_app(settings)) as client:
        chats = client.get("/chats").json()
        assert [c["origin"] for c in chats] == ["past"] and client.app.state.service.recorded == [live.name]
        [past] = chats
        assert past["recorded"] == json.loads((live / "run.json").read_text())["created"] and past["status"] == "finished"
        assert past["recorded_run"]["tool_calls"] == json.loads((live / "summary.json").read_text())["tool_calls"]
        # The dashboard's count of reads is the chats list's: reads that came back ok, not the budget's tool calls.
        calls = [json.loads(line) for line in (live / "calls.jsonl").read_text().splitlines()]
        ran = sum(c["kind"] == "read" and c["by"] == "agent" and c["status"] == "ok" for c in calls)
        assert past["recorded_run"]["reads"] == past["tally"]["run"] == ran
        assert client.get("/capabilities").json()["last_recorded"]["run_id"] == live.name
        view = client.get(f"/chats/{past['id']}").json()
        stored = [json.loads(line) for line in (live / "events.jsonl").read_text().splitlines()]
        assert [(e["type"], e["data"]) for e in view["events"]] == [(e["type"], e["data"]) for e in stored]
        assert client.post(f"/chats/{past['id']}/notes", json={"text": "again?"}).status_code == 409          # read-only
        assert client.post(f"/chats/{past['id']}/runs", json={}).status_code == 409


def test_a_run_made_here_exports_and_loads_back_as_the_same_chat(settings, tmp_path):
    with TestClient(create_app(settings)) as client:
        chat_id = client.post("/chats", json={"origin": "chat", "trigger": fx.TRIGGER, "auto_approve": True}).json()["id"]
        service = client.app.state.service
        service.wait(chat_id)
        gate = client.get(f"/chats/{chat_id}").json()["pending_gate"]
        client.post(f"/chats/{chat_id}/gate", json={"interrupt_id": gate["interrupt_id"], "answers": {gate["cards"][0]["call_id"]: {"approve": True}}})
        service.wait(chat_id)
        live = client.get(f"/chats/{chat_id}").json()
        run_id = live["runs"][0]["run_id"]
        exported = client.post(f"/runs/{run_id}/export")
        assert exported.status_code == 201 and sorted(p.name for p in Path(exported.json()["folder"]).iterdir()) == sorted(FILES)
        assert client.post(f"/runs/{run_id}/export").status_code == 409                  # never overwritten
        assert client.post("/runs/no-such-run/export").status_code == 404
    with TestClient(create_app(replace(settings, load_scripted=True))) as again:
        [past] = again.get("/chats").json()
        loaded = again.get(f"/chats/{past['id']}").json()
    assert past["origin"] == "past" and loaded["runs"][0]["run_id"] == run_id
    assert [(e["seq"], e["type"], e["data"]) for e in loaded["events"]] == [(e["seq"], e["type"], e["data"]) for e in live["events"]]
    assert past["tally"] == live["chat"]["tally"]
