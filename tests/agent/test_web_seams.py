"""The seams the web UI needs from the agent (docs/decisions/ui-build.md section 8), each over the fixture
registry with the scripted model: S1 (decisions from the web are recorded as such) and the ones after it."""

from __future__ import annotations

from pathlib import Path

import pytest

import fixture_registry as fx
import fixture_script
from smalls.agent.config import load_config
from smalls.agent.runner import Runner, module_toolbox
from smalls.contracts.report import Report


@pytest.fixture
def cfg(tmp_path: Path):
    return load_config(runs_dir=tmp_path)


def runner(cfg, run_id: str, **kwargs) -> Runner:
    kwargs.setdefault("script", fixture_script.turn)
    return Runner(cfg, module_toolbox("fixture_registry"), run_id, **kwargs)


def approve(cards):
    return {c["call_id"]: {"approve": True} for c in cards}


# -- S1: via web -----------------------------------------------------------------------------------------------

def test_s1_a_decision_made_on_the_web_is_recorded_as_one(cfg):
    r1 = runner(cfg, "web", stop_at_gate=True, attended=True, via="web")
    assert r1.start(fx.TRIGGER, fx.NOW)["status"] == "paused"
    r1.close()
    r2 = runner(cfg, "web", attended=True, via="web")
    pending = r2.state()["pending"]["calls"]
    assert r2.resume({c["call_id"]: {"approve": False, "reason": "not tonight"} for c in pending})["status"] == "completed"
    state = r2.state()
    r2.close()
    people = [g for g in state["gate_log"] if g["by"]["who"] == "human"]
    assert people and all(g["by"]["via"] == "web" for g in people)
    report = Report.model_validate(state["report"])
    assert report.proposed_actions[0].by.via == "web" and report.proposed_actions[0].decision == "rejected"
    assert all(g["by"]["via"] == "web" for g in state["gate_log"] if g["by"]["who"] == "auto")     # the switch was the web's too


def test_s1_the_terminal_and_unattended_runs_keep_their_own_via(cfg):
    attended = runner(cfg, "cli", attended=True, decider=approve)
    attended.start(fx.TRIGGER, fx.NOW)
    state = attended.state()
    attended.close()
    assert {g["by"]["via"] for g in state["gate_log"] if g["by"]["who"] == "human"} == {"cli"}
    unattended = runner(cfg, "unattended")
    unattended.start(fx.TRIGGER, fx.NOW)
    state = unattended.state()
    unattended.close()
    assert {g["by"]["via"] for g in state["gate_log"]} == {"headless"}


# -- S5: stop ----------------------------------------------------------------------------------------------------

def test_s5_a_stopped_run_ends_paused_with_its_partial_board_and_resumes_to_the_end(cfg):
    from langchain_core.messages import AIMessage

    holder: dict[str, Runner] = {}

    def stops_at_turn_three(messages, tools):
        if sum(isinstance(m, AIMessage) for m in messages) == 2:
            holder["r"].request_stop()          # as the API would, from another thread, while the model streams
        return fixture_script.turn(messages, tools)

    r = runner(cfg, "stop", script=stops_at_turn_three, decider=approve)
    holder["r"] = r
    result = r.start(fx.TRIGGER, fx.NOW)
    state = r.graph.get_state(r.config)
    events = r.record.events()
    r.close()
    assert result["status"] == "paused" and result["stopped"] and (state.next or state.tasks)      # something is left to run
    assert result["report"]["summary"].startswith("Incomplete (the incident commander stopped the run)")
    assert {h["id"] for h in result["report"]["hypotheses"]} == {"h-full", "h-dying"}               # the board as it stood
    assert events[-1].type == "run_paused" and events[-1].data["stopped"] and r.record.missing() == []
    again = runner(cfg, "stop", decider=approve)
    assert again.resume()["status"] == "completed"
    final = again.state()
    again.close()
    assert sum(1 for row in final["usage"] if row["node"] == "agent") == 5                          # the interrupted turn counted once
    assert [e["call_id"] for e in final["ledger"]].count("call_06_get_record") == 1


def test_s5_a_stop_on_a_finished_run_changes_nothing(cfg):
    r = runner(cfg, "stop-late", decider=approve)
    r.start(fx.TRIGGER, fx.NOW)
    r.request_stop()
    assert r.state()["status"] == "completed"
    r.close()


def test_s5_a_stop_from_another_thread_lands_while_the_model_streams(cfg):
    import threading
    from dataclasses import replace

    slow = replace(cfg, investigator=cfg.investigator.with_params(delay_s=0.01))
    r = runner(slow, "stop-thread", decider=approve)
    threading.Timer(0.25, r.request_stop).start()
    result = r.start(fx.TRIGGER, fx.NOW)
    r.close()
    assert result["status"] == "paused" and result["stopped"]
    again = runner(cfg, "stop-thread", decider=approve)
    assert again.resume()["status"] == "completed"
    again.close()


# -- S4: notes while a run is running ----------------------------------------------------------------------------

