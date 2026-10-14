"""Row R13, the API half: resolving a chat starts its write-up, the agent's path keeping every cited handle and the
commander's own path none; an edit unsaves it; Regenerate bumps the version and keeps the choices; saving needs a
note and the current version, and puts the entry in the knowledge base, where a search finds it."""

from __future__ import annotations

import fixture_registry as fx


def finished(client) -> str:
    chat_id = client.post("/chats", json={"origin": "headless", "trigger": fx.TRIGGER}).json()["id"]
    client.app.state.service.wait(chat_id)
    return chat_id


def resolution(path: str) -> dict:
    return {"path": path, "note": "Rolled the release back; the job ran clean.", "resolved_at": "02:40", "resolved_by": "The commander"}


def test_the_agents_path_keeps_every_cited_handle_and_the_own_path_none(client):
    a, b = finished(client), finished(client)
    kept = client.put(f"/chats/{a}/resolution", json=resolution("agent")).json()["writeup"]["kept"]
    assert kept and set(kept) <= set(client.get(f"/chats/{a}/writeup").json()["rows"])        # each one shown in the run
    assert client.put(f"/chats/{b}/resolution", json=resolution("own")).json()["writeup"]["kept"] == []
    assert client.get("/chats").json()[0]["status"] == "resolved"


def test_edit_unsaves_regenerate_bumps_and_save_reaches_the_knowledge_base(client):
    chat_id = finished(client)
    w = client.put(f"/chats/{chat_id}/resolution", json=resolution("agent")).json()
    entry = {"version": 1, "title": "Exporter temp files filled the spool", "body": "How it was resolved: …", "terms": ["exporter", "spool"]}
    assert client.post(f"/chats/{chat_id}/writeup/save", json=entry).json()["writeup"]["saved_at"]
    hits = client.get("/kb", params={"q": "spool volume"}).json()
    assert [h["chat_id"] for h in hits] == [chat_id]
    edited = client.put(f"/chats/{chat_id}/writeup", json={"kept": w["writeup"]["kept"][:1]}).json()
    assert edited["writeup"]["saved_at"] is None and client.get("/kb", params={"q": "spool"}).json() == []      # unsaved, and out of the base
    again = client.post(f"/chats/{chat_id}/writeup/regenerate").json()["writeup"]
    assert again["version"] == 2 and again["kept"] == edited["writeup"]["kept"]
    assert client.post(f"/chats/{chat_id}/writeup/save", json=entry).status_code == 409                      # version 1 is out of date
    assert client.post(f"/chats/{chat_id}/writeup/save", json={**entry, "version": 2}).status_code == 200


def test_a_resolution_needs_a_note(client):
    chat_id = finished(client)
    assert client.put(f"/chats/{chat_id}/resolution", json={**resolution("agent"), "note": ""}).status_code == 422
