"""v4 plan 5.2: the overview written at import. Code writes it at once from served data; a writer then replaces
its prose when the answer validates; contacts are code's, built only from the ticket, the on-call schedule, the
CMDB and the user records; nothing is invented when a field is missing; a writer's handle or markdown is dropped.
Values are read from the data at test time."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from smalls.api import overview as ov
from smalls.api.app import create_app
from smalls.api.settings import DEV_SCRIPT


@pytest.fixture
def make(settings, monkeypatch):
    monkeypatch.delenv("SMALLS_TAGS", raising=False)
    clients = []

    def build(**kw):
        c = TestClient(create_app(replace(settings, registry=None, script=DEV_SCRIPT, clock=None, **kw)))
        c.__enter__()
        clients.append(c)
        return c

    yield build
    for c in clients:
        c.__exit__(None, None, None)


def imported(client) -> tuple[str, dict]:
    number = client.get("/incidents/open").json()["incidents"][0]["number"]
    chat_id = client.post("/incidents/import", json={"number": number}).json()["chat_id"]
    client.app.state.service.wait(chat_id)
    return number, client.get("/incident/overview", params={"number": number}).json()


def test_code_writes_the_overview_from_served_data_and_leaves_the_rest_empty(make):
    client = make()
    number, body = imported(client)
    ticket = client.get("/incident/ticket", params={"number": number}).json()
    assert body["written_by"] == "code" and body["whats_going_on"].startswith(ticket["incident"]["short_description"])
    names = [c["name"] for c in body["affected_cis"]]
    assert ticket["fields"]["cmdb_ci"] in names and ticket["fields"]["business_service"] in names
    assert all(c["status"] == "" and c["severity"] == "unknown" for c in body["affected_cis"])
    assert body["working_theories"] == [] and body["timeline"] == [] and body["glossary"] == []
    assert client.get("/incident/overview", params={"number": "INC0000001"}).status_code == 404


def test_contacts_come_only_from_the_ticket_the_schedule_the_cmdb_and_the_users(make):
    client = make()
    number, body = imported(client)
    ticket = client.get("/incident/ticket", params={"number": number}).json()
    inputs = client.app.state.service.reader().overview_inputs(number)
    known = {ticket["fields"]["assigned_to"], inputs["support_group"]} | {o["name"] for o in inputs["oncall"]} | {p["name"] for p in inputs["people"]}
    assert body["contacts"][0]["name"] == ticket["fields"]["assigned_to"] and body["contacts"][0]["role"].startswith("Major Incident Manager")
    assert {c["name"] for c in body["contacts"]} <= known and len({c["name"] for c in body["contacts"]}) == len(body["contacts"])
    assert all(c["phone"] is None for c in body["contacts"] if c["name"] not in {p["name"] for p in inputs["people"] if p["phone"]})


def test_a_writer_replaces_the_prose_and_keeps_code_s_contacts(make):
    client = make(overview="scripted")
    number, body = imported(client)
    assert body["written_by"] == "scripted" and "(scripted)" in body["whats_going_on"] and body["timeline"]
    code = ov.view(client.app.state.service.reader().overview_inputs(number), ov.Written(), "code")
    assert body["contacts"] == code["contacts"]


def test_a_handle_or_markdown_in_what_the_writer_says_is_dropped():
    w = ov.clean(ov.Written(whats_going_on="See log:host-1/0123abcd for it.", business_impact="**half** of orders",
                            glossary=[ov.Term(term="X", meaning="A plain meaning.")]))
    assert w.whats_going_on == "" and w.business_impact == "" and w.glossary[0].meaning == "A plain meaning."


def test_a_writer_that_fails_leaves_code_s_overview_and_says_why(make):
    def broken(messages, tools):
        return "", [], {"text": "not json"}

    client = make(overview="scripted", overview_script=broken)
    _, body = imported(client)
    assert body["written_by"] == "code" and "failed" in body["why_code"]


def test_code_s_lines_skip_a_pasted_query_result():
    assert ov.prose("Rolled the service back at 14:42 and nothing changed.")
    for pasted in ["pg_stat_activity on db-1, grouped: x=# select a, b from t;", "usename | state | count", "$ kubectl get pods", "{\"a\": 1}"]:
        assert not ov.prose(pasted)
