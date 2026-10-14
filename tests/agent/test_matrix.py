"""The matrix runner, the no-tools baseline and the process metrics (build row D6 of
docs/decisions/tools-agent-build.md). Acceptance: one command runs arm C at the second checkpoint three
times. Everything here is scripted: no key, no network, and every run a process of its own."""

from __future__ import annotations

import json
import os

import pytest
from langchain_core.messages import AIMessage

import fixture_script
import golden
import parity
from eval import baseline, matrix
from smalls.agent import metrics
from smalls.agent.config import load_config
from smalls.agent.models import scripted_call as call
from smalls.agent.record import RunRecord
from smalls.agent.runner import Runner, bundle_toolbox
from smalls.contracts.report import Report

BUNDLE = str(parity.BUNDLE)
IMPACT = {"business_service": "unknown", "cis": [], "priority": {"suggested": "unknown", "impact": "unknown", "urgency": "unknown"},
          "assignment_group": "unknown", "oncall": "unknown", "customer": "unknown"}
UPDATE = {"status": "Investigating.", "customer_impact": "unknown", "scope": "unknown", "changed_since_last": "First update.",
          "next_steps": "Keep reading.", "next_update_at": "unknown"}
MADE_UP = "log:pay-02/00000000"


def baseline_script(messages, tools):
    """Arm A's one turn: a report from the chat and the alerts, with one handle nothing showed."""
    names = [t.get("name") or (t.get("function") or {}).get("name") for t in tools]
    assert names == ["submit_report"], names                                        # the only tool the model is given
    assert sum(isinstance(m, AIMessage) for m in messages) == 0                     # and this is its only turn
    alerts = fixture_script.swept_alerts(messages)
    chat = [h for h in fixture_script.find(messages, "  ") if h.startswith("slack:")]
    return "One turn.", [call(
        "submit_report", 1, summary=f"People are discussing it ({chat[0]}) and an alert is firing ({alerts[0]}).", impact=IMPACT,
        hypotheses=[{"id": "h1", "statement": "Something is failing.", "role": "cause", "status": "leading", "subject": "unknown", "confidence": 0.6,
                     "support": [alerts[0], chat[0], MADE_UP], "against": []}],
        timeline=[], actions=[], do_not=[], alerts=[{"handle": h, "verdict": "noise"} for h in alerts], open_questions=["What changed?"],
        stakeholder_update=UPDATE)]


def broken_script(messages, tools):
    raise RuntimeError("this script always fails")


@pytest.fixture
def subprocess_path(monkeypatch):
    """A run started by the matrix is a new process: it finds the test scripts by path, as a person would."""
    here = os.path.dirname(os.path.abspath(__file__))
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([here, os.path.dirname(here), os.environ.get("PYTHONPATH", "")]))
    monkeypatch.delenv("SMALLS_TAGS", raising=False)


def read(folder, name):
    return json.loads((folder / name).read_text(encoding="utf-8"))


def lines(folder, name):
    return [json.loads(line) for line in (folder / name).read_text(encoding="utf-8").splitlines()]


def test_one_command_runs_arm_c_at_the_second_checkpoint_three_times(tmp_path, subprocess_path, capsys):
    assert (matrix.REGRESSION, golden.SECOND) == ({"arms": "C", "checkpoints": "1452", "reps": 3}, "1452")       # the regression set is the default
    code = matrix.main(["--bundle", BUNDLE, "--profile", "scripted", "--script", "test_agent_real_tools:script", "--runs-dir", str(tmp_path), "--label", "reg"])
    out = capsys.readouterr().out
    assert code == 0 and "matrix reg: 3 runs, one process each" in out
    names = [f"reg-C-1452-tags-r{n}" for n in (1, 2, 3)]
    for name in names:
        folder = tmp_path / name
        assert RunRecord(tmp_path, name).missing() == [], name                       # a complete record, each time
        run, summary = read(folder, "run.json"), read(folder, "summary.json")
        assert (run["arm"], run["checkpoint"], run["tags"], run["durability"], run["eval_run"], run["bundle"]) == ("C", "1452", True, "exit", True, parity.BUNDLE.name)
        assert run["now"] == golden.ctx(golden.SECOND).now.strftime("%Y-%m-%dT%H:%M:%SZ") and run["trigger"].strip()
        assert summary["status"] == "completed" and summary["process"]["tool_calls"] == 5 and summary["process"]["model_turns"] == 3     # its own first look included
        Report.model_validate(read(folder, "report.json"))
    index = read(tmp_path, "reg.matrix.json")
    assert [row["run_id"] for row in index["runs"]] == names and all(row["exit"] == 0 for row in index["runs"])
    assert "arm C at 1452, tags on: 3 runs, 3 completed. Mean, with the lowest and highest run:" in out
    assert "  tool calls: 5 (5 to 5)" in out and "  model turns: 3 (3 to 3)" in out and all(name in out for name in names)


