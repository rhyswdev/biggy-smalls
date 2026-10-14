"""Row R2: the run service over the agent track's fixture registry and the scripted model. A chat run pauses
at a gate and resumes on an answer; a second answer gets 409; a gate nobody answered stays pending however
long it waits; a rejection's reason is in the run's notes; the switch never answers an action; and a whole
run writes no file."""

from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

import pytest

import fixture_registry as fx
from smalls.agent.record import RunRecord
from smalls.api import service as service_module


def files_under(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def start(client, *, origin="chat", auto=True) -> str:
    r = client.post("/chats", json={"origin": origin, "trigger": fx.TRIGGER, "auto_approve": auto})
    assert r.status_code == 201, r.text
    chat_id = r.json()["id"]
    settle(client, chat_id)
    return chat_id


def settle(client, chat_id: str) -> dict:
    client.app.state.service.wait(chat_id)
    return client.get(f"/chats/{chat_id}").json()


def record(client, run_id: str) -> RunRecord:
    return RunRecord(None, run_id, backend=client.app.state.service.db.records)


def test_a_chat_run_pauses_at_a_gate_resumes_on_an_answer_and_a_second_answer_gets_409(client):
    chat_id = start(client)
    view = client.get(f"/chats/{chat_id}").json()
    gate = view["pending_gate"]
    assert view["chat"]["status"] == "waiting" and view["chat"]["pending"] == 1
    assert [c["tool"] for c in gate["cards"]] == ["propose_action"]                   # reads ran on the switch; the action waits
    answer = {"interrupt_id": gate["interrupt_id"], "answers": {gate["cards"][0]["call_id"]: {"approve": True}}}
    assert client.post(f"/chats/{chat_id}/gate", json=answer).status_code == 202
    assert client.post(f"/chats/{chat_id}/gate", json=answer).status_code == 409       # the first answer wins
    view = settle(client, chat_id)
    assert view["chat"]["status"] == "finished" and view["runs"][0]["status"] == "completed" and view["pending_gate"] is None
    types = [e["type"] for e in view["events"]]
    assert types[0] == "run_started" and types[-1] == "run_finished" and [e["seq"] for e in view["events"]] == list(range(1, len(types) + 1))
    decided = [e["data"] for e in view["events"] if e["type"] == "gate_decision" and e["data"]["tool"] == "propose_action"]
    assert decided[-1]["by"] == {"who": "human", "via": "web"}


def test_two_answers_at_once_resume_the_run_once(client):
    chat_id = start(client)
    gate = client.get(f"/chats/{chat_id}").json()["pending_gate"]
    answer = {"interrupt_id": gate["interrupt_id"], "answers": {gate["cards"][0]["call_id"]: {"approve": True}}}
    codes: list[int] = []
    barrier = threading.Barrier(4)

    def post():
        barrier.wait()
        codes.append(client.post(f"/chats/{chat_id}/gate", json=answer).status_code)

    threads = [threading.Thread(target=post) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    view = settle(client, chat_id)
    assert sorted(codes) == [202, 409, 409, 409]
    assert sum(1 for e in view["events"] if e["type"] == "phase" and e["data"]["name"] == "resumed") == 1


def test_a_gate_nobody_answers_stays_pending_an_hour_later(client, monkeypatch):
    chat_id = start(client)
    later = service_module.utcnow() + timedelta(hours=1)
    monkeypatch.setattr(service_module, "utcnow", lambda: later)
    for path in ("/chats", f"/chats/{chat_id}", "/capabilities"):
        assert client.get(path).status_code == 200
    view = settle(client, chat_id)
    service = client.app.state.service
    run_id = view["runs"][0]["run_id"]
    assert view["chat"]["status"] == "waiting" and view["pending_gate"]["decided_at"] is None
    assert service.runners[run_id].graph.get_state(service.runners[run_id].config).interrupts      # the run still waits at its interrupt


def test_a_rejection_needs_a_reason_and_the_reason_is_in_the_runs_notes(client):
    chat_id = start(client)
    gate = client.get(f"/chats/{chat_id}").json()["pending_gate"]
    call_id = gate["cards"][0]["call_id"]
    bad = client.post(f"/chats/{chat_id}/gate", json={"interrupt_id": gate["interrupt_id"], "answers": {call_id: {"approve": False}}})
    assert bad.status_code == 422 and bad.headers["content-type"] == "application/problem+json"
    assert client.get(f"/chats/{chat_id}").json()["pending_gate"]["decided_at"] is None          # a bad answer decides nothing
    reason = "Not during business hours."
    ok = client.post(f"/chats/{chat_id}/gate", json={"interrupt_id": gate["interrupt_id"], "answers": {call_id: {"approve": False, "reason": reason}}})
    assert ok.status_code == 202
    view = settle(client, chat_id)
    notes = record(client, view["runs"][0]["run_id"]).read_jsonl("notes.jsonl")
    assert [n["text"] for n in notes] == [reason] and notes[0]["handle"].startswith("ic:note/")


def test_the_switch_answers_a_gate_of_reads_and_never_an_action(client):
    chat_id = start(client, auto=False)
    gate = client.get(f"/chats/{chat_id}").json()["pending_gate"]
    assert [c["tool"] for c in gate["cards"]] == ["list_alerts", "list_changes", "read_chat", "search_logs", "search_logs"]   # off: the first round asks
    first = {c["call_id"] for c in gate["cards"]}
    switched = client.put(f"/chats/{chat_id}/auto-approve", json={"on": True}).json()
    assert switched["answered"]["status"] == "resumed"
    view = settle(client, chat_id)
    gate = view["pending_gate"]
    assert [c["tool"] for c in gate["cards"]] == ["propose_action"] and view["chat"]["auto_approve"] is True
    assert client.put(f"/chats/{chat_id}/auto-approve", json={"on": True}).json()["answered"] is None
    assert settle(client, chat_id)["pending_gate"]["interrupt_id"] == gate["interrupt_id"]           # still the person's
    answered = [e["data"] for e in view["events"] if e["type"] == "gate_decision" and e["data"]["call_id"] in first]
    assert len(answered) == 5 and {d["by"]["who"] for d in answered} == {"human"}                     # the standing choice is the commander's


def test_run_agent_is_unattended_and_defers_its_action(client):
    chat_id = start(client, origin="headless")
    view = client.get(f"/chats/{chat_id}").json()
    assert view["chat"]["status"] == "finished" and view["pending_gate"] is None
    report = next(e["data"]["report"] for e in reversed(view["events"]) if e["type"] == "report")
    [proposal] = report["proposed_actions"]
    assert (proposal["decision"], proposal["by"]) == ("deferred", {"who": "policy", "via": "headless"})


def test_a_whole_run_writes_no_file(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = files_under(tmp_path)
    chat_id = start(client)
    gate = client.get(f"/chats/{chat_id}").json()["pending_gate"]
    client.post(f"/chats/{chat_id}/gate", json={"interrupt_id": gate["interrupt_id"], "answers": {gate["cards"][0]["call_id"]: {"defer": True}}})
    view = settle(client, chat_id)
    assert view["runs"][0]["status"] == "completed" and files_under(tmp_path) == before
    assert record(client, view["runs"][0]["run_id"]).missing() == []


def test_a_note_on_a_finished_run_continues_the_same_run(client):
    chat_id = start(client, origin="headless")
    run_id = client.get(f"/chats/{chat_id}").json()["runs"][0]["run_id"]
    r = client.post(f"/chats/{chat_id}/notes", json={"text": "Who should own the fix?"})
    assert r.status_code == 202 and r.json()["delivery"] == "follow-up"
    view = settle(client, chat_id)
    assert [run["run_id"] for run in view["runs"]] == [run_id] and view["runs"][0]["status"] == "completed"
    types = [e["type"] for e in view["events"]]
    assert "followup_finished" in types and types.count("run_started") == 1 and types.count("phase") > 0
    assert view["notes"][0]["delivered_at"] is not None


def test_stop_and_resume(client, monkeypatch):
    from dataclasses import replace

    service = client.app.state.service
    service.cfg = replace(service.cfg, investigator=service.cfg.investigator.with_params(delay_s=0.01))
    r = client.post("/chats", json={"origin": "headless", "trigger": fx.TRIGGER})
    chat_id = r.json()["id"]
    import time
    for _ in range(100):
        if client.get(f"/chats/{chat_id}").json()["runs"][0]["status"] == "running":
            break
        time.sleep(0.01)
    assert client.post(f"/chats/{chat_id}/stop").status_code == 202
    view = settle(client, chat_id)
    assert view["runs"][0]["status"] == "stopped" and view["chat"]["status"] == "stopped"
    assert client.post(f"/chats/{chat_id}/runs", json={"resume": True}).status_code == 202
    assert settle(client, chat_id)["runs"][0]["status"] == "completed"
    assert client.post(f"/chats/{chat_id}/stop").status_code == 409


def test_an_edit_that_does_not_validate_is_refused_before_the_gate_is_decided(client):
    chat_id = start(client, auto=False)
    gate = client.get(f"/chats/{chat_id}").json()["pending_gate"]
    call_id = gate["cards"][0]["call_id"]
    r = client.post(f"/chats/{chat_id}/gate", json={"interrupt_id": gate["interrupt_id"], "answers": {call_id: {"approve": True, "args": {"since": 5}}}})
    assert r.status_code == 422 and "not valid" in r.json()["detail"]
    assert client.get(f"/chats/{chat_id}").json()["pending_gate"]["decided_at"] is None
