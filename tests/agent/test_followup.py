"""Seam S3 (docs/decisions/ui-build.md section 8): a follow-up continues a finished run where it stopped, on the
same run id and thread, with the same conversation and board, and a few more gated calls. Neither the
orientation nor the opening sweep runs again. A turn with no tool call ends it, and its text is the answer;
a report submitted again is validated and adds history rows."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import fixture_registry as fx
import fixture_script
from smalls.agent.config import load_config
from smalls.agent.events import read_events
from smalls.agent.models import scripted_call as call
from smalls.agent.runner import Runner, module_toolbox
from smalls.contracts.report import Report

QUESTION = "Why not the disk itself?"


@pytest.fixture
def cfg(tmp_path: Path):
    return load_config(runs_dir=tmp_path)


def approve(cards):
    return {c["call_id"]: {"approve": True} for c in cards}


def in_followup(messages) -> bool:
    last_human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
    return last_human is not None and "follow-up from the incident commander" in str(last_human.content)


def turns_since_followup(messages) -> int:
    start = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage) and "follow-up from the incident commander" in str(m.content))
    return sum(isinstance(m, AIMessage) for m in messages[start:])


def answers_in_text(messages, tools):
    if in_followup(messages):
        return "The report already covers this.", [], {"text": "The disk is healthy: usage fell back once the temp files were purged."}
    return fixture_script.turn(messages, tools)


def reads_then_resubmits(messages, tools):
    if not in_followup(messages):
        return fixture_script.turn(messages, tools)
    k = turns_since_followup(messages)
    if k == 0:
        return "Check the disk's own lines again.", [call("search_logs", 40, ("h-dying", "disk errors, not just usage"), terms=["[disk]"], since="-3h")]
    if k == 1:
        disk = fixture_script.find(messages, "[disk]")
        return "Lower the disk theory further.", [call("update_board", 41, hypotheses=[{"id": "h-dying", "confidence": 0.05, "because": disk[:1]}])]
    turn = fixture_script.turn(messages, tools)       # the plain run's report, with the summary changed
    report = dict(turn[1][0])
    report["args"] = {**report["args"], "summary": report["args"]["summary"] + " The disk itself shows no fault."}
    report["id"] = "call_42_submit_report"
    return "Resubmit with the disk ruled out more firmly.", [report]


def finished(cfg, run_id: str) -> Runner:
    r = Runner(cfg, module_toolbox("fixture_registry"), run_id, script=fixture_script.turn, decider=approve)
    assert r.start(fx.TRIGGER, fx.NOW)["status"] == "completed"
    r.close()
    return r


def harness_calls(state) -> list[str]:
    return [e["call_id"] for e in state["ledger"] if e["by"] == "harness"]


def test_a_followup_answered_in_text_continues_the_same_run_and_the_report_stands(cfg):
    first = finished(cfg, "fu-text")
    before = json.loads((first.dir / "report.json").read_text())
    r = Runner(cfg, module_toolbox("fixture_registry"), "fu-text", script=answers_in_text, decider=approve)
    before_state = r.state()
    result = r.reopen(QUESTION)
    state = r.state()
    r.close()
    assert result["status"] == "completed" and r.run_id == "fu-text"
    assert harness_calls(state) == harness_calls(before_state)                              # no second sweep, no second inventory
    assert sum(isinstance(m, HumanMessage) and str(m.content).startswith("Opening sweep") for m in state["messages"]) == 1
    assert state["messages"][: len(before_state["messages"])] == before_state["messages"]   # the same conversation, extended
    after = json.loads((r.dir / "report.json").read_text())
    assert {k: v for k, v in after.items() if k != "proposed_actions"} == {k: v for k, v in before.items() if k != "proposed_actions"}
    [done] = state["followups"]
    assert (done["outcome"], done["n"], done["answer"]) == ("answered", 1, "The disk is healthy: usage fell back once the temp files were purged.")
    assert state["followup"] is None and state["ic_notes"][-1]["text"] == QUESTION and state["ic_notes"][-1]["handle"] == done["note"]
    events = read_events(r.dir / "events.jsonl")
    types = [e.type for e in events]
    assert [e.seq for e in events] == list(range(1, len(events) + 1))                       # one numbering across both segments
    tail = types[types.index("run_finished") + 1:]
    assert tail[:2] == ["ic_note", "phase"] and "answer" in tail and tail[-4:] == ["followup_finished", "report", "phase", "run_finished"]
    assert "run_started" not in tail and not any(e.type == "phase" and e.data["name"] in ("orient", "sweep") for e in events[types.index("run_finished"):])


def test_a_followup_that_reads_and_resubmits_is_validated_and_adds_history(cfg):
    finished(cfg, "fu-report")
    r = Runner(cfg, module_toolbox("fixture_registry"), "fu-report", script=reads_then_resubmits, decider=approve)
    before = r.state()
    result = r.reopen(QUESTION, calls=3)
    state = r.state()
    r.close()
    assert result["status"] == "completed"
    report = Report.model_validate(json.loads((r.dir / "report.json").read_text()))
    assert report.summary.endswith("The disk itself shows no fault.") and report.validation.ok
    assert state["calls_used"] == before["calls_used"] + 1
    assert len(state["hypotheses"]["h-dying"]["history"]) == len(before["hypotheses"]["h-dying"]["history"]) + 1
    [done] = state["followups"]
    assert (done["outcome"], done["calls_until"]) == ("report", before["calls_used"] + 3)
    assert harness_calls(state) == harness_calls(before)
    types = [e.type for e in read_events(r.dir / "events.jsonl")]
    assert "draft_report" in types[types.index("run_finished"):]


def test_a_followup_gets_only_the_calls_it_was_given(cfg):
    finished(cfg, "fu-budget")

    def keeps_reading(messages, tools):
        if not in_followup(messages):
            return fixture_script.turn(messages, tools)
        k = turns_since_followup(messages)
        return "Read more.", [call("search_logs", 50 + k, ("h-dying", "more"), terms=[f"term{k}"], since="-2h")]

    r = Runner(cfg, module_toolbox("fixture_registry"), "fu-budget", script=keeps_reading, decider=approve)
    used = r.state()["calls_used"]
    result = r.reopen(QUESTION, calls=2)
    state = r.state()
    r.close()
    assert result["status"] == "completed" and state["calls_used"] == used + 2                 # two ran; the rest were refused by policy
    [done] = state["followups"]
    assert done["outcome"] == "stopped" and "turn limit" in done["stop_reason"]
    assert any(g["by"]["who"] == "policy" and g["decision"] == "rejected" for g in state["gate_log"][-3:])


def test_a_run_that_has_not_finished_is_resumed_not_reopened(cfg):
    r = Runner(cfg, module_toolbox("fixture_registry"), "fu-paused", script=fixture_script.turn, stop_at_gate=True, attended=True)
    assert r.start(fx.TRIGGER, fx.NOW)["status"] == "paused"
    r.close()
    again = Runner(cfg, module_toolbox("fixture_registry"), "fu-paused", script=fixture_script.turn)
    with pytest.raises(SystemExit, match="resume it instead"):
        again.reopen(QUESTION)
    again.close()


def test_a_verified_runs_resubmitted_report_gets_the_changed_claims_recheck(cfg):
    from test_verifier import claims_script, contrarian_script

    scripts = {"claims": claims_script(), "contrarian": contrarian_script()}
    first = Runner(cfg, module_toolbox("fixture_registry"), "fu-verified", script=fixture_script.turn, decider=approve, verifier_scripts=scripts)
    assert first.start(fx.TRIGGER, fx.NOW)["status"] == "completed"
    first.close()
    seen: list[str] = []
    recheck = {"claims": claims_script(seen=seen), "contrarian": contrarian_script()}
    r = Runner(cfg, module_toolbox("fixture_registry"), "fu-verified", script=reads_then_resubmits, decider=approve, verifier_scripts=recheck)
    assert r.reopen(QUESTION, calls=3)["status"] == "completed"
    state = r.state()
    r.close()
    verdict = state["verdict"]
    assert verdict["status"] == "revised" and verdict["diff"]["changed"] and not verdict["diff"]["added"]
    assert len(seen) == 1 and all(f"Claim {c}" in seen[0] for c in verdict["diff"]["changed"])     # only what changed is checked again
    assert state["report"]["validation"]["verifier"] == "revised" and set(verdict["latest"]) >= set(verdict["diff"]["changed"])
