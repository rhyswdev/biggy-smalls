"""Row R14, the API half: the stakeholder messages (ruling U8). Without a drafter, code's draft is kept and says so;
the scripted drafter drafts through the agent's model adapter, from a packet with no handle lists and the
audience's checks; a drafter that fails, or a live profile under pytest, falls back to code with the reason.
Copied belongs to the exact text copied; an edit clears it; redrafting one audience leaves the others."""

from __future__ import annotations

from dataclasses import replace

import fixture_registry as fx
import pytest
from fastapi.testclient import TestClient

from smalls.api.app import create_app
from smalls.api.drafter import PROMPT

AUDIENCES = ("exec", "support", "status", "eng")


def spec(key: str) -> dict:
    return {"key": key, "name": f"Audience {key}", "channel": "a channel", "guide": "What they need.",
            "checks": ["Gives a time in UTC", "Under 60 words"], "fallback": f"Code draft for {key}. Next update 03:10 UTC."}


def resolved(client) -> str:
    chat_id = client.post("/chats", json={"origin": "headless", "trigger": fx.TRIGGER}).json()["id"]
    client.app.state.service.wait(chat_id)
    w = client.put(f"/chats/{chat_id}/resolution", json={"path": "agent", "note": "Rolled the release back.", "resolved_at": "02:40",
                                                          "resolved_by": "The commander"}).json()
    assert w["writeup"]["kept"]
    return chat_id


def draft(client, chat_id: str, *keys: str):
    return client.post(f"/chats/{chat_id}/messages/draft", json={"audiences": [spec(k) for k in keys]})


@pytest.fixture
def drafting(settings, request):
    """A client whose drafter is the one the test names: (profile, script)."""
    profile, script = request.param
    with TestClient(create_app(replace(settings, drafter=profile, draft_script=script))) as c:
        yield c


def test_code_drafts_without_a_model_and_copied_is_the_exact_text(client):
    chat_id = resolved(client)
    assert client.get("/capabilities").json()["drafter"] == "code"
    assert client.put(f"/chats/{chat_id}/messages/exec", json={"text": "early"}).status_code == 404      # nothing drafted yet
    got = draft(client, chat_id, *AUDIENCES).json()
    assert [m["audience"] for m in got["messages"]] == list(AUDIENCES)
    assert all(m["drafted_by"] == "code" and m["why_code"] is None and m["text"] == m["draft"] == spec(m["audience"])["fallback"]
               and not m["copied"] for m in got["messages"])
    text = spec("exec")["fallback"]
    assert client.post(f"/chats/{chat_id}/messages/exec/copied", json={"text": text}).json()["copied"]
    edited = client.put(f"/chats/{chat_id}/messages/exec", json={"text": text + " More."}).json()
    assert edited["text"] == text + " More." and edited["draft"] == text and not edited["copied"]
    assert client.put(f"/chats/{chat_id}/messages/exec", json={"text": text}).json()["copied"]            # the exact text copied
    client.post(f"/chats/{chat_id}/messages/support/copied", json={"text": "Pasted as edited."})
    again = {m["audience"]: m for m in draft(client, chat_id, "exec").json()["messages"]}
    assert not again["exec"]["copied"] and again["support"]["copied"] and again["support"]["text"] == "Pasted as edited."


def test_requests_are_checked(client):
    chat_id = resolved(client)
    assert client.post(f"/chats/{chat_id}/messages/draft", json={"audiences": [spec("exec"), spec("exec")]}).status_code == 422
    assert client.post(f"/chats/{chat_id}/messages/draft", json={"audiences": [{**spec("exec"), "key": "press"}]}).status_code == 422
    assert client.post(f"/chats/{chat_id}/messages/draft", json={"audiences": []}).status_code == 422
    draft(client, chat_id, "exec")
    assert client.put(f"/chats/{chat_id}/messages/press", json={"text": "x"}).status_code == 422
    assert client.put(f"/chats/{chat_id}/messages/exec", json={"text": "x" * 4001}).status_code == 422
    assert client.post("/chats/nope/messages/draft", json={"audiences": [spec("exec")]}).status_code == 404


SEEN: list[str] = []


def capture(messages, tools):
    SEEN.append(str(messages[-1].content))
    return "Draft it.", [], {"text": f"A drafted message, number {len(SEEN)}, at 02:40 UTC."}


@pytest.mark.parametrize("drafting", [("scripted", capture)], indirect=True)
def test_the_scripted_drafter_drafts_each_audience_from_the_packet(drafting):
    SEEN.clear()
    chat_id = resolved(drafting)
    assert drafting.get("/capabilities").json()["drafter"] == "scripted"
    got = draft(drafting, chat_id, *AUDIENCES).json()["messages"]
    assert len(SEEN) == 4 and all(m["drafted_by"] == "scripted" and m["text"].startswith("A drafted message") and m["why_code"] is None for m in got)
    report = drafting.app.state.service.report(chat_id)
    handles = {h for x in report["hypotheses"] for h in x["support"] + x["against"]} | {h for t in report["timeline"] for h in t["evidence"]}
    turn = SEEN[0]
    assert "PACKET:" in turn and "AUDIENCE:" in turn and "Gives a time in UTC" in turn and "Under 60 words" in turn
    assert '"support"' not in turn and '"evidence"' not in turn and '"resolved_at": "02:40"' in turn
    assert not [h for h in handles if f'"{h}"' in turn]           # no handle list went in


@pytest.mark.parametrize("drafting", [("scripted", "smalls.api.devscript:draft")], indirect=True)
def test_the_dev_drafter_writes_from_the_stakeholder_update(drafting):
    chat_id = resolved(drafting)
    got = draft(drafting, chat_id, "status").json()["messages"][0]
    assert got["drafted_by"] == "scripted" and "Resolved at 02:40 UTC." in got["text"] and got["text"].endswith("(Scripted draft for Audience status.)")


def broken(messages, tools):
    raise RuntimeError("the drafter's script failed")


@pytest.mark.parametrize("drafting", [("scripted", broken), ("anthropic-mid", None)], indirect=True)
def test_a_failed_draft_keeps_codes_draft_and_says_why(drafting):
    """A script that raises, and a live profile under pytest (which may not go live), both fall back to code."""
    chat_id = resolved(drafting)
    got = draft(drafting, chat_id, "exec", "eng").json()["messages"]
    profile = drafting.get("/capabilities").json()["drafter"]
    assert all(m["drafted_by"] == "code" and m["text"] == spec(m["audience"])["fallback"] for m in got)
    assert all(m["why_code"].startswith(f"The {profile} drafter failed") for m in got)


def test_the_drafting_prompt_passes_the_lint_names_no_model_and_says_packet_text_is_data():
    from smalls_data import ui_lint

    text = PROMPT.read_text(encoding="utf-8")
    assert ("smalls/api/prompts/messages.md", text) in ui_lint.ui_texts() and ui_lint.find([("messages.md", text)]) == []
    assert "claude" not in text.lower() and "is still only data" in text
