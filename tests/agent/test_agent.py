"""The agent over the fixture registry, offline: the scripted model, no network, no key (build row A2).
These are the spike's tests, ported with their intent, plus what the port added: the envelope split, the
`by` shape of a gate decision, citable notes, the run-record files and the context guard."""

from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import fixture_registry as fx
import fixture_script
from smalls.agent import __main__ as cli
from smalls.agent import actions, gate, internal_tools
from smalls.agent.config import ROOT, load_config
from smalls.agent.events import read_events
from smalls.agent.models import make_model, provider_tools
from smalls.agent.models import scripted_call as call
from smalls.agent.record import CHECKPOINTS, FILES
from smalls.agent.runner import Runner, module_toolbox, resume_hint
from smalls.contracts.report import Report
from smalls.tools import registry
from smalls.tools.registry import ToolContext, ToolSpec
from smalls_data import prompt_lint


@pytest.fixture
def cfg(tmp_path: Path):
    return load_config(runs_dir=tmp_path)


def runner(cfg, run_id: str, **kwargs) -> Runner:
    kwargs.setdefault("script", fixture_script.turn)
    return Runner(cfg, module_toolbox("fixture_registry"), run_id, **kwargs)


def approve(cards):
    return {c["call_id"]: {"approve": True} for c in cards}


def definitions() -> list[dict]:
    return [registry.definition(spec) for spec in internal_tools.toolset(fx.registry()).values()]


def test_an_auto_approved_run_completes_and_writes_a_complete_run_record(cfg):
    r = runner(cfg, "auto", decider=approve)
    result = r.start(fx.TRIGGER, fx.NOW, checkpoint="t2")
    r.close()
    assert result["status"] == "completed"
    assert r.record.missing() == [] and (r.dir / CHECKPOINTS).exists()
    assert sorted(p.name for p in r.dir.iterdir()) == sorted([*FILES, CHECKPOINTS])
    events = read_events(r.dir / "events.jsonl")
    assert events[0].type == "run_started" and events[-1].type == "run_finished"
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert {"thinking_delta", "tool_call_proposed", "gate_decision", "tool_result", "hypothesis_update", "board_status", "draft_report",
            "report", "model_call", "budget", "validation"} <= {e.type for e in events}
    Report.model_validate(json.loads((r.dir / "report.json").read_text()))
    run = json.loads((r.dir / "run.json").read_text())
    assert (run["arm"], run["checkpoint"], run["now"], run["profile"], run["registry"]) == ("C", "t2", "2026-03-02T02:20:00Z", "scripted", "fixture_registry")
    assert run["prompt_sha"] and run["tools_sha"] and run["commit"] and run["versions"]["langgraph"].startswith("1.2.")


def test_every_tool_call_gets_exactly_one_result(cfg):
    r = runner(cfg, "ids", decider=approve)
    r.start(fx.TRIGGER, fx.NOW)
    messages = r.state()["messages"]
    r.close()
    asked = [c["id"] for m in messages if isinstance(m, AIMessage) for c in m.tool_calls]
    answered = [m.tool_call_id for m in messages if isinstance(m, ToolMessage)]
    assert sorted(asked) == sorted(answered) and len(asked) == 12


