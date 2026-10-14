"""The API's endpoints (docs/decisions/ui-build.md section 6.4). Every body is a pydantic model with length
limits; every error is a problem response."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from smalls.api import models
from smalls.api.db import loads
from smalls.api.problems import Problem
from smalls.api.service import Service

router = APIRouter()


def service(request: Request) -> Service:
    return request.app.state.service


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/capabilities")
def capabilities(request: Request) -> dict[str, Any]:
    return service(request).capabilities()


# -- chats -------------------------------------------------------------------------------------------------------

def tallies(s: Service, chat_id: str) -> dict[str, int]:
    """What the chats list and the tally count: reads the agent proposed, ran, the commander rejected or skipped."""
    row = s.db.row("""
        SELECT
          sum(type = 'tool_call_proposed' AND json_extract(data, '$.kind') = 'read' AND json_extract(data, '$.by') = 'agent') AS proposed,
          sum(type = 'tool_result' AND json_extract(data, '$.kind') = 'read' AND json_extract(data, '$.by') = 'agent'
              AND json_extract(data, '$.status') = 'ok') AS run,
          sum(type = 'gate_decision' AND json_extract(data, '$.decision') = 'rejected' AND json_extract(data, '$.by.who') = 'human') AS rejected,
          sum(type = 'gate_decision' AND json_extract(data, '$.decision') = 'deferred' AND json_extract(data, '$.by.who') = 'human') AS skipped
        FROM event WHERE chat_id = ?""", (chat_id,))
    return {k: int(v or 0) for k, v in (row or {}).items()}


def summary(s: Service, chat: dict[str, Any]) -> dict[str, Any]:
    gate = s.pending_gate(chat["id"])
    return {**chat, "status": s.status(chat["id"]), "pending": len(gate["cards"]) if gate else 0, "tally": tallies(s, chat["id"])}


@router.get("/chats")
def list_chats(request: Request) -> list[dict[str, Any]]:
    s = service(request)
    return [summary(s, {**c, "auto_approve": bool(c["auto_approve"])}) for c in s.db.rows("SELECT * FROM chat ORDER BY created DESC")]


@router.post("/chats", status_code=201)
def create_chat(body: models.NewChat, request: Request) -> dict[str, Any]:
    s = service(request)
    chat = s.create_chat(body.origin, trigger=body.trigger, auto_approve=body.auto_approve, title=body.title)
    return summary(s, chat)


@router.get("/chats/{chat_id}")
def get_chat(chat_id: str, request: Request, after: int = 0) -> dict[str, Any]:
    s = service(request)
    chat = s.chat(chat_id)
    events = [{**e, "data": loads(e["data"])} for e in
              s.db.rows("SELECT seq, run_id, agent_seq, type, data, t_wall FROM event WHERE chat_id = ? AND seq > ? ORDER BY seq", (chat_id, after))]
    return {"chat": summary(s, chat), "runs": s.runs(chat_id), "events": events, "pending_gate": s.pending_gate(chat_id),
            "notes": s.db.rows("SELECT handle, text, submitted_at, delivered_at, run_id FROM note WHERE chat_id = ? ORDER BY id", (chat_id,))}


KEEP_ALIVE_S = 15.0
PAGE = 500
STREAM_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no", "Connection": "keep-alive"}


def sse(row: dict[str, Any]) -> str:
    """One stored event as the agent's protocol sends it (`Event.to_sse`), numbered per chat."""
    body = {"seq": row["seq"], "run_id": row["run_id"], "agent_seq": row["agent_seq"], "type": row["type"],
            "data": json.loads(row["data"]), "t_wall": row["t_wall"]}
    return f"id: {row['seq']}\nevent: {row['type']}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"


@router.get("/chats/{chat_id}/events")
async def events(chat_id: str, request: Request, after: int = 0) -> StreamingResponse:
    """The chat's events (6.3): every stored one after `Last-Event-ID` (or `after`), then each as it is written,
    with a comment line every 15 s so proxies keep the connection open."""
    s = service(request)
    s.chat(chat_id)
    given = request.headers.get("last-event-id", "")
    start = int(given) if given.strip().isdigit() else max(after, 0)
    query = "SELECT seq, run_id, agent_seq, type, data, t_wall FROM event WHERE chat_id = ? AND seq > ? ORDER BY seq LIMIT ?"

    async def stream() -> AsyncIterator[str]:
        cursor, wake = start, asyncio.Event()
        s.listeners.setdefault(chat_id, set()).add(wake)
        try:
            yield "retry: 2000\n\n"
            while True:
                wake.clear()        # cleared before the read, so an event written after it is never missed
                rows = await asyncio.to_thread(s.db.rows, query, (chat_id, cursor, PAGE))
                for row in rows:
                    yield sse(row)
                    cursor = row["seq"]
                if rows:
                    continue
                try:
                    await asyncio.wait_for(wake.wait(), timeout=KEEP_ALIVE_S)
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            s.listeners.get(chat_id, set()).discard(wake)

    return StreamingResponse(stream(), media_type="text/event-stream", headers=STREAM_HEADERS)


@router.post("/chats/{chat_id}/gate", status_code=202)
def answer_gate(chat_id: str, body: models.GateAnswer, request: Request) -> dict[str, Any]:
    return service(request).answer_gate(chat_id, body.interrupt_id, {k: v.for_runner() for k, v in body.answers.items()}, body.note)


@router.post("/chats/{chat_id}/notes", status_code=202)
def add_note(chat_id: str, body: models.NoteIn, request: Request) -> dict[str, Any]:
    return service(request).add_note(chat_id, body.text.strip())


@router.post("/chats/{chat_id}/stop", status_code=202)
def stop(chat_id: str, request: Request) -> dict[str, Any]:
    return service(request).stop(chat_id)


@router.put("/chats/{chat_id}/auto-approve")
def auto_approve(chat_id: str, body: models.Switch, request: Request) -> dict[str, Any]:
    return service(request).set_auto_approve(chat_id, body.on)


@router.post("/chats/{chat_id}/runs", status_code=202)
def more_runs(chat_id: str, body: models.MoreRun, request: Request) -> dict[str, Any]:
    return service(request).another_run(chat_id, resume=body.resume)


def not_found(what: str) -> Problem:
    return Problem(404, "Not found", what)