def test_s4_a_note_added_while_the_run_runs_reaches_the_model_at_the_next_round(cfg):
    from langchain_core.messages import AIMessage, ToolMessage

    holder: dict[str, Runner] = {}
    seen: list[str] = []

    def adds_a_note(messages, tools):
        if sum(isinstance(m, AIMessage) for m in messages) == 1:
            seen.append(holder["r"].add_note("The disk was replaced last week.")["handle"])
        return fixture_script.turn(messages, tools)

    r = runner(cfg, "note-live", script=adds_a_note, decider=approve)
    holder["r"] = r
    assert r.start(fx.TRIGGER, fx.NOW)["status"] == "completed"
    state = r.state()
    events = r.record.events()
    r.close()
    [handle] = seen
    assert [n["handle"] for n in state["ic_notes"]] == [handle] and r.pending_notes() == []
    carrying = [m for m in state["messages"] if isinstance(m, ToolMessage) and handle in str(m.content)]
    assert len(carrying) == 1 and "The disk was replaced last week." in str(carrying[0].content)      # shown once, as a row
    assert [e.data["handle"] for e in events if e.type == "ic_note"] == [handle]                           # one event, at delivery


def test_s4_a_note_no_round_took_is_delivered_by_the_follow_up(cfg):
    from langchain_core.messages import AIMessage

    holder: dict[str, Runner] = {}

    def adds_a_note_on_the_last_turn(messages, tools):
        if sum(isinstance(m, AIMessage) for m in messages) == 4:
            holder["r"].add_note("Is the second worker affected?")
        return fixture_script.turn(messages, tools)

    r = runner(cfg, "note-late", script=adds_a_note_on_the_last_turn, decider=approve)
    holder["r"] = r
    assert r.start(fx.TRIGGER, fx.NOW)["status"] == "completed"
    [left] = r.pending_notes()
    assert left["handle"] not in {n["handle"] for n in r.state()["ic_notes"]}

    r.close()

    def answers(messages, tools):
        return "Answered.", [], {"text": "No: worker-02 finished its job."}

    again = Runner(cfg, module_toolbox("fixture_registry"), "note-late", script=answers, decider=approve)
    again._inbox = [left]               # the API keeps its runner; a new one with the same inbox stands in for it here
    assert again.reopen()["status"] == "completed"
    state = again.state()
    again.close()
    assert state["followups"][-1]["note"] == left["handle"] and state["ic_notes"][-1]["handle"] == left["handle"]


def test_s4_notes_from_another_thread_are_all_delivered(cfg):
    import threading
    from dataclasses import replace

    slow = replace(cfg, investigator=cfg.investigator.with_params(delay_s=0.005))
    r = runner(slow, "note-thread", decider=approve)
    added: list[str] = []
    timers = [threading.Timer(0.05 * k, lambda k=k: added.append(r.add_note(f"note {k}")["handle"])) for k in range(1, 5)]
    for t in timers:
        t.start()
    assert r.start(fx.TRIGGER, fx.NOW)["status"] == "completed"
    for t in timers:
        t.join()
    delivered = {n["handle"] for n in r.state()["ic_notes"]} | {n["handle"] for n in r.pending_notes()}
    r.close()
    assert len(added) == 4 and set(added) == delivered                                                     # none lost, none twice


# -- S2: approve with edit ---------------------------------------------------------------------------------------

def interactive(cfg, run_id: str) -> Runner:
    """A run that asks before every read, paused at its first gate (the opening sweep's card)."""
    from dataclasses import replace

    r = runner(replace(cfg, auto_approve=False), run_id, stop_at_gate=True, attended=True, via="web")
    assert r.start(fx.TRIGGER, fx.NOW)["status"] == "paused"
    return r


def answer_until(r: Runner, call_id: str, decide) -> Runner:
    """Approve every card as proposed until the one named, which `decide` answers; then run to the next stop."""
    while True:
        cards = r.state()["pending"]["calls"]
        answers = {c["call_id"]: (decide(c) if c["call_id"] == call_id else {"approve": True}) for c in cards}
        result = r.resume(answers)
        if call_id in answers or result["status"] != "paused":
            return r