def test_pause_at_a_gate_then_resume_in_a_new_runner_with_a_rejection(cfg):
    r1 = runner(cfg, "gate", stop_at_gate=True, attended=True)
    assert r1.start(fx.TRIGGER, fx.NOW)["status"] == "paused"
    assert r1.record.missing() == []                                   # a paused run already has its derived files
    r1.close()
    # A new Runner is a new process as far as the framework is concerned: new connection, new graph, new model.
    reason = "rollback already attempted and failed"
    r2 = runner(cfg, "gate", attended=True, decider=lambda cards: {c["call_id"]: {"approve": False, "reason": reason} for c in cards})
    result = r2.resume()
    state = r2.state()
    r2.close()
    assert result["status"] == "completed"
    [answer] = [m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "propose_action"]
    assert answer.status == "error" and answer.content.startswith(f"REJECTED by human: {reason}.")
    [note] = state["ic_notes"]
    assert re.fullmatch(r"ic:note/[0-9a-f]{8}", note["handle"]) and note["text"] == reason and note["handle"] in answer.content
    assert json.loads((r2.dir / "notes.jsonl").read_text().splitlines()[0])["handle"] == note["handle"]
    assert any(g["by"] == {"who": "human", "via": "cli"} and g["decision"] == "rejected" for g in state["gate_log"])
    assert not any("Roll back" in a["action"] for a in state["report"]["actions"])     # the agent re-planned
    [proposal] = state["report"]["proposed_actions"]                                    # written by code from the ledger
    assert (proposal["decision"], proposal["by"], proposal["reason"]) == ("rejected", {"who": "human", "via": "cli"}, note["handle"])
    assert proposal["rationale"] == {"tests": "h-release", "expect": "tonight's run cleans up after itself"}
    assert len(json.loads((r2.dir / "usage.json").read_text())["calls"]) == 5        # model calls from both segments


def test_an_eval_run_approves_and_records_actions_as_policy(cfg):
    r = runner(cfg, "eval", eval_run=True)
    result = r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    assert result["status"] == "completed"
    answer = next(m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "propose_action")
    assert answer.content.startswith(actions.RECORDED) and answer.status == "success"
    [proposal] = state["report"]["proposed_actions"]
    assert (proposal["decision"], proposal["by"]) == ("approved", {"who": "policy", "via": "headless"})


def test_any_other_unattended_run_defers_actions(cfg):
    r = runner(cfg, "defer")
    result = r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    assert result["status"] == "completed" and cfg.headless == "defer"
    answer = next(m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "propose_action")
    assert answer.content.startswith("DEFERRED") and answer.status == "success"
    [proposal] = state["report"]["proposed_actions"]
    assert (proposal["decision"], proposal["by"]) == ("deferred", {"who": "policy", "via": "headless"})
    assert next(e for e in state["ledger"] if e["tool"] == "propose_action")["status"] == "deferred"


def test_a_headless_reject_reaches_the_model_as_a_policy_rejection(cfg):
    r = runner(cfg, "reject", decider=gate.headless("reject"))
    result = r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    assert result["status"] == "completed"
    answer = next(m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "propose_action")
    assert answer.content.startswith("REJECTED by policy:") and answer.status == "error"
    assert state["ic_notes"] == []                                     # only a person's reason is a note
    assert state["report"]["proposed_actions"][0]["by"] == {"who": "policy", "via": "headless"}


def test_headless_approval_is_refused_when_an_action_tool_is_not_a_mock(cfg):
    tools = internal_tools.toolset(fx.registry())
    gate.guard_headless_approval("approve", tools)
    real = {**tools, "propose_action": replace(tools["propose_action"], mock=False)}
    with pytest.raises(SystemExit, match="not mocks"):
        gate.guard_headless_approval("approve", real)
    gate.guard_headless_approval("defer", real)


def test_board_history_records_round_call_and_reason(cfg):
    r = runner(cfg, "board", decider=approve)
    r.start(fx.TRIGGER, fx.NOW)
    board = r.state()["hypotheses"]
    r.close()
    first = board["h-full"]["history"][0]
    assert first["round"] == 2 and first["call_id"].endswith("update_board")
    assert first["from"] is None and first["to"]["status"] == "leading" and first["because"]
    assert [row["to"]["status"] for row in board["h-dying"]["history"]] == ["open", "rejected"]
    assert board["h-dying"]["source"] == "stated" and board["h-release"]["status"] == "leading" and board["h-full"]["role"] == "symptom"
    saved = json.loads((r.dir / "board.json").read_text())
    assert [h["id"] for h in saved["hypotheses"]] == ["h-full", "h-dying", "h-release"]


