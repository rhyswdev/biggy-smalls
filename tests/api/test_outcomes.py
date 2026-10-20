"""v4 plan 5.1 and 6.8: the per-check outcomes and the scripted "concluded" run. A clause is one plain clause of
at most 14 words with no handle and no markdown, or it is dropped; the summariser runs once a run settles,
summarises each agent check once, and sends one `outcomes` event; without a summariser nothing is sent and the
web app writes the clauses; the concluded dev script reaches a leading hypothesis and recommends an action."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import fixture_registry as fx
from smalls.api import outcomes
from smalls.api.app import create_app
from smalls.api.settings import DEV_SCRIPT, SCRIPTS, Settings


@pytest.mark.parametrize("clause, ok", [
    ("found the job's exit code", True),
    ("ruled out the disk filling up.", True),
    ("found nothing in the last two hours of logs", True),
    ("one two three four five six seven eight nine ten eleven twelve thirteen fourteen", True),
    ("one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen", False),
    ("saw the error. Then it stopped", False),
    ("found **three** errors", False),
    ("found it in log:host-1/0123abcd", False),
    ("found it\nand more", False),
    ("", False),
    (None, False),
])
def test_a_clause_is_one_short_plain_clause(clause, ok):
    assert outcomes.valid(clause) is ok


@pytest.mark.parametrize("summary, ok", [
    ("Smalls found the job failing on every retry and ruled out the disk filling up.", True),
    ("Smalls found the error in the job logs. It ruled out a network fault.", True),
    (" ".join(["word"] * 40), True),
    (" ".join(["word"] * 41), False),
    ("Smalls found a. Then b. Then c.", False),
    ("Smalls found **three** errors.", False),
    ("Smalls found it in log:host-1/0123abcd.", False),
    ("Smalls found it\nand more", False),
    ("", False),
    (None, False),
    (["Smalls found x"], False),
])
def test_a_summary_is_at_most_two_short_plain_sentences(summary, ok):
    assert outcomes.valid_summary(summary) is ok


def scripted(answer):
    """A scripted summariser that answers `answer(ids)` for the ids it is given."""
    def script(messages, tools):
        from langchain_core.messages import HumanMessage
        import re
        text = str(next(m.content for m in reversed(messages) if isinstance(m, HumanMessage)))
        ids = [c["id"] for c in json.loads(re.search(r"```json\n(.*?)\n```", text, re.S).group(1))]
        return "", [], {"text": json.dumps(answer(ids))}
    return script


def start(client, **body) -> str:
    chat_id = client.post("/chats", json={"origin": "headless", "trigger": fx.TRIGGER, **body}).json()["id"]
    client.app.state.service.wait(chat_id)
    return chat_id


def test_without_a_summariser_no_outcomes_are_sent(client):
    assert client.get("/capabilities").json()["outcomes"] == "code"
    chat_id = start(client)
    assert [e for e in client.get(f"/chats/{chat_id}").json()["events"] if e["type"] == "outcomes"] == []


@pytest.fixture
def summarised(settings):
    with TestClient(create_app(replace(settings, outcomes="scripted"))) as c:
        yield c


def test_the_summariser_writes_one_clause_per_agent_check_once(summarised):
    service = summarised.app.state.service
    chat_id = start(summarised)
    events = summarised.get(f"/chats/{chat_id}").json()["events"]
    reads = [e["data"]["call_id"] for e in events if e["type"] == "tool_call_proposed" and e["data"]["kind"] == "read" and e["data"]["by"] == "agent"]
    [sent] = [e for e in events if e["type"] == "outcomes"]
    assert sent["data"]["by"] == "scripted" and sorted(sent["data"]["clauses"]) == sorted(reads)
    assert all(outcomes.valid(c) for c in sent["data"]["clauses"].values())
    assert outcomes.valid_summary(sent["data"]["summary"]) and sent["data"]["summary"].startswith("Smalls")
    stored = service.db.rows("SELECT call_id FROM check_outcome WHERE chat_id = ?", (chat_id,))
    assert sorted(r["call_id"] for r in stored) == sorted(reads)
    summarised.post(f"/chats/{chat_id}/notes", json={"text": "Anything else?"})          # a follow-up that runs no new check
    service.wait(chat_id)
    assert len([e for e in summarised.get(f"/chats/{chat_id}").json()["events"] if e["type"] == "outcomes"]) == 1


def test_the_checks_given_carry_what_ran_and_the_board_after(summarised):
    chat_id = start(summarised)
    items = outcomes.checks(summarised.app.state.service.events(chat_id), set())
    assert items and all(i["result"] is not None and i["tool"] for i in items)
    assert any(i["board_after"] for i in items)
    assert json.dumps(items)                  # what the model is given is plain JSON


def test_a_clause_that_fails_is_dropped_and_none_valid_sends_nothing(settings):
    def bad(messages, tools):
        from langchain_core.messages import HumanMessage
        import re
        text = str(next(m.content for m in reversed(messages) if isinstance(m, HumanMessage)))
        ids = [c["id"] for c in json.loads(re.search(r"```json\n(.*?)\n```", text, re.S).group(1))]
        return "", [], {"text": json.dumps({i: "found **bold** things" for i in ids})}

    with TestClient(create_app(replace(settings, outcomes="scripted", outcomes_script=bad))) as c:
        chat_id = start(c)
        assert [e for e in c.get(f"/chats/{chat_id}").json()["events"] if e["type"] == "outcomes"] == []


def test_the_old_flat_answer_is_still_accepted_with_no_summary(settings):
    script = scripted(lambda ids: {i: "found the job's exit code" for i in ids})
    with TestClient(create_app(replace(settings, outcomes="scripted", outcomes_script=script))) as c:
        chat_id = start(c)
        [sent] = [e for e in c.get(f"/chats/{chat_id}").json()["events"] if e["type"] == "outcomes"]
        assert sent["data"]["clauses"] and set(sent["data"]["clauses"].values()) == {"found the job's exit code"}
        assert "summary" not in sent["data"]


def test_an_invalid_summary_is_dropped_and_the_clauses_still_sent(settings):
    script = scripted(lambda ids: {"summary": "Smalls found **it** in log:host-1/0123abcd.", "checks": {i: "found nothing new" for i in ids}})
    with TestClient(create_app(replace(settings, outcomes="scripted", outcomes_script=script))) as c:
        chat_id = start(c)
        [sent] = [e for e in c.get(f"/chats/{chat_id}").json()["events"] if e["type"] == "outcomes"]
        assert sent["data"]["clauses"] and "summary" not in sent["data"]


def test_the_concluded_dev_script_reaches_a_probable_cause(settings, monkeypatch):
    monkeypatch.delenv("SMALLS_TAGS", raising=False)
    monkeypatch.setenv("SMALLS_API_SCRIPT", "concluded")
    monkeypatch.setenv("SMALLS_BUNDLE", str(settings.bundle))
    monkeypatch.setenv("SMALLS_CHECKPOINT", settings.checkpoint)
    monkeypatch.setenv("SMALLS_API_MODE", "scripted")
    assert Settings.from_env().script == SCRIPTS["concluded"]
    real = replace(settings, registry=None, clock=None, script=SCRIPTS["concluded"])
    with TestClient(create_app(real)) as c:
        chat_id = c.post("/chats", json={"origin": "headless"}).json()["id"]
        c.app.state.service.wait(chat_id)
        report = next(e["data"]["report"] for e in reversed(c.get(f"/chats/{chat_id}").json()["events"]) if e["type"] == "report")
    [lead] = [h for h in report["hypotheses"] if h["status"] == "leading"]
    assert lead["confidence"] == 0.75 and report["actions"] and report["actions"][0]["rank"] == 1 and report["do_not"]
    assert DEV_SCRIPT != SCRIPTS["concluded"]