def test_s2_an_approved_edit_runs_the_edited_arguments_and_both_are_kept(cfg):
    import json

    r = interactive(cfg, "edit")
    answer_until(r, "call_01_search_logs", lambda c: {"approve": True, "args": {**c["args"], "since": "-90m"}})
    while r.state().get("pending"):
        r.resume({c["call_id"]: {"approve": True} for c in r.state()["pending"]["calls"]})
    state = r.state()
    r.close()
    entry = next(e for e in state["ledger"] if e["call_id"] == "call_01_search_logs")
    assert entry["args"]["since"] == "-2h" and entry["edited_args"]["since"] == "-90m" and entry["status"] == "ok"
    assert entry["window"][0] == "2026-03-02T00:50:00Z"                                                   # ninety minutes before now: the edit ran
    decision = next(g for g in state["gate_log"] if g["call_id"] == "call_01_search_logs")
    assert decision["decision"] == "approved" and decision["edited_args"] == entry["edited_args"] and decision["by"] == {"who": "human", "via": "web"}
    from langchain_core.messages import ToolMessage
    result = next(m for m in state["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "call_01_search_logs")
    assert str(result.content).startswith('EDITED by the incident commander: the call ran with {"terms": ["report-export"], "since": "-90m"}')
    row = next(json.loads(line) for line in (r.dir / "calls.jsonl").read_text().splitlines() if '"call_01_search_logs"' in line)
    assert row["args"]["since"] == "-2h" and row["edited_args"]["since"] == "-90m" and row["gate"]["edited_args"]["since"] == "-90m"


def test_s2_an_edit_that_does_not_validate_is_not_run(cfg):
    r = interactive(cfg, "edit-bad")
    answer_until(r, "call_01_search_logs", lambda c: {"approve": True, "args": {**c["args"], "limit": 100000}})
    state = r.state()
    r.close()
    decision = next(g for g in state["gate_log"] if g["call_id"] == "call_01_search_logs")
    assert decision["decision"] == "deferred" and decision["reason"].startswith("the edited arguments are not valid for search_logs")
    assert next(e for e in state["ledger"] if e["call_id"] == "call_01_search_logs")["status"] == "deferred"


def test_s2_an_action_cannot_be_approved_with_edits(cfg):
    from smalls.agent import gate

    card = {"call_id": "c1", "tool": "propose_action", "kind": "action", "args": {}}
    assert gate.edited(card, {"approve": True, "args": {"target": "x"}}, None) == (None, gate.NOT_EDITABLE)
    assert gate.edited(card, {"approve": True}, None) == (None, None)


# -- S6: the auto-approve switch mid-run -------------------------------------------------------------------------

def test_s6_the_switch_applies_from_the_next_round_and_never_to_an_action(cfg):
    from langchain_core.messages import AIMessage

    holder: dict[str, Runner] = {}

    def turns_it_off_at_turn_three(messages, tools):
        if sum(isinstance(m, AIMessage) for m in messages) == 2:
            holder["r"].set_auto_approve(False)
        return fixture_script.turn(messages, tools)

    from dataclasses import replace
    r = runner(replace(cfg, auto_approve=False), "switch", script=turns_it_off_at_turn_three, stop_at_gate=True, attended=True, via="web")
    holder["r"] = r
    assert r.start(fx.TRIGGER, fx.NOW)["status"] == "paused"                          # off at start: the first round asks
    r.set_auto_approve(True)                                                           # on while it waits: the asked card still waits
    assert [c["call_id"] for c in r.state()["pending"]["calls"]][0] == "call_97_list_alerts"
    assert r.resume({c["call_id"]: {"approve": True} for c in r.state()["pending"]["calls"]})["status"] == "paused"
    state = r.state()
    by_call = {g["call_id"]: g["by"]["who"] for g in state["gate_log"]}
    assert by_call["call_01_search_logs"] == "human" and by_call["call_04_search_logs"] == "auto"     # round one was asked, round two ran on the switch
    assert [c["tool"] for c in state["pending"]["calls"]] == ["get_record", "get_record", "search_logs", "search_logs"]   # off: round three asks
    while r.state().get("pending"):
        cards = r.state()["pending"]["calls"]
        r.resume({c["call_id"]: {"approve": True} for c in cards})
    state = r.state()
    events = r.record.events()
    r.close()
    assert next(g for g in state["gate_log"] if g["tool"] == "propose_action")["by"]["who"] == "human"     # an action always asks
    assert [e.data["on"] for e in events if e.type == "auto_approve"] == [True, False]


# -- S7: the native query before a read runs ---------------------------------------------------------------------

def test_s7_read_cards_carry_the_native_query_and_window_before_the_read_runs(tmp_path, monkeypatch):
    from dataclasses import replace

    import golden
    import parity
    from test_agent_real_tools import TRIGGER, script
    from smalls.agent.runner import bundle_toolbox

    monkeypatch.delenv("SMALLS_TAGS", raising=False)
    cfg = replace(load_config(runs_dir=tmp_path), auto_approve=False)
    r = Runner(cfg, bundle_toolbox(parity.BUNDLE), "s7", script=script, stop_at_gate=True, attended=True, via="web")
    assert r.start(TRIGGER, golden.ctx().now, checkpoint=golden.SECOND)["status"] == "paused"
    first = r.state()["pending"]["calls"]
    events = [e for e in r.record.events() if e.type == "gate_pending"]
    r.close()
    assert [c["tool"] for c in first] == ["list_alerts", "list_changes", "read_chat", "search_logs", "search_logs"]     # the agent's own first look
    from smalls.tools import windows
    assert all(c["window"][1] == windows.iso(golden.ctx().store.now) for c in first[:3])     # resolved, and clamped to now
    assert first[0]["native_query"].startswith("(no single native query") and first[2]["native_query"].startswith("after:")
    reads = first[3:]
    assert reads[0]["native_query"].startswith("search index=") and reads[1]["native_query"].endswith("| stats count by level")
    assert events[-1].data["calls"][3]["native_query"] == reads[0]["native_query"]          # the card the browser is sent carries it