def test_the_report_cites_specific_rows_inline_and_in_support(cfg):
    r = runner(cfg, "cite", decider=approve)
    r.start(fx.TRIGGER, fx.NOW)
    report = r.state()["report"]
    r.close()
    assert "jk:report-export#56" in report["summary"] and "conf:RB-DISK-SPACE" in " ".join(a["action"] for a in report["actions"])
    leading = next(h for h in report["hypotheses"] if h["status"] == "leading")
    assert leading["id"] == "h-release" and len(leading["support"]) == 5 and leading["source"] == "derived"
    assert leading["history"] == [{"as_of": "2026-03-02T02:20:00Z", "status": "leading", "confidence": 0.65, "because": leading["history"][0]["because"]}]
    assert report["validation"] == {"ok": True, "attempts": 1, "dropped": [], "flagged": [], "continuity": None, "verifier": None, "disputed": []}


def test_interactive_mode_gates_reads_too(tmp_path):
    cfg = load_config(runs_dir=tmp_path, interactive=True)
    seen: list[str] = []

    def decide(cards):
        seen.extend(c["tool"] for c in cards)
        return approve(cards)

    r = runner(cfg, "inter", decider=decide, attended=True)
    assert r.start(fx.TRIGGER, fx.NOW)["status"] == "completed"
    r.close()
    assert "search_logs" in seen and "propose_action" in seen
    assert "update_board" not in seen and "submit_report" not in seen      # internal tools never gate


def test_the_validator_refuses_an_unknown_handle_once_then_accepts(cfg):
    r = runner(cfg, "bad", decider=approve, script=fixture_script.turn_bad_handle)
    result = r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    assert result["status"] == "completed" and state["report_attempts"] == 1
    assert state["validation"] == {"ok": True, "attempts": 2, "dropped": [], "flagged": [], "continuity": None, "verifier": None, "disputed": []}
    assert "log:worker-01/deadbeef" not in json.dumps(state["report"])
    refusal = next(m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "submit_report")
    assert refusal.status == "error" and "log:worker-01/deadbeef" in refusal.content and refusal.content.startswith("Report not accepted.")


def test_a_handle_that_stays_bad_is_dropped_and_recorded(cfg):
    def stubborn(messages, tools):
        thinking, calls = fixture_script.turn(messages, tools)
        for c in calls:
            if c["name"] == "submit_report":
                c["args"]["hypotheses"][0]["support"].append("log:worker-01/deadbeef")
                c["args"]["do_not"][0]["evidence"] = ["log:worker-01/deadbeef"]
        return thinking, calls

    r = runner(cfg, "stubborn", decider=approve, script=stubborn)
    result = r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    assert result["status"] == "completed"
    assert state["validation"] == {"ok": False, "attempts": 2, "dropped": ["log:worker-01/deadbeef"], "flagged": ["d:1"], "continuity": None, "verifier": None, "disputed": []}
    claims = {k: v for k, v in state["report"].items() if k != "validation"}      # the validation block is where it is recorded
    assert "log:worker-01/deadbeef" not in json.dumps(claims) and state["report"]["do_not"][0]["evidence"] == []
    assert state["report"]["validation"] == state["validation"]
    assert "log:worker-01/deadbeef" not in json.dumps(state["hypotheses"])


