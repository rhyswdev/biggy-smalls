"""Carry-forward between checkpoints and the restart from the board (build row E2; agent-design axis 2; J8,
J9 and J16 of docs/decisions/tools-agent-build.md). Acceptance: a second-checkpoint run seeds from the
first; a carried leading hypothesis with no new handle is flagged; a scripted run with a low input limit
restarts once and keeps its ledger."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

import fixture_registry as fx
import fixture_script
from smalls.agent import __main__ as cli
from smalls.agent import carry, prompts
from smalls.agent.config import load_config
from smalls.agent.models import scripted_call as call
from smalls.agent.runner import Runner, module_toolbox
from smalls.contracts.handles import query_handle
from smalls.contracts.report import Report

EARLY_ISO, NOW_ISO = "2026-03-02T02:12:00Z", "2026-03-02T02:20:00Z"
IMPACT = {"business_service": "Reporting", "cis": ["report-exporter"], "priority": {"suggested": "P3", "impact": "medium", "urgency": "low"},
          "assignment_group": "Reporting", "oncall": "unknown", "customer": "Last night's reports were not delivered."}
UPDATE = {"status": "Cause identified.", "customer_impact": "Reports were not delivered.", "scope": "The nightly export.",
          "changed_since_last": "Nothing has changed since the first look.", "next_steps": "Fix before tonight.", "next_update_at": "unknown"}


@pytest.fixture
def cfg(tmp_path: Path):
    return load_config(runs_dir=tmp_path)


def prior_board(first_turn: str, starts: str) -> str:
    """The `{prior_board}` slot's value in a rendered first turn, found by the code's own opening words and
    ended where the template's text after the slot begins. It reads none of the author's wording."""
    tail = prompts.load("first_turn").split("{prior_board}", 1)[1].rstrip()
    value = first_turn.rstrip()[first_turn.index(starts):]
    assert value.endswith(tail)
    return value[: len(value) - len(tail)]


def start(cfg, run_id: str, script, now=fx.NOW, **kwargs):
    r = Runner(cfg, module_toolbox("fixture_registry"), run_id, script=script, eval_run=True)
    result = r.start(fx.TRIGGER, now, **kwargs)
    state = r.state()
    r.close()
    read = lambda name: json.loads((r.dir / name).read_text(encoding="utf-8"))      # noqa: E731
    rows = lambda name: [json.loads(line) for line in (r.dir / name).read_text(encoding="utf-8").splitlines()]      # noqa: E731
    return result, state, read, rows


def submit(messages, n: int, **hypothesis):
    return call("submit_report", n, summary="Second look: the release still explains it.", impact=IMPACT, hypotheses=[{"id": "h-release", **hypothesis}],
                timeline=[], actions=[], do_not=[], alerts=[{"handle": h, "verdict": "noise"} for h in fixture_script.swept_alerts(messages)],
                open_questions=[], stakeholder_update=UPDATE)


def first_turn_of(messages) -> str:
    """The first user turn, as a script sees it: after the system prompt."""
    return str(next(m for m in messages if isinstance(m, HumanMessage)).content)


def carried_support(messages) -> list[str]:
    """The support the earlier board lists for the release hypothesis, read from the first turn."""
    lines = first_turn_of(messages).splitlines()
    at = next(n for n, line in enumerate(lines) if line.startswith("h-release  "))
    return next(line for line in lines[at + 1:] if line.startswith("    support: ")).split(": ", 1)[1].split()


def stale_look(messages, tools):
    """A second look that reads nothing new and submits the first look's leading hypothesis again."""
    return "Nothing new to read.", [submit(messages, 20 + sum(isinstance(m, AIMessage) for m in messages))]


