"""The scripted mode's generic dev script runs over the real bundle's tools: it reads, writes the board,
proposes one action that waits for the commander, and submits an inconclusive report with a verdict for every
swept alert. It knows nothing about the data, so the bundle-string lint covers it like the rest of the API."""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from smalls.api.app import create_app
from smalls.api.settings import DEV_SCRIPT
from smalls.contracts.report import Report


def test_the_dev_script_runs_a_chat_over_the_real_bundle(settings, monkeypatch):
    monkeypatch.delenv("SMALLS_TAGS", raising=False)
    real = replace(settings, registry=None, script=DEV_SCRIPT, clock=None)
    with TestClient(create_app(real)) as client:
        chat_id = client.post("/chats", json={"origin": "chat", "auto_approve": True}).json()["id"]      # the checkpoint's own trigger
        service = client.app.state.service
        service.wait(chat_id)
        gate = client.get(f"/chats/{chat_id}").json()["pending_gate"]
        assert [c["tool"] for c in gate["cards"]] == ["propose_action"]
        client.post(f"/chats/{chat_id}/gate", json={"interrupt_id": gate["interrupt_id"],
                                                     "answers": {gate["cards"][0]["call_id"]: {"approve": False, "reason": "Not now."}}})
        service.wait(chat_id)
        view = client.get(f"/chats/{chat_id}").json()
        report = Report.model_validate(next(e["data"]["report"] for e in reversed(view["events"]) if e["type"] == "report"))
        assert view["runs"][0]["status"] == "completed" and report.alerts and all(a.verdict == "noise" for a in report.alerts)
        assert report.proposed_actions[0].decision == "rejected" and report.validation is not None
        assert view["runs"][0]["trigger"] == real.trigger()
