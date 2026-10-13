"""Offline tests: the scripted model, no network, no key."""

from __future__ import annotations

import json
import re
import time
import tomllib
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from spike.config import ROOT, load_config
from spike.events import read_events
from spike.models import ScriptedChatModel, _call
from spike.graph import SYSTEM_PROMPT, tool_definitions
from spike.runner import Runner, headless
from spike.tools import LOGS, TOOLS, GetRecordArgs, RunContext, get_record

Q = "The nightly report-export job failed at 02:10 UTC. What happened?"
NOW = datetime.fromisoformat("2026-03-02T02:20:00+00:00")
BUNDLE_STRINGS = ["paycore", "hikari", "pay-db", "3.8.0", "larkspur"]


@pytest.fixture
def cfg(tmp_path: Path):
    return load_config(runs_dir=tmp_path)


def _state(r: Runner) -> dict:
    return r.graph.get_state(r.config).values


def test_auto_approve_run_completes_and_writes_a_run_record(cfg):
    r = Runner(cfg, "auto", decider=headless("approve"))
    res = r.start(Q, NOW)
    r.close()
    assert res["status"] == "completed"
    for f in ["run.json", "events.jsonl", "calls.jsonl", "report.json", "usage.json", "summary.json",
              "checkpoints.sqlite"]:
        assert (r.dir / f).exists(), f
    ev = read_events(r.dir / "events.jsonl")
    assert ev[0].type == "run_started" and ev[-1].type == "run_finished"
    assert [e.seq for e in ev] == list(range(1, len(ev) + 1))
    types = {e.type for e in ev}
    assert {"thinking_delta", "tool_call_proposed", "gate_decision", "tool_result", "hypothesis_update",
            "report", "model_call"} <= types


def test_every_tool_call_gets_exactly_one_result(cfg):
    r = Runner(cfg, "ids", decider=headless("approve"))
    r.start(Q, NOW)
    msgs = _state(r)["messages"]
    r.close()
    asked = [c["id"] for m in msgs if isinstance(m, AIMessage) for c in m.tool_calls]
    answered = [m.tool_call_id for m in msgs if isinstance(m, ToolMessage)]
    assert sorted(asked) == sorted(answered)


def test_pause_at_gate_then_resume_in_a_new_runner_with_rejection(cfg):
    r1 = Runner(cfg, "gate", stop_at_gate=True)
    assert r1.start(Q, NOW)["status"] == "waiting_for_gate"
    r1.close()
    # a new Runner is a new process as far as LangGraph is concerned: new connection, new graph, new model
    reason = "rollback already attempted and failed"
    r2 = Runner(cfg, "gate", decider=lambda calls: {c["call_id"]: {"approve": False, "reason": reason} for c in calls})
    res = r2.resume()
    st = _state(r2)
    r2.close()
    assert res["status"] == "completed"
    rej = [m for m in st["messages"] if isinstance(m, ToolMessage) and m.name == "propose_rollback"]
    assert rej and rej[0].status == "error" and reason in rej[0].content
    assert any(g["by"] == "human" and g["decision"] == "rejected" for g in st["gate_log"])
    assert not any("Roll back" in a for a in st["report"]["actions"])  # the agent re-planned
    usage = json.loads((r2.dir / "usage.json").read_text())
    assert len(usage["calls"]) == 5  # model calls from both segments are in one record


def test_headless_default_defers_actions_and_code_reports_their_state(cfg):
    r = Runner(cfg, "defer")  # default decider: headless defer
    res = r.start(Q, NOW)
    st = _state(r)
    r.close()
    assert res["status"] == "completed"
    msg = next(m for m in st["messages"] if isinstance(m, ToolMessage) and m.name == "propose_rollback")
    assert msg.content.startswith("DEFERRED") and msg.status == "success"
    [p] = st["report"]["proposed_actions"]
    assert p["state"] == "deferred" and p["by"] == "headless"


def test_board_history_records_round_call_and_reason(cfg):
    r = Runner(cfg, "board", decider=headless("approve"))
    r.start(Q, NOW)
    board = _state(r)["hypotheses"]
    r.close()
    row = board["h-disk"]["history"][0]
    assert row["round"] == 2 and row["call_id"].endswith("update_hypotheses")
    assert row["from"] is None and row["to"]["status"] == "leading"