def fresh_look(messages, tools, aggregate: bool = False):
    """A second look that reads what became visible since, and rests the leading hypothesis on it."""
    k = sum(isinstance(m, AIMessage) for m in messages)
    if k == 0:
        args = {"terms": ["health"], "since": "-8m", **({"group_by": "level"} if aggregate else {})}
        return "What has happened since the first look?", [call("search_logs", 20, ("h-release", "the host is healthy again"), **args)]
    new = [h for h in fixture_script.find(messages, "count by level" if aggregate else "[health]")][:1]
    if k == 1:
        return "Record what is new.", [call("update_board", 21, hypotheses=[{"id": "h-release", "support": carried_support(messages) + new, "because": new}])]
    return "Submit.", [submit(messages, 22 + k)]


def fresh_aggregate(messages, tools):
    return fresh_look(messages, tools, aggregate=True)


def test_a_second_checkpoint_run_seeds_from_the_first(cfg):
    first_result, first_state, first_read, first_rows = start(cfg, "first", fixture_script.turn, now=fx.EARLY, checkpoint="early")
    assert first_result["status"] == "completed" and first_read("board.json")["as_of"] == EARLY_ISO
    result, state, read, rows = start(cfg, "second", fresh_look, checkpoint="late", carry_from="first")
    assert result["status"] == "completed" and read("run.json")["carried_from"] == {"run_id": "first", "as_of": EARLY_ISO}
    # The earlier board is in the first turn, each hypothesis under its id and marked re-test, with the rows it cites served again.
    first_turn = str(state["messages"][0].content)
    prior = prior_board(first_turn, f"The board as it stood at {EARLY_ISO}, from run first.")
    earlier = {h["id"]: h for h in first_read("board.json")["hypotheses"]}
    for hid, h in earlier.items():
        assert f"{hid}  [{h['status']} {h['role']}, confidence {h['confidence']:.2f}, blames {h['subject']}, source {h['source']}] re-test  {h['statement']}" in first_turn
        assert state["hypotheses"][hid]["carried"] is True and state["hypotheses"][hid]["statement"] == h["statement"]
    served = prior.split("Rows served again for this run, one line each")[1]
    assert all(f"    {h}  " in served for h in earlier["h-release"]["support"])
    # Every handle the earlier board cites was fetched again by this run, as carry_forward, and is citable.
    cited = {x for h in earlier.values() for name in carry.LISTS for x in h.get(name) or []} | {x for row in first_read("board.json")["timeline"] for x in row["evidence"]}
    carried = [c for c in rows("calls.jsonl") if c["by"] == "carry_forward"]
    assert carried and all(c["status"] == "ok" and c["round"] == 0 and c["tool"] == "get_record" for c in carried)
    assert {h for c in carried for h in c["shown"]} == cited and state["carried"]["dropped"] == {}
    evidence = rows("evidence.jsonl")
    assert cited <= {e["handle"] for e in evidence} and any(e["call_id"].startswith("carry_") for e in evidence)
    # A handle that is here only because it was carried does not count toward "ready": the same board was ready in the first look.
    lines = [e["data"]["line"] for e in read_events(cfg, "second") if e["type"] == "board_status"]
    assert "[board: ready" in [e["data"]["line"] for e in read_events(cfg, "first") if e["type"] == "board_status"][-1]
    # Nothing in this run's own first round showed the carried supports again, so they do not count yet (E2).
    assert lines[0].startswith("[board: not ready: leading h-release has no non-chat support, leading h-release has no discriminating evidence")
    # The report keeps one history row per look, and the leading hypothesis rests on a row that became known in between.
    report = Report.model_validate(read("report.json"))
    lead = next(h for h in report.hypotheses if h.id == "h-release")
    assert [row.as_of for row in lead.history] == [EARLY_ISO, NOW_ISO] and report.validation.continuity == "passed" and report.validation.ok
    new = set(lead.support) - set(earlier["h-release"]["support"])
    assert len(new) == 1 and all(e["t_known"] > EARLY_ISO for e in evidence if e["handle"] in new)       # known after the earlier look
    assert Report.model_validate(first_read("report.json")).validation.continuity is None        # nothing was carried into the first look


