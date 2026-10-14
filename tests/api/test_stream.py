"""Row R3: the event stream. Served by a real server on a free port, so events arrive as they are written: a
client that disconnects mid-run and reconnects with Last-Event-ID receives every seq exactly once, in order."""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import replace

import httpx
import pytest
import uvicorn

import fixture_registry as fx
from smalls.api.app import create_app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(settings):
    app = create_app(settings)
    port = free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.02)
    yield app, f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(timeout=10)


def read(url: str, *, until, headers=None, after: int | None = None) -> list[dict]:
    """Read the stream until `until(event)` is true, then hang up."""
    got: list[dict] = []
    params = {"after": after} if after is not None else None
    with httpx.Client(timeout=30) as c, c.stream("GET", url, headers=headers or {}, params=params) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["cache-control"] == "no-cache, no-transform" and r.headers["x-accel-buffering"] == "no"
        block: dict[str, str] = {}
        for line in r.iter_lines():
            if line.startswith(("id:", "event:", "data:")):
                key, _, value = line.partition(":")
                block[key] = value.strip()
            elif line == "" and "data" in block:
                event = json.loads(block["data"])
                assert int(block["id"]) == event["seq"] and block["event"] == event["type"]
                got.append(event)
                block = {}
                if until(event):
                    break
    return got


def test_a_client_that_reconnects_with_last_event_id_gets_every_event_once_in_order(server):
    app, base = server
    service = app.state.service
    service.cfg = replace(service.cfg, investigator=service.cfg.investigator.with_params(delay_s=0.003))     # long enough to cut mid-run
    chat_id = httpx.post(f"{base}/chats", json={"origin": "headless", "trigger": fx.TRIGGER}).json()["id"]
    url = f"{base}/chats/{chat_id}/events"
    first = read(url, until=lambda e: e["seq"] >= 15)
    arrived_at = time.monotonic()
    second = read(url, headers={"Last-Event-ID": str(first[-1]["seq"])}, until=lambda e: e["type"] == "run_finished")
    seqs = [e["seq"] for e in first + second]
    assert seqs == list(range(1, len(seqs) + 1)) and first[-1]["seq"] == 15
    assert second[-1]["type"] == "run_finished" and time.monotonic() - arrived_at > 0.05       # the rest arrived as it ran
    stored = [e["seq"] for e in httpx.get(f"{base}/chats/{chat_id}").json()["events"]]
    assert stored == seqs
    again = read(url, after=len(seqs) - 3, until=lambda e: e["type"] == "run_finished")
    assert [e["seq"] for e in again] == seqs[-3:]                                                 # `after` starts where the first paint ended


def test_an_unknown_chat_has_no_stream(server):
    _, base = server
    r = httpx.get(f"{base}/chats/nope/events")
    assert r.status_code == 404 and r.headers["content-type"] == "application/problem+json"
