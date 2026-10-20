"""Row R4: the read endpoints over the real bundle, at both checkpoints. Every endpoint is called after an
unattended scripted run, and no time in any answer is at or after `now` but the ones that are `now` by
definition; no file under `_internal/` or `scenario/` is opened; an unknown handle is a 404 problem.

Wall-clock fields (when a chat was made, when a gate was asked or decided, when an event was written) are
the app's bookkeeping in real time, not incident data, and are left out of the scan by name."""

from __future__ import annotations

import builtins
import io
import json
from dataclasses import replace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import golden
import parity
import test_leak
import test_leak_tools
from smalls.api.app import create_app
from smalls.api.settings import DEV_SCRIPT

PLANNED_KEYS = {"planned_utc", "next_update_due"}      # a scheduled time, labelled as a plan
WALL = {"created", "started", "finished", "t_wall", "asked_at", "decided_at", "submitted_at", "delivered_at", "recorded", "started_at", "imported_at",
        "run_id"}       # a run id is minted from the wall clock
NOW_BY_DEFINITION = {"now", "time", "as_of"}     # the checkpoint itself, the trigger's own time, a report's as-of


def times(node, zone_of, path=()) -> list[tuple[int, tuple]]:
    """Every time in a JSON answer, with where it was found, skipping wall-clock fields."""
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            if k in WALL or k in NOW_BY_DEFINITION or k in PLANNED_KEYS:
                continue
            out += times(v, zone_of, (*path, k))
    elif isinstance(node, list):
        for n, v in enumerate(node):
            out += times(v, zone_of, (*path, n))
    elif isinstance(node, str):         # a scheduled time is printed as planned, as the tools' leak test allows
        out += [(t, path) for t in test_leak.times_in(test_leak_tools.PLANNED.sub("", node), zone_of(path))]
    return out


def drop_keys(node, keys: set[str]):
    if isinstance(node, dict):
        return {k: drop_keys(v, keys) for k, v in node.items() if k not in keys}
    if isinstance(node, list):
        return [drop_keys(v, keys) for v in node]
    return node


class Opened:
    """Every path opened while it is installed."""

    def __init__(self):
        self.paths: list[str] = []

    def __enter__(self):
        self.real = builtins.open

        def spy(file, *a, **k):
            self.paths.append(str(file))
            return self.real(file, *a, **k)

        builtins.open = io.open = spy
        return self

    def __exit__(self, *exc):
        builtins.open = io.open = self.real


@pytest.mark.parametrize("checkpoint", list(golden.CHECKPOINTS))
def test_every_read_endpoint_at_a_checkpoint(settings, checkpoint, monkeypatch):
    monkeypatch.delenv("SMALLS_TAGS", raising=False)
    real = replace(settings, registry=None, script=DEV_SCRIPT, clock=None, checkpoint=checkpoint)
    with Opened() as opened, TestClient(create_app(real)) as client:
        service = client.app.state.service
        now = int(service.now.timestamp() * 1000)
        chat_id = client.post("/chats", json={"origin": "headless"}).json()["id"]
        service.wait(chat_id)
        answers = {"/health": client.get("/health"), "/capabilities": client.get("/capabilities"), "/incident": client.get("/incident"),
                   "/chats": client.get("/chats"), "/tools/schemas": client.get("/tools/schemas"),
                   "/incidents/open": client.get("/incidents/open")}
        number = answers["/incident"].json()["incident"]["number"]
        answers["/incident/ticket"] = client.get("/incident/ticket", params={"number": number})
        imported = client.post("/incidents/import", json={"number": number})
        service.wait(imported.json()["chat_id"])
        answers["/incidents"] = client.get("/incidents")
        answers["/incident/overview"] = client.get("/incident/overview", params={"number": number})
        view = client.get(f"/chats/{chat_id}")
        answers["/chats/{id}"] = view
        events = view.json()["events"]
        calls = {e["data"]["call_id"] for e in events if e["type"] == "tool_result"}
        handles = {h for e in events if e["type"] == "tool_result" for h in [*e["data"]["shown"], *e["data"]["referenced"][:3]]}
        for call_id in sorted(calls):
            answers[f"call {call_id}"] = client.get(f"/chats/{chat_id}/calls/{call_id}")
        for handle in sorted(handles):
            answers[f"evidence {handle}"] = client.get(f"/chats/{chat_id}/evidence/{quote(handle, safe='')}")
        missing = client.get(f"/chats/{chat_id}/evidence/{quote('snow:INC9999999', safe='')}")
        store = service.reader().store
    assert missing.status_code == 404 and missing.headers["content-type"] == "application/problem+json"
    assert [p for p in opened.paths if "/_internal/" in p or "/scenario/" in p] == []
    assert len(calls) >= 5 and len(handles) >= 10
    for what, r in answers.items():
        assert r.status_code == 200, (what, r.text[:200])
        body = r.json()
        record = body.get("record") if isinstance(body, dict) else None
        scheduled = \
            test_leak.SCHEDULED.get(((record or {}).get("source"), (record or {}).get("dataset")), ())
        if record and scheduled and record["format"] == "json":     # a scheduled time in a native record is a plan, not an event
            record["native"] = json.dumps(drop_keys(json.loads(record["native"]), set(scheduled)))

        def zone_of(path):         # a record's native text is in its source's own clock
            if "record" in path and isinstance(body, dict) and (body.get("record") or {}).get("source") in store.integration.sources:
                return store.integration.zone(body["record"]["source"])
            return test_leak.UTC

        late = [(t, p) for t, p in times(body, zone_of) if t >= now]
        # now itself, only as the end of a tool's window: the window, and where a tool echoes it (its query, its
        # result's first line, the text of the rows it showed), which the tools' own leak test allows too
        echoes = {"window", "native_query", "first_line", "text"}
        allowed = [(t, p) for t, p in late if t == now and echoes & set(map(str, p))]
        assert late == allowed, (what, late[:3])
    card = answers["/incident"].json()
    assert card["incident"]["number"] and card["note"]["author"] and card["found_by"]
    assert "first" not in json.dumps(card["incident"]).lower()                    # no derived fact on the card