def test_a_handle_that_was_only_listed_cannot_be_cited_until_it_is_shown(cfg):
    """`get_record` on the failed build lists its commit as a child. The commit is referenced, not shown."""
    def cites_child(messages, tools):
        k = sum(isinstance(m, AIMessage) for m in messages)
        if k == 0:
            return "read the build", [call("get_record", 1, ("h", "the build"), handle="jk:report-export#57")]
        if k == 1:
            return "cite its commit", [call("update_board", 2, hypotheses=[
                {"id": "h", "statement": "s", "role": "cause", "status": "open", "subject": "x", "confidence": 0.3, "source": "derived",
                 "support": [fx.COMMIT]}])]
        if k == 2:
            return "fetch it", [call("get_record", 3, ("h", "the commit"), handle=fx.COMMIT)]
        if k == 3:
            return "cite it now", [call("update_board", 4, hypotheses=[
                {"id": "h", "statement": "s", "role": "cause", "status": "open", "subject": "x", "confidence": 0.3, "source": "derived",
                 "support": [fx.COMMIT]}])]
        return "stop", []

    r = runner(cfg, "child", decider=approve, script=cites_child)
    r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    first, second = [m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "update_board"]
    assert first.status == "error" and "only listed, never shown" in first.content and fx.COMMIT in first.content
    assert second.status == "success" and state["hypotheses"]["h"]["support"] == [fx.COMMIT]
    build = next(e for e in state["ledger"] if e["call_id"].endswith("01_get_record"))
    assert build["shown"] == ["jk:report-export#57"] and build["referenced"] == [fx.COMMIT]


def test_budget_exhaustion_rejects_calls_and_the_run_still_reports(cfg):
    r = runner(replace(cfg, call_budget=3), "budget", decider=approve)
    result = r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    assert result["status"] == "completed"
    assert state["calls_used"] == 3                                    # checked per call, so a parallel batch cannot overshoot
    assert any(g["by"]["who"] == "policy" and "budget" in (g["reason"] or "") and "submit now" in g["reason"] for g in state["gate_log"])
    assert "budget 3/3" in next(m.content for m in reversed(state["messages"]) if isinstance(m, ToolMessage) and "[board:" in m.content)


def test_now_is_injected_never_a_parameter_and_hides_the_future(cfg):
    for d in definitions():
        assert "now" not in d["parameters"]["properties"]
    r = runner(cfg, "future", decider=approve)
    r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    text = "\n".join(str(m.content) for m in state["messages"] if isinstance(m, ToolMessage))
    assert "purged by on-call" not in text                             # logged at 02:31, after now
    future = next(i for i in fx.LOGS if "purged by on-call" in i.text)
    ctx = ToolContext(store=None, now=fx.NOW, tags=False)
    spec = fx.registry()["get_record"]
    hidden, unknown = registry.call(spec, {"handle": future.handle}, ctx), registry.call(spec, {"handle": "log:worker-01/00000000"}, ctx)
    assert hidden.status == unknown.status == "error" and "not in this bundle" in hidden.text
    assert hidden.text.replace(future.handle, "H") == unknown.text.replace("log:worker-01/00000000", "H")


def test_no_string_under_the_agent_or_the_tools_holds_a_bundle_string():
    texts = prompt_lint.prompt_texts()
    names = {where for where, _ in texts}
    assert {"smalls/agent/prompts/investigator.md", "smalls/agent/prompts/first_turn.md", "smalls/agent/prompts/verifier_claims.md",
            "smalls/agent/prompts/verifier_contrarian.md", "smalls/tools/descriptions.yaml", "smalls/agent/graph.py",
            "smalls/tools/errors.py", "smalls/agent/config.toml", "smalls/tools/ingest/tagging/profiles.toml"} <= names
    assert prompt_lint.find(texts) == []
    assert prompt_lint.find([("x", prompt_lint.string_literals('"""About Larkspur."""\nX = "ok"  # Larkspur in a comment is not a string\n'))]) != []
    assert prompt_lint.find([("x", prompt_lint.string_literals('X = "ok"  # Larkspur in a comment is not a string\n'))]) == []


def test_no_model_name_outside_the_config_file():
    pattern = re.compile(r"claude-(opus|sonnet|haiku|fable|\d)|gpt-\d|gemini-\d|\bo\d-(mini|preview)\b", re.I)
    files = [p for folder in ("smalls", "tests", "docs", "smalls_data") for p in (ROOT / folder).rglob("*") if p.is_file()]
    files += [p for p in (ROOT / "eval").glob("*.py")] + [ROOT / name for name in ("Makefile", "compose.yaml", "Dockerfile", "CLAUDE.md", "pyproject.toml")]
    allowed = ROOT / "smalls/agent/config.toml"
    offenders = []
    for path in files:
        if path == allowed or "__pycache__" in path.parts or path.suffix in (".pyc", ".sqlite", ".db"):
            continue
        try:
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(path.relative_to(ROOT).as_posix())
        except UnicodeDecodeError:
            continue
    assert offenders == []
    assert pattern.search(allowed.read_text())


