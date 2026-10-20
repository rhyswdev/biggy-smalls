"""v4 plan row 1: the service desk endpoints over the real bundle. The open list holds only open P1 and P2
incidents; an unknown number is a 404 with a sentence the dialog shows; a known one makes one diagnosis chat
and starts it unattended; a second import returns the same chat; the ticket's notes come newest first, in UTC
with the source's local time beside, and none is from after `now`. Values are read from the data at test time,
so no incident detail is written here."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

import fixture_registry as fx
from smalls.api.app import create_app
from smalls.api.settings import DEV_SCRIPT


@pytest.fixture
def real(settings, monkeypatch):
    monkeypatch.delenv("SMALLS_TAGS", raising=False)
    with TestClient(create_app(replace(settings, registry=None, script=DEV_SCRIPT, clock=None))) as c:
        yield c


def when(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def test_the_open_list_holds_open_p1_and_p2_incidents_not_yet_imported(real):
    body = real.get("/incidents/open").json()
    assert body["connected"] is True and body["incidents"]
    for card in body["incidents"]:
        assert card["priority_code"] in ("1", "2") and card["active"] and card["imported"] is False and card["chat_id"] is None
    assert real.get("/incidents").json() == []


def test_an_unknown_number_is_a_404_the_dialog_can_show(real):
    r = real.post("/incidents/import", json={"number": "INC0000001"})
    assert r.status_code == 404 and r.headers["content-type"] == "application/problem+json"
    assert "INC0000001" in r.json()["detail"] and r.json()["detail"].endswith(".")
    assert real.get("/incidents").json() == []


def test_import_starts_one_unattended_diagnosis_and_a_repeat_returns_it(real):
    service = real.app.state.service
    number = real.get("/incidents/open").json()["incidents"][0]["number"]
    first = real.post("/incidents/import", json={"number": f"https://desk.example/nav_to.do?uri=incident.do?number={number.lower()}"})
    assert first.status_code == 201 and first.json()["number"] == number and first.json()["already"] is False
    chat_id = first.json()["chat_id"]
    service.wait(chat_id)
    again = real.post("/incidents/import", json={"number": f"  {number} "})
    assert again.status_code == 201 and again.json() == {"number": number, "chat_id": chat_id, "already": True}
    view = real.get(f"/chats/{chat_id}").json()
    assert view["chat"]["origin"] == "headless" and view["chat"]["incident"] == number and view["chat"]["auto_approve"] is False
    assert view["pending_gate"] is None and view["runs"][0]["status"] in ("completed", "incomplete")
    decided = [e["data"] for e in view["events"] if e["type"] == "gate_decision"]
    assert decided and all(d["by"]["who"] != "human" for d in decided)        # the first run's checks ran without asking
    [listed] = real.get("/incidents").json()
    assert listed["number"] == number and listed["chat_id"] == chat_id and listed["resolution"] is None
    minutes = dict(service.settings.cadence)[listed["priority_code"]]
    assert (when(listed["next_update_due"]) - when(listed["updated"])).total_seconds() == minutes * 60
    opened = {c["number"]: c for c in real.get("/incidents/open").json()["incidents"]}
    assert opened[number]["imported"] is True and opened[number]["chat_id"] == chat_id


def test_the_ticket_shows_notes_newest_first_in_utc_with_local_time_beside(real):
    service = real.app.state.service
    number = real.get("/incidents/open").json()["incidents"][0]["number"]
    ticket = real.get("/incident/ticket", params={"number": number}).json()
    zone = ZoneInfo(ticket["zone"])
    assert ticket["incident"]["number"] == number and ticket["fields"]["state"] and ticket["notes"]
    stamps = [when(n["utc"]) for n in ticket["notes"]]
    assert stamps == sorted(stamps, reverse=True) and max(stamps) < service.now
    for note in [*ticket["notes"], ticket["opened"]]:
        assert when(note["utc"]).astimezone(zone).strftime("%H:%M:%S") == note["local"]
    assert all(n["author"] and n["text"] for n in ticket["notes"])
    assert real.get("/incident/ticket", params={"number": "INC0000001"}).status_code == 404


def test_a_headless_chats_follow_up_is_the_persons_with_the_chats_own_switch(client):
    """5.3: the first run of a headless chat runs its reads without asking; a follow-up stops at its gates for the
    person, and the chat's switch (off here) decides its reads."""
    service = client.app.state.service
    chat_id = client.post("/chats", json={"origin": "headless", "trigger": fx.TRIGGER}).json()["id"]
    service.wait(chat_id)
    view = client.get(f"/chats/{chat_id}").json()
    run_id = view["runs"][0]["run_id"]
    assert view["chat"]["auto_approve"] is False and view["runs"][0]["status"] == "completed"
    runner = service.runners[run_id]
    assert (runner.attended, runner.stop_at_gate, runner.via) == (False, False, "headless")
    assert client.post(f"/chats/{chat_id}/notes", json={"text": "What would change your mind?"}).json()["delivery"] == "follow-up"
    service.wait(chat_id)
    assert (runner.attended, runner.stop_at_gate, runner.via) == (True, True, "web") and service.sinks[run_id].attended
    switched = [e["data"] for e in client.get(f"/chats/{chat_id}").json()["events"] if e["type"] == "auto_approve"]
    assert switched[-1]["on"] is False


def test_a_new_chat_can_be_linked_to_an_incident_and_an_unknown_one_is_refused(real):
    number = real.get("/incidents/open").json()["incidents"][0]["number"]
    linked = real.post("/chats", json={"origin": "chat", "trigger": "What changed?", "incident": number})
    assert linked.status_code == 201 and linked.json()["incident"] == number
    assert real.post("/chats", json={"origin": "chat", "trigger": "What changed?", "incident": "INC0000001"}).status_code == 422
    real.app.state.service.wait(linked.json()["id"])
