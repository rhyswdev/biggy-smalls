"""`make api-fixtures`: the event logs the web app's reducer tests fold, recorded from scripted runs through the API
over the agent track's fixture registry (a made-up incident, so the UI lint passes over them). Each file is a chat's
events as `GET /chats/{id}` returns them. Run again whenever the event protocol changes.

    uv run python tests/api/make_fixtures.py [<folder>]      (default web/tests/fixtures)
"""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent / "agent"), str(HERE.parent)]

import fixture_registry as fx  # noqa: E402
import parity  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from smalls.api.app import create_app  # noqa: E402
from smalls.api.settings import Settings  # noqa: E402

OUT = HERE.parent.parent / "web" / "tests" / "fixtures"


def settings(runs: Path) -> Settings:
    return Settings(bundle=parity.BUNDLE, checkpoint="1452", mode="scripted", runs_dir=runs, registry="fixture_registry",
                    script="fixture_script:turn", clock=fx.NOW.isoformat())


def answer_all(client: TestClient, chat_id: str, decide) -> None:
    """Answer every gate the run stops at, card by card, until it finishes."""
    service = client.app.state.service
    for _ in range(20):
        service.wait(chat_id)
        gate = client.get(f"/chats/{chat_id}").json()["pending_gate"]
        if not gate:
            return
        answers = {c["call_id"]: decide(n, c) for n, c in enumerate(gate["cards"])}
        assert client.post(f"/chats/{chat_id}/gate", json={"interrupt_id": gate["interrupt_id"], "answers": answers}).status_code == 202


def events(client: TestClient, chat_id: str) -> list[dict]:
    client.app.state.service.wait(chat_id)
    return client.get(f"/chats/{chat_id}").json()["events"]


def main(out: Path = OUT) -> None:
    import tempfile

    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        runs = Path(tmp)
        files: dict[str, list[dict]] = {}
        with TestClient(create_app(settings(runs))) as client:
            new = lambda **body: client.post("/chats", json={"trigger": fx.TRIGGER, **body}).json()["id"]  # noqa: E731
            approved = new(origin="chat", auto_approve=True)
            answer_all(client, approved, lambda n, c: {"approve": True})
            files["chat-approved"] = events(client, approved)

            def mixed(n: int, card: dict) -> dict:
                if card["kind"] == "action":
                    return {"approve": False, "reason": "Not before the owning team has looked."}
                return [{"approve": True}, {"defer": True}, {"approve": False, "reason": "Already known; not needed"}][n % 3]

            asking = new(origin="chat", auto_approve=False)
            answer_all(client, asking, mixed)
            files["chat-asking"] = events(client, asking)
            headless = new(origin="headless")
            files["headless"] = events(client, headless)
            followup = new(origin="headless")
            client.app.state.service.wait(followup)
            client.post(f"/chats/{followup}/notes", json={"text": "Why not the disk itself?"})
            files["followup"] = events(client, followup)
            run_id = client.get(f"/chats/{approved}").json()["runs"][0]["run_id"]
            assert client.post(f"/runs/{run_id}/export").status_code == 201
        # A follow-up the agent answers in text, played by the agent track's follow-up test script.
        with TestClient(create_app(replace(settings(Path(tmp) / "answered"), script="test_followup:answers_in_text"))) as other:
            answered = other.post("/chats", json={"trigger": fx.TRIGGER, "origin": "headless"}).json()["id"]
            other.app.state.service.wait(answered)
            other.post(f"/chats/{answered}/notes", json={"text": "Why not the disk itself?"})
            files["followup-answered"] = events(other, answered)
        with TestClient(create_app(replace(settings(runs), load_scripted=True))) as again:
            past = next(c for c in again.get("/chats").json() if c["origin"] == "past" and c["id"].endswith(run_id))
            files["past-of-chat-approved"] = again.get(f"/chats/{past['id']}").json()["events"]
    for name, rows in files.items():
        text = scrub(json.dumps(rows, indent=1, ensure_ascii=False))
        json.loads(text)
        (out / f"{name}.json").write_text(text + "\n", encoding="utf-8")
        print(f"{name}: {len(rows)} events")


def scrub(text: str) -> str:
    """The made-up incident happens to use a few words that are also names in the real bundle (a first name in
    its chat). The UI lint keeps every bundle string out of `web/`, so each such word is replaced, whole and in
    any case, by a neutral one. Handles are hashes and carry no name, so nothing the reducer reads changes shape."""
    import re

    from smalls_data import prompt_lint

    for s in prompt_lint.bundle_strings():
        text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(s) + r"(?![A-Za-z0-9])", "someone", text, flags=re.I)
    return text


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT)