def test_the_tags_flag_and_the_arms_are_dimensions_and_a_dry_run_starts_nothing(tmp_path, capsys):
    code = matrix.main(["--bundle", BUNDLE, "--arms", "a,C", "--checkpoints", "1440,1452", "--tags", "on,off", "--reps", "2", "--runs-dir", str(tmp_path),
                        "--label", "dry", "--dry-run"])
    out = capsys.readouterr().out.splitlines()
    assert code == 0 and out[0] == "matrix dry: 16 runs, one process each" and len(out) == 17 and list(tmp_path.iterdir()) == []
    agent, base = [ln for ln in out[1:] if " -m smalls.agent run " in ln], [ln for ln in out[1:] if " -m eval.baseline " in ln]
    assert len(agent) == len(base) == 8
    assert all("--arm C --eval-run --durability exit" in ln for ln in agent) and sum("--tags off" in ln for ln in out) == 8
    assert all(f"--now 14:{ln.split('--checkpoint 14')[1][:2]} " in ln and f"triggers/14{ln.split('--checkpoint 14')[1][:2]}.md" in ln for ln in out[1:])
    assert len({ln.split("--run-id ")[1].split()[0] for ln in out[1:]}) == 16       # one id per run
    assert matrix.clock("1452") == "14:52" and matrix.clock("2026-10-13T14:52:00Z") == "2026-10-13T14:52:00Z"
    with pytest.raises(SystemExit):
        matrix.main(["--bundle", BUNDLE, "--arms", "Z", "--dry-run"])
    assert [c["rep"] for c in matrix.cells(["C"], ["1452"], [True], 3)] == [1, 2, 3]


def test_a_run_that_fails_is_a_row_and_the_matrix_goes_on(tmp_path, subprocess_path, capsys):
    code = matrix.main(["--bundle", BUNDLE, "--profile", "scripted", "--script", "test_matrix:broken_script", "--runs-dir", str(tmp_path), "--label", "bad",
                        "--reps", "2", "--tags", "off"])
    out = capsys.readouterr().out
    rows = read(tmp_path, "bad.matrix.json")["runs"]
    assert code == 1 and len(rows) == 2 and all(row["summary"]["status"] in ("paused", "failed") and row["exit"] != 0 for row in rows)
    assert all(RunRecord(tmp_path, row["run_id"]).missing() == [] for row in rows)   # a failed run still leaves a whole record
    assert "arm C at 1452, tags off: 2 runs, 0 completed" in out
    assert matrix.read_summary(tmp_path, "no-such-run")["status"] == "no record"


@pytest.mark.parametrize("checkpoint", list(golden.CHECKPOINTS))
def test_arm_a_is_one_model_call_over_the_chat_and_the_alerts(tmp_path, checkpoint, monkeypatch):
    monkeypatch.setenv("SMALLS_RUNS_DIR", str(tmp_path))
    result = baseline.run(BUNDLE, matrix.clock(checkpoint), "What is happening?", checkpoint=checkpoint, profile="scripted", script=baseline_script,
                          run_id=f"a-{checkpoint}", runs_dir=tmp_path)
    folder = tmp_path / f"a-{checkpoint}"
    assert result["status"] == "completed" and RunRecord(tmp_path, f"a-{checkpoint}").missing() == []
    run, calls, usage, summary = read(folder, "run.json"), lines(folder, "calls.jsonl"), read(folder, "usage.json"), read(folder, "summary.json")
    assert (run["arm"], run["read_tools"], run["offered"], run["durability"]) == ("A", ["list_alerts", "read_chat"], ["submit_report"], "exit")
    assert run["context"]["single_call"] is True and run["context"]["call_budget"] == 0 and run["context"]["verifier"] is False
    # The harness reads the alerts and the chat through the same call every tool goes through; the model reads nothing.
    assert [(c["by"], c["tool"]) for c in calls] == [("harness", "inventory"), ("harness", "list_alerts"), ("harness", "read_chat"), ("agent", "submit_report")]
    assert all(c["status"] == "ok" for c in calls) and calls[1]["shown"] and calls[2]["shown"]
    assert len(usage["calls"] if isinstance(usage, dict) else usage) == 1 and summary["process"]["model_turns"] == 1 and summary["process"]["tool_calls"] == 0
    report = Report.model_validate(read(folder, "report.json"))
    h1 = report.hypotheses[0]
    assert [h.id for h in report.hypotheses] == ["h1"] and h1.source == "derived" and h1.status == "leading"       # new in the report, as derived
    assert MADE_UP not in h1.support and set(h1.support) <= set(calls[1]["shown"]) | set(calls[2]["shown"]) and len(h1.support) == 2
    assert report.validation.dropped == [MADE_UP] and report.validation.attempts == 1 and not report.validation.ok     # no second turn to fix it in
    assert {a.handle for a in report.alerts} == set(calls[1]["shown"]) and report.proposed_actions == []
    first_turn = lines(folder, "events.jsonl")
    assert not any(e["type"] == "tool_call_proposed" and e["data"]["tool"] == "list_changes" for e in first_turn)