def read_events(cfg, run_id: str) -> list[dict]:
    return [json.loads(line) for line in (cfg.runs_dir / run_id / "events.jsonl").read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("script", [stale_look, fresh_aggregate], ids=["nothing new", "only a count"])
def test_a_carried_leading_hypothesis_with_no_new_handle_is_flagged(cfg, script):
    start(cfg, "first", fixture_script.turn, now=fx.EARLY)
    result, state, read, rows = start(cfg, "second", script, carry_from="first")
    answers = [str(m.content) for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "submit_report"]
    assert len(answers) == 2 and answers[0].startswith("Report not accepted.") and answers[1].startswith("Report accepted.")      # a finding, with its one retry
    assert (f"h-release was carried from the earlier look at {EARLY_ISO} and cites no row that became known after it and that a call of this run showed.") in answers[0]
    report = Report.model_validate(read("report.json"))
    assert result["status"] == "completed" and report.validation.continuity == "failed" and not report.validation.ok and report.validation.attempts == 2
    lead = next(h for h in report.hypotheses if h.id == "h-release")
    assert lead.status == "leading" and (script is stale_look or any(h.startswith("q:") for h in lead.support))      # a count never satisfies it


def test_what_the_earlier_board_cites_is_served_again_or_dropped_and_listed():
    box = module_toolbox("fixture_registry")
    ctx = box.context(fx.NOW, "seed-test")
    record, gone, note = "jk:report-export#57", "log:worker-01/00000000", "ic:note/0a1b2c3d"
    args = {"terms": ["ERROR"], "since": "-2h", "group_by": "level"}
    old_count = query_handle("search_logs|x", EARLY_ISO)
    earlier = {"run_id": "first", "as_of": EARLY_ISO, "checkpoints": {},
               "calls": [{"tool": "search_logs", "args": args, "status": "ok", "by": "agent", "shown": [old_count], "window": ["2026-03-02T00:12:00Z", EARLY_ISO]}],
               "board": {"as_of": EARLY_ISO, "open_questions": ["Why?"],
                         "hypotheses": [{"id": "h1", "statement": "S", "role": "cause", "status": "leading", "subject": "x", "confidence": 0.6, "source": "derived",
                                         "support": [record, old_count, gone, note], "against": [], "history": [{"round": 3}]}],
                         "timeline": [{"t_utc": "T1", "event": "kept", "evidence": [record]}, {"t_utc": "T2", "event": "lost", "evidence": [gone]}]}}
    written = []
    seeded = carry.seed(earlier, box.read_tools, ctx, lambda call_id, rows: written.append((call_id, [r.handle for r in rows])))
    new_count = seeded["carried"]["replaced"][old_count]
    assert new_count.startswith("q:") and new_count != old_count                       # minted again, for the window the earlier call resolved to
    again = next(e for e in seeded["ledger"] if e["tool"] == "search_logs")
    assert again["args"] == {**args, "since": "2026-03-02T00:12:00Z", "until": EARLY_ISO} and again["by"] == "carry_forward" and again["identity"] is None
    assert seeded["carried"]["dropped"] == {gone: "it no longer resolves", note: "an earlier note is not carried"}
    h1 = seeded["hypotheses"]["h1"]
    assert h1["support"] == [record, new_count] and h1["carried"] is True and h1["_seed"] is True and h1["status"] == "leading"
    assert h1["history"] == [{"round": 0, "call_id": "carry_forward", "from": None, "to": {"status": "leading", "confidence": 0.6}, "because": []}]
    assert h1["checkpoints"] == [{"as_of": EARLY_ISO, "status": "leading", "confidence": 0.6, "because": []}]
    assert [row["event"] for row in seeded["timeline"]] == ["kept"] and seeded["open_questions"] == ["Why?"]      # a row left with no evidence goes
    assert [call_id for call_id, _ in written] == [e["call_id"] for e in seeded["ledger"]] and len(seeded["ledger"]) == 2
    text = carry.render(seeded["hypotheses"], seeded["timeline"], seeded["open_questions"], head="HEAD", rows=seeded["rows"],
                        dropped=seeded["carried"]["dropped"], retest=True)
    assert text.splitlines()[0] == "HEAD" and "h1  [leading cause, confidence 0.60, blames x, source derived] re-test  S" in text
    assert f"    support: {record} {new_count}" in text and "timeline:\n    T1  kept  (jk:report-export#57)" in text and "open questions:\n    Why?" in text
    assert f"Not carried, and removed from the entries above: {gone} (it no longer resolves); {note} (an earlier note is not carried)." in text
    # Which handles are in the run only because they were carried, and the continuity rule on its own.
    ledger = [*seeded["ledger"], {"by": "agent", "status": "ok", "shown": [record], "t_known": {record: EARLY_ISO}}]
    assert carry.carried_only(seeded["ledger"]) == {record, new_count} and carry.carried_only(ledger) == {new_count}
    report = {"hypotheses": [{"id": "h1", "status": "leading", "support": [record, new_count]}]}
    assert carry.continuity(report, None, ledger) == (None, None)
    assert carry.continuity(report, seeded["carried"], ledger)[0] == "failed"            # shown by this run, but known before the earlier look
    later = [{"by": "agent", "status": "ok", "shown": [record], "t_known": {record: "2026-03-02T02:15:00Z"}}]
    assert carry.continuity(report, seeded["carried"], later) == ("passed", None)
    assert carry.continuity(report, seeded["carried"], [{**later[0], "by": "carry_forward"}])[0] == "failed"      # the carry-forward itself does not count
    assert carry.continuity({"hypotheses": [{"id": "h9", "status": "leading", "support": []}]}, seeded["carried"], ledger) == ("passed", None)      # a new lead


def after_restart(messages, tools):
    """The fixture script, until the conversation is restarted; then it reports from the board it is given."""
    first = first_turn_of(messages)
    if "The conversation was restarted from it to free context" not in first:
        return fixture_script.turn(messages, tools)
    k = sum(isinstance(m, AIMessage) for m in messages)
    return "The board has what is needed.", [call(
        "submit_report", 30 + k, summary="Reported from the board after a restart.", impact=IMPACT, hypotheses=[{"id": h} for h in ("h-full", "h-dying") if f"\n{h}  [" in first],
        timeline=[], actions=[], do_not=[], alerts=[{"handle": h, "verdict": "noise"} for h in fixture_script.swept_alerts(messages)], open_questions=[],
        stakeholder_update=UPDATE)]


def test_a_scripted_run_with_a_low_input_limit_restarts_once_and_keeps_its_ledger(cfg):
    _, plain, read, _ = start(cfg, "plain", fixture_script.turn)
    per_round = [c["usage"]["input_tokens"] for c in read("usage.json")["calls"]]
    assert per_round == sorted(per_round) and len(per_round) == 5
    limit = int((per_round[1] + per_round[2]) / 2 / cfg.context_fraction)          # the guard trips on the third model call
    small = replace(cfg, investigator=cfg.investigator.with_params(max_input_tokens=limit, max_tokens=0))
    r = Runner(small, module_toolbox("fixture_registry"), "restart", script=after_restart, eval_run=True)
    result = r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    assert r.config["configurable"]["thread_id"] == "restart.1" and state["restarts"] == 1 and state["restart_round"] == 3
    restarts = [e for e in read_events(small, "restart") if e["type"] == "restart"]
    assert len(restarts) == 1 and restarts[0]["data"]["thread"] == "restart.1" and restarts[0]["data"]["round"] == 3      # once
    # The ledger, the board and the budget carry over; only the conversation is new.
    ledger = state["ledger"]
    before = [e for e in ledger if e["by"] == "agent" and e["round"] in (1, 2)]
    assert [e["tool"] for e in before] == ["list_alerts", "list_changes", "read_chat", "search_logs", "search_logs", "update_board", "search_logs",
                                           "get_record"] and state["calls_used"] == 7
    assert [e["call_id"] for e in ledger if e["by"] == "harness"] == ["inventory"]          # nothing is read for the agent, before or after the restart
    assert not [e for e in ledger if e["round"] == 3 and e["by"] == "agent" and e["tool"] != "submit_report"]      # the turn that tripped the guard ran nothing
    assert set(state["hypotheses"]) == {"h-full", "h-dying"} and state["hypotheses"]["h-full"]["history"][0]["round"] == 2      # history and all
    first = str(state["messages"][0].content)
    assert isinstance(state["messages"][0], HumanMessage) and "The board of this run as it stands. The conversation was restarted from it to free context" in first
    assert "h-full  [leading cause, confidence 0.60, blames worker-01, source derived]  The run failed because its spool volume filled up." in first
    board_text = prior_board(first, "The board of this run as it stands.")
    assert "re-test" not in board_text and "Rows served again for this run" in board_text and "No space left on device" in board_text
    assert f"{small.call_budget - 7} tool calls, {small.call_budget + small.extra_turns - 3} model turns" in first      # what is left, not what it began with
    assert sum(isinstance(m, AIMessage) for m in state["messages"]) == 2 and isinstance(state["messages"][1], AIMessage)     # the agent's turn follows at once
    # The run then finishes on the new thread, citing what the earlier conversation was shown: nothing it cites is dropped.
    report = Report.model_validate(json.loads((r.dir / "report.json").read_text()))
    assert result["status"] == "completed" and {h.id for h in report.hypotheses} == {"h-full", "h-dying"}
    assert report.validation.dropped == [] and report.validation.flagged == ["h:h-dying"] and len(next(h for h in report.hypotheses if h.id == "h-full").support) == 3
    assert r.record.missing() == [] and json.loads((r.dir / "summary.json").read_text())["process"]["restarts"] == 1
    r.close()
    again = Runner(small, module_toolbox("fixture_registry"), "restart", script=after_restart, eval_run=True)
    with pytest.raises(SystemExit, match="nothing left to run"):
        again.resume()
    assert again.config["configurable"]["thread_id"] == "restart.1"                 # another process finds the live thread
    again.close()


def test_a_second_trip_of_the_guard_ends_the_run_incomplete(cfg):
    small = replace(cfg, investigator=cfg.investigator.with_params(max_input_tokens=1500, max_tokens=100))      # no conversation fits
    r = Runner(small, module_toolbox("fixture_registry"), "tiny", script=fixture_script.turn, eval_run=True)
    result = r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    assert result["status"] == "incomplete" and state["stop_reason"] == "the context limit was reached" and state["restarts"] == 1
    assert len([e for e in read_events(small, "tiny") if e["type"] == "restart"]) == 1 and r.record.missing() == []


def test_the_cli_carries_forward_and_refuses_what_it_cannot_carry(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SMALLS_RUNS_DIR", str(tmp_path))
    monkeypatch.syspath_prepend(os.path.dirname(os.path.abspath(__file__)))
    common = ["--trigger", fx.TRIGGER, "--registry", "fixture_registry", "--profile", "scripted", "--eval-run", "--no-thinking"]
    assert cli.main(["run", "--now", EARLY_ISO, "--script", "fixture_script:turn", "--run-id", "one", *common]) == 0
    assert cli.main(["run", "--now", NOW_ISO, "--script", "test_carry:fresh_look", "--run-id", "two", "--carry-from", "one", *common]) == 0
    assert json.loads((tmp_path / "two" / "run.json").read_text())["carried_from"] == {"run_id": "one", "as_of": EARLY_ISO}
    assert json.loads((tmp_path / "two" / "report.json").read_text())["validation"]["continuity"] == "passed"
    capsys.readouterr()
    with pytest.raises(SystemExit, match="cannot carry forward from no-such-run"):
        cli.main(["run", "--now", NOW_ISO, "--script", "test_carry:fresh_look", "--run-id", "three", "--carry-from", "no-such-run", *common])
    with pytest.raises(SystemExit, match="is not before this run's now"):
        cli.main(["run", "--now", EARLY_ISO, "--script", "test_carry:fresh_look", "--run-id", "four", "--carry-from", "two", *common])
