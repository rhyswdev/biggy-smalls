"""The agent over the real registry and the real bundle, with the scripted model: the done condition of
agent-design row 1 (build row B2). The script only does what any script could: it reads, cites handles it
was shown, and submits. It knows nothing about what the logs say."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage

import fixture_script
import golden
import parity
from smalls.agent.config import load_config
from smalls.agent.models import scripted_call as call
from smalls.agent.runner import Runner, bundle_toolbox
from smalls.contracts.report import Report
from smalls.tools import registry

TRIGGER = "Something is wrong with checkout. What is happening?"


def script(messages, tools):
    k = sum(isinstance(m, AIMessage) for m in messages)
    if k == 0:
        return "Read the recent logs and count them by level.", [
            call("search_logs", 1, ("h1", "recent records"), since="-30m", limit=5),
            call("search_logs", 2, ("h1", "a count per level"), since="-30m", group_by="level")]
    logs, counts = fixture_script.find(messages, "  "), fixture_script.find(messages, "count by level")
    logs = [h for h in logs if h.startswith(("log:", "k8s:"))]
    if k == 1:
        return "Record a first, open hypothesis.", [call("update_board", 3, hypotheses=[
            {"id": "h1", "statement": "Something changed in the last half hour.", "role": "cause", "status": "open", "subject": "unknown",
             "confidence": 0.3, "source": "derived", "support": logs[:2], "because": logs[:1]}])]
    return "Nothing conclusive: say so.", [call(
        "submit_report", 4, summary=f"Inconclusive. The logs were read ({logs[0]}) and counted ({counts[0]}).",
        impact={"business_service": "unknown", "cis": [], "priority": {"suggested": "unknown", "impact": "unknown", "urgency": "unknown"},
                "assignment_group": "unknown", "oncall": "unknown", "customer": "unknown"},
        hypotheses=[{"id": "h1"}], timeline=[], actions=[], do_not=[],
        alerts=[{"handle": h, "verdict": "noise"} for h in fixture_script.swept_alerts(messages)], open_questions=["What changed?"],
        stakeholder_update={"status": "Investigating.", "customer_impact": "unknown", "scope": "unknown", "changed_since_last": "First update.",
                            "next_steps": "Keep reading.", "next_update_at": "unknown"})]


@pytest.mark.parametrize("checkpoint", list(golden.CHECKPOINTS))
def test_a_scripted_run_over_the_real_registry_writes_a_complete_record(tmp_path, checkpoint, monkeypatch):
    monkeypatch.delenv("SMALLS_TAGS", raising=False)
    cfg = load_config(runs_dir=tmp_path)
    toolbox = bundle_toolbox(parity.BUNDLE)
    assert list(toolbox.read_tools) == list(registry.registry(tags=True)) and "search_logs" in toolbox.read_tools
    r = Runner(cfg, toolbox, f"real-{checkpoint}", script=script, eval_run=True)
    now = golden.ctx(checkpoint).now
    result = r.start(TRIGGER, now, checkpoint=checkpoint)
    state = r.state()
    r.close()
    assert result["status"] == "completed" and r.record.missing() == []
    report = Report.model_validate(json.loads((r.dir / "report.json").read_text()))
    assert report.validation.ok and [h.status for h in report.hypotheses] == ["open"]        # no leading hypothesis: inconclusive
    run = json.loads((r.dir / "run.json").read_text())
    assert (run["bundle"], run["tags"], run["registry"], run["checkpoint"]) == (parity.BUNDLE.name, True, "bundle", checkpoint)
    calls = [json.loads(line) for line in (r.dir / "calls.jsonl").read_text().splitlines()]
    sweep = [c for c in calls if c["by"] == "harness" and c["tool"] != "inventory"]
    assert [c["tool"] for c in sweep] == ["list_alerts", "list_changes", "read_chat"] and all(c["status"] == "ok" and c["round"] == 0 for c in sweep)
    assert [a.handle for a in report.alerts] == sweep[0]["shown"] and len(report.alerts) > 5       # a verdict for every swept stream
    rows_call, stats_call = [c for c in calls if c["tool"] == "search_logs"]
    visible = {row["handle"] for row in golden.visible(golden.ctx(checkpoint).store.now, "splunk/raw/")}
    assert len(rows_call["shown"]) == 5 and set(rows_call["shown"]) <= visible                 # every shown handle is a served, visible record
    assert stats_call["shown"][0].startswith("q:") and rows_call["native_query"].startswith("search index=")
    assert rows_call["window"][1] == run["now"] and rows_call["used_tag_input"] is False
    evidence = [json.loads(line) for line in (r.dir / "evidence.jsonl").read_text().splitlines()]
    assert {e["handle"] for e in evidence} == {h for c in calls for h in c["shown"]}
    assert all(e["t_known"] is None or e["t_known"] < run["now"] for e in evidence)
    first_turn = state["messages"][0].content
    assert "Logs, by sourcetype (" in first_turn and "Chat channels:" in first_turn and run["now"] in first_turn
    assert "Knowledge documents (" in first_turn and "Metric series (" in first_turn and "Tags, a search aid" in first_turn     # the full inventory


def tagged_script(messages, tools):
    """Reads the logs once through the tag input, then says it found nothing conclusive."""
    k = sum(isinstance(m, AIMessage) for m in messages)
    if k == 0:
        return "Read the records tagged as errors.", [call("search_logs", 1, ("h1", "records tagged errors"), since="-30m", limit=3, signal="errors")]
    return "Nothing conclusive: say so.", [call(
        "submit_report", 2, summary="Inconclusive.",
        impact={"business_service": "unknown", "cis": [], "priority": {"suggested": "unknown", "impact": "unknown", "urgency": "unknown"},
                "assignment_group": "unknown", "oncall": "unknown", "customer": "unknown"},
        hypotheses=[], timeline=[], actions=[], do_not=[],
        alerts=[{"handle": h, "verdict": "noise"} for h in fixture_script.swept_alerts(messages)], open_questions=["What changed?"],
        stakeholder_update={"status": "Investigating.", "customer_impact": "unknown", "scope": "unknown", "changed_since_last": "First update.",
                            "next_steps": "Keep reading.", "next_update_at": "unknown"})]


@pytest.mark.parametrize("tags", [False, True])
def test_the_tag_flag_is_a_setting_of_the_run_and_a_call_records_whether_it_used_a_tag_input(tmp_path, tags):
    """Row D9: the flag is a dimension a run is started with. Off, the model is offered no tag input and sees
    no tag; on, a call through a tag input is recorded as one."""
    import argparse

    from smalls.agent import __main__ as cli

    picked = cli._toolbox(argparse.Namespace(registry=None, bundle=str(parity.BUNDLE), tags="on" if tags else "off"))
    assert picked.describe["tags"] is tags
    assert cli._toolbox(argparse.Namespace(registry=None, bundle=str(parity.BUNDLE), tags="on"), saved={"tags": False, "registry": "bundle"}).describe["tags"] is False
    cfg = load_config(runs_dir=tmp_path)
    toolbox = bundle_toolbox(parity.BUNDLE, tags)
    assert ("signal" in toolbox.read_tools["search_logs"].args.model_fields) is tags
    r = Runner(cfg, toolbox, f"tags-{tags}", script=tagged_script, eval_run=True)
    r.start(TRIGGER, golden.ctx().now, checkpoint=golden.SECOND)
    state = r.state()
    r.close()
    run = json.loads((r.dir / "run.json").read_text())
    calls = [json.loads(line) for line in (r.dir / "calls.jsonl").read_text().splitlines()]
    mine = next(c for c in calls if c["by"] == "agent" and c["tool"] == "search_logs")
    first_turn = state["messages"][0].content
    shown = "\n".join(str(m.content) for m in state["messages"])
    assert run["tags"] is tags and ("Tags, a search aid" in first_turn) is tags and ("[tag " in shown) is tags
    if tags:
        assert mine["status"] == "ok" and mine["used_tag_input"] is True and "[tag filter signal=errors:" in shown
    else:
        assert mine["status"] == "error" and mine["used_tag_input"] is False and mine["shown"] == []      # the input does not exist: bad arguments
    assert all(c["used_tag_input"] is False for c in calls if c["by"] == "harness")                        # the sweep never uses a tag filter