def test_the_cli_of_arm_a_and_a_normal_run_is_unchanged_by_the_single_call_setting(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SMALLS_RUNS_DIR", str(tmp_path))
    monkeypatch.syspath_prepend(os.path.dirname(os.path.abspath(__file__)))
    code = baseline.main(["--bundle", BUNDLE, "--now", "14:52", "--trigger", "What is happening?", "--checkpoint", "1452", "--profile", "scripted",
                          "--script", "test_matrix:baseline_script", "--run-id", "cli-a", "--tags", "off"])
    assert code == 0 and "arm A: completed; run record: " in capsys.readouterr().out and read(tmp_path / "cli-a", "run.json")["tags"] is False
    with pytest.raises(SystemExit):
        baseline.main(["--bundle", BUNDLE, "--now", "14:52"])
    # In a run that is not single-call, a hypothesis the board does not hold cannot appear in the report: the first submission is sent back.
    cfg = load_config(runs_dir=tmp_path)
    def looks_first(messages, tools):       # an agent run reads nothing it has not asked for, so it lists the alerts and reads the chat first
        if not any(isinstance(m, AIMessage) for m in messages):
            return "Look first.", [c for c in fixture_script.OPENING if c["name"] in ("list_alerts", "read_chat")]
        return baseline_script([m for m in messages if not isinstance(m, AIMessage)], [{"name": "submit_report"}])

    r = Runner(cfg, bundle_toolbox(parity.BUNDLE, False), "normal", script=looks_first, eval_run=True)
    r.start("What is happening?", golden.ctx().now)
    r.close()
    validation = read(tmp_path / "normal", "report.json")["validation"]
    assert validation["attempts"] == 2 and read(tmp_path / "normal", "report.json")["hypotheses"] == []


def test_process_metrics_are_counted_from_the_runs_own_state():
    ledger = [{"by": "harness", "kind": "read", "tool": "list_alerts", "status": "ok", "round": 0, "shown": ["dd:mon1", "dd:mon2"]},
              {"by": "agent", "kind": "read", "tool": "search_logs", "status": "ok", "round": 1, "shown": ["log:a/00000001"], "used_tag_input": True},
              {"by": "agent", "kind": "read", "tool": "search_logs", "status": "repeat", "round": 2, "shown": [], "repeat_of": "c1"},
              {"by": "agent", "kind": "read", "tool": "query_metrics", "status": "ok", "round": 2, "shown": [], "empty": True},
              {"by": "agent", "kind": "read", "tool": "get_record", "status": "error", "round": 3, "shown": []},
              {"by": "agent", "kind": "internal", "tool": "update_board", "status": "ok", "round": 3, "shown": []}]
    usage = [{"node": "agent", "round": n, "usage": {"input_tokens": 1000 * n, "output_tokens": 100}} for n in (1, 2, 3, 4)]
    hypotheses = {"h1": {"subject": "x", "role": "cause", "status": "leading", "source": "derived",
                         "history": [{"round": 2, "to": {"status": "open"}}, {"round": 4, "to": {"status": "leading"}}]},
                  "h2": {"subject": "y", "role": "symptom", "status": "rejected", "source": "stated", "history": [{"round": 3, "to": {"status": "rejected"}}]}}
    report = {"summary": "see dd:mon1.", "hypotheses": [{"id": "h1", "support": ["log:a/00000001"], "against": []}], "alerts": [{"handle": "dd:mon2", "verdict": "noise"}]}
    values = {"ledger": ledger, "hypotheses": hypotheses, "report": report, "calls_used": 2,
              "gate_log": [{"by": {"who": "policy"}, "decision": "rejected"}, {"by": {"who": "auto"}, "decision": "approved"}]}
    m = metrics.process(values, usage, {"stuck": 1}, budget=2, turn_cap=8)
    assert (m["tool_calls"], m["calls_by_tool"], m["repeated_calls"], m["empty_results"], m["error_results"]) == (2, {"query_metrics": 1, "search_logs": 1}, 1, 1, 1)
    assert (m["tag_input_calls"], m["refused_by_policy"], m["model_turns"], m["stuck_events"], m["restarts"]) == (1, 1, 4, 1, 0)
    assert (m["shown_handles"], m["cited_handles"], m["tokens_total"]) == (3, 3, 10_400) and m["tokens_per_cited_handle"] == round(10_400 / 3, 1)
    assert m["budget"] == {"calls": 2, "calls_used": 2, "hit": True, "turn_cap": 8, "turns_used": 4, "turn_cap_hit": False}
    h1, h2 = m["hypotheses"]
    assert h1["first_seen"] == {"round": 2, "tool_calls_before": 1, "tokens_through": 3200}        # the calls of earlier rounds, the tokens through this one
    assert h1["first_leading"] == {"round": 4, "tool_calls_before": 2, "tokens_through": 10_400} and h1["changes"] == 2
    assert h2["first_seen"]["round"] == 3 and h2["first_leading"] is None                          # which one is right is the scorer's to say
    empty = metrics.process({}, [], {}, budget=30, turn_cap=36)
    assert empty["tokens_per_cited_handle"] is None and empty["cited_share_of_shown"] is None and empty["budget"]["hit"] is False