def test_report_cites_specific_rows_inline_and_in_support(cfg):
    r = Runner(cfg, "cite", decider=headless("approve"))
    r.start(Q, NOW)
    rep = _state(r)["report"]
    r.close()
    assert "jk:report-export#56" in rep["summary"] and "conf:RB-DISK-SPACE" in " ".join(rep["actions"])
    assert len(rep["hypotheses"][0]["support"]) == 5


def test_interactive_mode_gates_reads_too(tmp_path):
    cfg = load_config(runs_dir=tmp_path, interactive=True)
    seen: list[str] = []

    def decide(calls):
        seen.extend(c["tool"] for c in calls)
        return {c["call_id"]: {"approve": True} for c in calls}

    r = Runner(cfg, "inter", decider=decide)
    assert r.start(Q, NOW)["status"] == "completed"
    r.close()
    assert "search_logs" in seen and "propose_rollback" in seen
    assert "update_hypotheses" not in seen  # internal tools never gate (open question for Rhys)


def test_validator_rejects_unknown_handle_once_then_accepts(cfg):
    r = Runner(cfg, "bad", decider=headless("approve"), model_overrides={"inject_bad_handle": True})
    res = r.start(Q, NOW)
    st = _state(r)
    r.close()
    assert res["status"] == "completed"
    assert st["report_attempts"] == 1
    assert st["validation"] == {"ok": True, "dropped": [], "attempt": 2}
    assert "log:worker-01/deadbeef" not in json.dumps(st["report"])


def test_budget_exhaustion_rejects_calls_and_still_reports(cfg):
    small = replace(cfg, tool_call_budget=3)
    r = Runner(small, "budget", decider=headless("approve"))
    res = r.start(Q, NOW)
    st = _state(r)
    r.close()
    assert res["status"] == "completed"
    assert st["tool_calls_used"] <= 3  # checked per call, so a parallel batch cannot overshoot
    assert any(g["by"] == "policy" and "budget" in (g["reason"] or "") for g in st["gate_log"])


def test_now_is_injected_never_a_parameter_and_hides_the_future(cfg):
    for d in tool_definitions():
        assert "now" not in d["function"]["parameters"]["properties"]
    r = Runner(cfg, "future", decider=headless("approve"))
    r.start(Q, NOW)
    st = _state(r)
    r.close()
    text = "\n".join(str(m.content) for m in st["messages"] if isinstance(m, ToolMessage))
    assert "purged by on-call" not in text  # logged at 02:31, after now
    future = next(x for x in LOGS if "purged by on-call" in x.raw)
    ctx = RunContext("t", NOW, True, True, 10)
    assert "not in this bundle" in get_record(GetRecordArgs(handle=future.handle), ctx).text
    unknown = get_record(GetRecordArgs(handle="log:worker-01/00000000"), ctx).text
    assert get_record(GetRecordArgs(handle=future.handle), ctx).text.split(":", 2)[-1] == unknown.split(":", 2)[-1]


def test_prompt_and_tool_text_contain_no_bundle_strings():
    text = (SYSTEM_PROMPT + json.dumps(tool_definitions())).lower()
    for s in BUNDLE_STRINGS:
        assert s not in text, s


def test_no_model_identifiers_outside_config():
    pat = re.compile(r"claude-|gpt-\d|gemini-", re.I)
    for p in [*(ROOT / "spike").glob("*.py"), ROOT / "README.md"]:
        if p.exists():
            assert not pat.search(p.read_text()), p


def test_anthropic_payload_keeps_thinking_and_batches_tool_results(monkeypatch):
    """Offline: build the request langchain-anthropic would send, without sending it."""
    from langchain_anthropic import ChatAnthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-not-used")
    prof = tomllib.loads((ROOT / "config.toml").read_text())["profiles"]["anthropic"]
    m = ChatAnthropic(model=prof["model"], max_tokens=prof["max_tokens"], effort=prof["effort"],
                      thinking=prof["thinking"])
    bound = m.bind_tools(tool_definitions(), cache_control={"type": "ephemeral"})
    ai = AIMessage(content=[{"type": "thinking", "thinking": "plan", "signature": "SIG"},
                            {"type": "tool_use", "id": "t1", "name": "search_logs", "input": {"terms": ["a"]}},
                            {"type": "tool_use", "id": "t2", "name": "get_record", "input": {"handle": "conf:X"}}],
                   tool_calls=[{"id": "t1", "name": "search_logs", "args": {"terms": ["a"]}},
                               {"id": "t2", "name": "get_record", "args": {"handle": "conf:X"}}],
                   response_metadata={"model_provider": "anthropic"})
    msgs = [SystemMessage("s"), HumanMessage("q"), ai, ToolMessage("rows", tool_call_id="t1", name="search_logs"),
            ToolMessage("REJECTED by human: no", tool_call_id="t2", name="get_record", status="error")]
    p = bound._get_request_payload(msgs, **bound.kwargs)
    assert p["messages"][1]["content"][0] == {"type": "thinking", "thinking": "plan", "signature": "SIG"}
    assert [m["role"] for m in p["messages"]] == ["user", "assistant", "user"]
    results = p["messages"][2]["content"]
    assert [r["is_error"] for r in results] == [False, True]
    assert p["cache_control"] == {"type": "ephemeral"}
    assert p["thinking"]["display"] == "summarized"
    assert "rationale" in p["tools"][0]["input_schema"]["required"]