def test_the_provider_request_keeps_reasoning_batches_results_and_sends_no_sampling_parameter(monkeypatch, tmp_path):
    """Offline: build the request the provider's client library would send, without sending it."""
    monkeypatch.setenv("SMALLS_LIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-not-used")
    cfg = load_config(runs_dir=tmp_path, profile="anthropic")
    model, bind = make_model(cfg.investigator, timeout_s=cfg.timeout_s)
    bound = model.bind_tools(provider_tools(definitions(), "anthropic"), **bind)
    ai = AIMessage(content=[{"type": "thinking", "thinking": "plan", "signature": "SIG"},
                            {"type": "tool_use", "id": "t1", "name": "search_logs", "input": {"since": "-1h"}},
                            {"type": "tool_use", "id": "t2", "name": "get_record", "input": {"handle": "conf:X"}}],
                   tool_calls=[{"id": "t1", "name": "search_logs", "args": {"since": "-1h"}},
                               {"id": "t2", "name": "get_record", "args": {"handle": "conf:X"}}],
                   response_metadata={"model_provider": "anthropic"})
    messages = [SystemMessage("s"), HumanMessage("q"), ai, ToolMessage("rows", tool_call_id="t1", name="search_logs"),
                ToolMessage("REJECTED by human: no", tool_call_id="t2", name="get_record", status="error")]
    p = bound._get_request_payload(messages, **bound.kwargs)
    assert p["messages"][1]["content"][0] == {"type": "thinking", "thinking": "plan", "signature": "SIG"}
    assert [m["role"] for m in p["messages"]] == ["user", "assistant", "user"]        # both results in one user message
    assert [block["is_error"] for block in p["messages"][2]["content"]] == [False, True]
    assert p["cache_control"] == {"type": "ephemeral"}                                # caching is on by default
    assert p["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert p["output_config"]["effort"] == cfg.investigator.params["effort"]
    assert not {"temperature", "top_p", "top_k"} & set(p)
    assert "tool_choice" not in p or p["tool_choice"] == {"type": "auto"}             # never forced
    assert model.default_request_timeout == cfg.timeout_s and p["max_tokens"] == cfg.investigator.params["max_tokens"]
    by_name = {t["name"]: t for t in p["tools"]}
    assert len(by_name) == 8 and "rationale" in by_name["search_logs"]["input_schema"]["required"]


def test_under_pytest_the_profile_is_scripted_and_a_live_model_needs_the_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("SMALLS_PROFILE", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key-that-happens-to-be-set")
    monkeypatch.delenv("SMALLS_LIVE", raising=False)
    cfg = load_config(runs_dir=tmp_path)
    assert cfg.investigator.provider == "scripted" and load_config(runs_dir=tmp_path, profile="anthropic").investigator.provider == "scripted"
    with pytest.raises(RuntimeError, match="SMALLS_LIVE"):
        make_model(cfg.profiles["anthropic"])
    monkeypatch.setenv("SMALLS_LIVE", "1")
    assert load_config(runs_dir=tmp_path).investigator.provider == "anthropic"


def test_tool_kinds_cover_the_gate_policy():
    assert {t.kind for t in internal_tools.toolset(fx.registry()).values()} == {"read", "action", "internal"}


def test_runaway_internal_calls_are_bounded_and_still_write_a_record(cfg):
    def runaway(messages, tools):       # only ever writes to the board: internal calls are free, so only the turn cap stops it
        return "again", [call("update_board", sum(isinstance(m, AIMessage) for m in messages), hypotheses=[])]

    r = runner(cfg, "runaway", decider=approve, script=runaway)
    result = r.start(fx.TRIGGER, fx.NOW)
    r.close()
    assert result["status"] == "incomplete" and r.record.missing() == []
    assert len(json.loads((r.dir / "usage.json").read_text())["calls"]) == cfg.call_budget + cfg.extra_turns
    report = json.loads((r.dir / "report.json").read_text())
    assert "model-turn limit" in report["summary"] and report["impact"] is None and report["stakeholder_update"] is None
    Report.model_validate(report)


def test_submit_report_batched_with_other_calls_is_refused(cfg):
    def batched(messages, tools):
        thinking, calls = fixture_script.turn([m for m in messages] + [AIMessage("")] * 4, tools)   # jump to the report turn
        if sum(isinstance(m, AIMessage) for m in messages) == 0:
            return "both", [call("search_logs", 1, ("h", "errors"), terms=["ERROR"], since="-1h"), calls[0]]
        return "alone", calls

    r = runner(cfg, "batched", decider=approve, script=batched)
    r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    first = next(g for g in state["gate_log"] if g["tool"] == "submit_report")
    assert first["decision"] == "rejected" and first["by"]["who"] == "policy" and "only call" in first["reason"]


def test_usage_comes_from_state_and_every_call_is_priced(cfg):
    r = runner(cfg, "usage", decider=approve)
    r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    usage = json.loads((r.dir / "usage.json").read_text())
    assert len(usage["calls"]) == len(state["usage"]) == 5 and all("cost_usd" in c and c["attempt"] == 1 for c in usage["calls"])
    assert usage["totals"]["input_tokens"] == sum(c["usage"]["input_tokens"] for c in state["usage"]) > 0
    summary = json.loads((r.dir / "summary.json").read_text())
    assert (summary["status"], summary["model_calls"], summary["tool_calls"], summary["retries"]) == ("completed", 5, 9, 0)


def test_the_gate_wait_spans_processes_and_a_payload_cannot_claim_to_be_policy(cfg):
    r1 = runner(cfg, "wait", stop_at_gate=True, attended=True)
    r1.start(fx.TRIGGER, fx.NOW)
    r1.close()
    paused = read_events(r1.dir / "events.jsonl")[-1]
    assert paused.type == "run_paused" and paused.data["resume"] == resume_hint("wait")
    time.sleep(0.3)
    r2 = runner(cfg, "wait", attended=True, decider=lambda cards: {c["call_id"]: {"approve": True, "by": "policy", "who": "policy"} for c in cards})
    r2.resume()
    state = r2.state()
    r2.close()
    [wait] = state["gate_waits"]
    assert wait["wait_ms"] >= 300
    assert next(g for g in state["gate_log"] if g["tool"] == "propose_action")["by"] == {"who": "human", "via": "cli"}
    events = read_events(r2.dir / "events.jsonl")
    assert [e.type for e in events].count("run_finished") == 1 and [e.seq for e in events] == list(range(1, len(events) + 1))


def test_the_resume_hint_is_the_command_for_the_way_the_run_was_started(monkeypatch):
    monkeypatch.delenv("SMALLS_RUNNER", raising=False)
    assert resume_hint("r1") == "uv run smalls resume r1"
    monkeypatch.setenv("SMALLS_RUNNER", "compose")
    assert resume_hint("r1") == "docker compose run --rm smalls smalls resume r1"


def test_message_history_is_append_only(cfg):
    """Each checkpoint's messages are a prefix of the next one's: required for preserved reasoning and caching."""
    r = runner(cfg, "prefix", decider=approve)
    r.start(fx.TRIGGER, fx.NOW)
    snapshots = list(r.graph.get_state_history(r.config))[::-1]
    r.close()
    histories = [[(m.id, str(m.content)) for m in s.values.get("messages", [])] for s in snapshots]
    assert len(histories) > 10
    for earlier, later in zip(histories, histories[1:]):
        assert later[: len(earlier)] == earlier


def test_evidence_is_written_at_call_time_for_every_row_a_tool_showed(cfg):
    r = runner(cfg, "evidence", decider=approve)
    r.start(fx.TRIGGER, fx.NOW)
    r.close()
    evidence = [json.loads(line) for line in (r.dir / "evidence.jsonl").read_text().splitlines()]
    calls = [json.loads(line) for line in (r.dir / "calls.jsonl").read_text().splitlines()]
    assert all(set(row) == {"call_id", "handle", "t_known", "sha256", "text"} and len(row["sha256"]) == 64 for row in evidence)
    shown = {(c["call_id"], h) for c in calls for h in c["shown"]}
    assert shown and shown == {(row["call_id"], row["handle"]) for row in evidence}
    assert all(row["text"].startswith(row["handle"]) for row in evidence)
    reads = [c for c in calls if c["kind"] == "read"]
    assert {c["by"] for c in calls} == {"agent", "harness"} and all(c["gate"]["by"]["who"] == "auto" for c in reads)
    assert set(calls[1]) == {"call_id", "round", "by", "tool", "kind", "args", "rationale", "status", "shown", "referenced", "native_query",
                             "window", "chars", "latency_ms", "repeat_of", "used_tag_input", "gate"}
    assert calls[0]["tool"] == "inventory" and calls[0]["referenced"] and calls[0]["shown"] == []


def test_the_context_guard_stops_a_run_that_nears_the_input_limit(tmp_path):
    cfg = load_config(runs_dir=tmp_path)
    small = replace(cfg, investigator=cfg.investigator.with_params(max_input_tokens=1500, max_tokens=100))
    r = runner(small, "context", decider=approve)
    result = r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    assert result["status"] == "incomplete" and state["stop_reason"] == "the context limit was reached"
    assert "context limit" in state["report"]["summary"] and r.record.missing() == []
    assert 1 <= len(state["usage"]) < 5


def test_a_run_record_names_the_key_variables_that_were_set_and_never_their_values(cfg, monkeypatch):
    monkeypatch.setenv("FIXTURE_API_KEY", "sk-fixture-secret-value-123")
    r = runner(cfg, "keys", decider=approve)
    r.start(fx.TRIGGER, fx.NOW)
    r.close()
    assert "FIXTURE_API_KEY" in json.loads((r.dir / "run.json").read_text())["key_variables"]
    for path in r.dir.iterdir():
        assert b"sk-fixture-secret-value-123" not in path.read_bytes(), path.name


def test_a_replay_renders_a_run_record_with_no_key_and_no_model(cfg, capsys, monkeypatch):
    r = runner(cfg, "replay", decider=approve)
    r.start(fx.TRIGGER, fx.NOW)
    r.close()
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("smalls.agent.models.make_model", lambda *a, **k: pytest.fail("a replay built a model"))
    assert cli.main(["replay", str(r.dir)]) == 0
    out = capsys.readouterr().out
    assert "run replay" in out and "report as of 2026-03-02T02:20:00Z" in out and "h-release" in out and "completed" in out


def test_the_cli_runs_pauses_and_resumes_a_scripted_run(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SMALLS_RUNS_DIR", str(tmp_path))
    common = ["--registry", "fixture_registry", "--script", "fixture_script:turn", "--profile", "scripted"]
    assert cli.main(["run", "--now", "2026-03-02T02:20:00Z", "--trigger", fx.TRIGGER, "--run-id", "cli", "--stop-at-gate", *common]) == 0
    assert "paused at a gate" in capsys.readouterr().out
    assert cli.main(["resume", "cli", "--reject", "not tonight"]) == 0
    report = json.loads((tmp_path / "cli" / "report.json").read_text())
    assert report["proposed_actions"][0]["decision"] == "rejected" and report["proposed_actions"][0]["by"]["who"] == "human"
    with pytest.raises(SystemExit, match="not waiting at a gate"):
        cli.main(["resume", "cli", "--approve"])
    with pytest.raises(SystemExit, match="not a run id"):      # a run id is never used as a path
        cli.main(["resume", "../etc"])