def test_tool_kinds_cover_the_gate_policy():
    assert {t.kind for t in TOOLS.values()} == {"read", "action", "internal"}


class _Runaway(ScriptedChatModel):
    """Only ever writes to the board: internal calls are free, so only the model-turn cap stops it."""

    def _turn(self, msgs):
        k = sum(isinstance(m, AIMessage) for m in msgs)
        return "again", [_call("update_hypotheses", k, updates=[])]


class _BatchedSubmit(ScriptedChatModel):
    """Submits in the same turn as another call first, then alone."""

    def _turn(self, msgs):
        k = sum(isinstance(m, AIMessage) for m in msgs)
        report = _call("submit_report", 10 + k, summary="s", hypotheses=[], actions=[])
        if k == 0:
            return "both", [_call("search_logs", 1, "r", terms=["ERROR"]), report]
        return "alone", [report]


def _with_model(r: Runner, model) -> Runner:
    from spike.graph import build_graph
    from langgraph.checkpoint.sqlite import SqliteSaver
    r.graph = build_graph(model, {}, SqliteSaver(r.conn))
    return r


def test_runaway_internal_calls_are_bounded_and_still_write_a_record(cfg):
    r = _with_model(Runner(cfg, "runaway", decider=headless("approve")), _Runaway())
    res = r.start(Q, NOW)
    r.close()
    assert res["status"] == "incomplete"
    usage = json.loads((r.dir / "usage.json").read_text())
    assert len(usage["calls"]) == cfg.tool_call_budget + 6
    assert "model-turn limit" in json.loads((r.dir / "report.json").read_text())["summary"]


def test_submit_report_batched_with_other_calls_is_refused(cfg):
    r = _with_model(Runner(cfg, "batched", decider=headless("approve")), _BatchedSubmit())
    res = r.start(Q, NOW)
    st = _state(r)
    r.close()
    assert res["status"] == "completed"
    first = next(g for g in st["gate_log"] if g["tool"] == "submit_report")
    assert first["decision"] == "rejected" and "only call" in first["reason"]


def test_usage_is_recorded_without_streaming(cfg):
    r = Runner(cfg, "nostream", decider=headless("approve"), stream=False)
    r.start(Q, NOW)
    r.close()
    assert len(json.loads((r.dir / "usage.json").read_text())["calls"]) == 5


def test_gate_wait_spans_processes_and_payload_cannot_claim_policy(cfg):
    r1 = Runner(cfg, "wait", stop_at_gate=True)
    r1.start(Q, NOW)
    r1.close()
    assert read_events(r1.dir / "events.jsonl")[-1].type == "run_paused"
    time.sleep(0.3)
    r2 = Runner(cfg, "wait", decider=lambda calls: {c["call_id"]: {"approve": True, "by": "policy"} for c in calls})
    r2.resume()
    st = _state(r2)
    r2.close()
    [w] = st["gate_waits"]
    assert w["wait_ms"] >= 300
    assert any(g["tool"] == "propose_rollback" and g["by"] == "human" for g in st["gate_log"])
    assert [e.type for e in read_events(r2.dir / "events.jsonl")].count("run_finished") == 1


def test_message_history_is_append_only(cfg):
    """Each checkpoint's messages are a prefix of the next one's: required for preserved thinking and caching."""
    r = Runner(cfg, "prefix", decider=headless("approve"))
    r.start(Q, NOW)
    snaps = list(r.graph.get_state_history(r.config))[::-1]
    r.close()
    seqs = [[(m.id, str(m.content)) for m in s.values.get("messages", [])] for s in snaps]
    for a, b in zip(seqs, seqs[1:]):
        assert b[: len(a)] == a
