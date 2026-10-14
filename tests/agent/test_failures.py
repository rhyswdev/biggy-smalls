"""Failure handling (build row D1; docs/research/agent-design.md axis 8 and section 8.4): one scripted test
per row of axis 8, a provider timeout that leaves a full record and a resumable checkpoint, and no key
value in any file of a run directory. All offline."""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage

import fixture_registry as fx
import fixture_script
from smalls.agent import __main__ as cli
from smalls.agent import gate
from smalls.agent.config import load_config
from smalls.agent.events import Bus, Event, JsonlSink, last_seq, read_events
from smalls.agent.models import retryable
from smalls.agent.models import scripted_call as call
from smalls.agent.record import FILES, RunsIndex, check_run_id, new_run_id, scrub
from smalls.agent.runner import Runner, Toolbox, module_toolbox, resume_hint
from smalls.contracts.envelope import Envelope
from smalls.contracts.report import Report


@pytest.fixture
def cfg(tmp_path: Path):
    return load_config(runs_dir=tmp_path)


def approve(cards):
    return {c["call_id"]: {"approve": True} for c in cards}


def runner(cfg, run_id: str, script=fixture_script.turn, **kwargs) -> Runner:
    kwargs.setdefault("decider", approve)
    return Runner(cfg, module_toolbox("fixture_registry"), run_id, script=script, **kwargs)


def turns(messages) -> int:
    return sum(isinstance(m, AIMessage) for m in messages)


def events_of(r: Runner) -> list[Event]:
    return read_events(r.dir / "events.jsonl")


# -- axis 8, row 1: tool error, bad arguments, unparseable JSON ----------------------------------------------


def test_row1_every_bad_call_is_answered_with_text_and_the_run_goes_on(cfg):
    bad = {"call_91_search_logs", "call_92_search_logs", "call_93_search_logs", "call_94_read_chat"}

    def script(messages, tools):
        if turns(messages) == 0:
            return "four ways to get it wrong", [
                call("search_logs", 91, ("h", "x"), since="-1h", limit=999),                      # bad arguments
                {"name": "search_logs", "args": '{"since": -1h, terms}', "id": "call_92_search_logs", "type": "tool_call"},        # not JSON
                call("search_logs", 93, since="-1h"),                                               # no rationale
                call("read_chat", 94, ("h", "x"), since="-1h")]                                     # a tool that breaks
        first = next(m for m in messages if isinstance(m, AIMessage))
        rest = [m for m in messages if m is not first and not (isinstance(m, ToolMessage) and m.tool_call_id in bad)]
        return fixture_script.turn(rest, tools)                                                     # then the ordinary investigation

    base = module_toolbox("fixture_registry")

    def broken(args, ctx):
        raise ZeroDivisionError("/secret/path/store.db is locked")

    tools = {**base.read_tools, "read_chat": replace(base.read_tools["read_chat"], fn=broken)}
    r = Runner(cfg, Toolbox(tools, base.inventory, base.context, base.describe), "row1", script=script, decider=approve)
    result = r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    answers = {m.tool_call_id: m for m in state["messages"] if isinstance(m, ToolMessage)}
    assert set(answers) >= bad                                                     # every call id is answered
    assert answers["call_91_search_logs"].content.startswith("Invalid arguments for search_logs: limit:") and answers["call_91_search_logs"].status == "error"
    assert answers["call_92_search_logs"].content.startswith("Could not parse the arguments for search_logs:")
    assert "rationale: an object with `tests` and `expect` is required" in answers["call_93_search_logs"].content
    broken_answer = answers["call_94_read_chat"].content
    assert broken_answer.startswith("read_chat failed inside the tool (ZeroDivisionError).") and "secret" not in broken_answer and "Traceback" not in broken_answer
    assert result["status"] == "completed" and state["validation"]["ok"]           # the run went on to a report
    first_round = [e for e in state["ledger"] if e["round"] == 1]
    assert [e["status"] for e in first_round] == ["error"] * 4 and state["calls_used"] == 13       # a call that ran and failed is charged; the others are not


def test_row1_a_malformed_call_is_answered_before_the_gate_and_shown_only_on_the_third_round_in_a_row(cfg):
    bad = {f"call_{80 + n}_search_logs" for n in range(3)}

    def script(messages, tools):
        n = turns(messages)
        if n < 3:       # a read without its rationale, three model turns running
            return "a read without its rationale", [call("search_logs", 80 + n, since="-1h")]
        mine = [m for m in messages if isinstance(m, AIMessage)][:3]
        rest = [m for m in messages if all(m is not x for x in mine) and not (isinstance(m, ToolMessage) and m.tool_call_id in bad)]
        return fixture_script.turn(rest, tools)

    asked: list[str] = []

    def decider(cards):
        asked.extend(c["call_id"] for c in cards)
        return approve(cards)

    attended = replace(cfg, auto_approve=False)         # every read asks, so a card for a malformed call would show here
    clean = runner(attended, "clean", decider=decider)
    clean.start(fx.TRIGGER, fx.NOW)
    used_clean = clean.state()["calls_used"]
    clean.close()
    asked.clear()

    r = runner(attended, "malformed", script=script, decider=decider)
    result = r.start(fx.TRIGGER, fx.NOW)
    state, events = r.state(), events_of(r)
    r.close()
    assert result["status"] == "completed"
    assert not bad & set(asked)                                                     # no person was asked about one
    assert state["calls_used"] == used_clean                                        # and none was charged
    answers = {m.tool_call_id: m for m in state["messages"] if isinstance(m, ToolMessage)}
    for call_id in bad:                                                             # the model is told what to fix, word for word
        assert answers[call_id].content.startswith(       # the round's board line may follow on the last message
            "Invalid arguments for search_logs: rationale: an object with `tests` and `expect` is required. Fix them and call again.")
        assert answers[call_id].status == "error"
    hidden = {e.data["call_id"]: e.data.get("hidden", False) for e in events if e.type in ("tool_call_proposed", "gate_decision", "tool_result")
              and e.data.get("call_id") in bad}
    assert hidden == {"call_80_search_logs": True, "call_81_search_logs": True, "call_82_search_logs": False}     # the third in a row is shown
    assert [e.get("hidden", False) for e in state["ledger"] if e["call_id"] in bad] == [True, True, False]
    assert state["malformed_streak"] == 0                                           # a clean turn starts the count again


# -- axis 8, row 2: provider 429, 5xx, timeout, refusal, max_tokens --------------------------------------------


class ProviderDown:
    """A script that fails like a provider while a marker file exists."""

    def __init__(self, marker: Path, at_turn: int, error: type[BaseException] = TimeoutError, times: int = 99):
        self.marker, self.at_turn, self.error, self.times, self.raised = marker, at_turn, error, times, 0
        marker.write_text("down")

    def __call__(self, messages, tools):
        if turns(messages) == self.at_turn and self.marker.exists() and self.raised < self.times:
            self.raised += 1
            raise self.error("the provider did not answer in time")
        return fixture_script.turn(messages, tools)


def test_row2_one_timeout_is_retried_inside_the_node_and_the_run_completes(cfg, tmp_path):
    script = ProviderDown(tmp_path / "down", at_turn=2, times=1)
    r = runner(cfg, "retry", script)
    result = r.start(fx.TRIGGER, fx.NOW)
    r.close()
    assert result["status"] == "completed" and script.raised == 1
    usage = json.loads((r.dir / "usage.json").read_text())["calls"]
    assert [c["attempt"] for c in usage] == [1, 1, 2, 1, 1]                        # the third call succeeded on its second attempt
    assert json.loads((r.dir / "summary.json").read_text())["retries"] == 1
    assert "error" not in {e.type for e in events_of(r)}


def test_row2_an_injected_provider_timeout_leaves_a_full_record_and_a_resumable_checkpoint(cfg, tmp_path):
    marker = tmp_path / "down"
    r1 = runner(cfg, "timeout", ProviderDown(marker, at_turn=2))
    result = r1.start(fx.TRIGGER, fx.NOW)
    r1.close()
    assert result["status"] == "paused" and result["error"].startswith("TimeoutError:")
    assert r1.record.missing() == []                                               # every file of the record, not the thinnest one
    types = [e.type for e in events_of(r1)]
    assert types[-1] == "run_paused" and "error" in types and "run_finished" not in types
    error = next(e for e in events_of(r1) if e.type == "error")
    assert (error.data["where"], error.data["kind"], error.data["next"]) == ("model", "TimeoutError", "resume")
    assert error.data["message"] == "TimeoutError: the provider did not answer in time"
    paused = events_of(r1)[-1].data
    assert paused["resume"] == resume_hint("timeout") and "the model failed (TimeoutError)" in paused["reason"]
    report = json.loads((r1.dir / "report.json").read_text())
    Report.model_validate(report)                                                  # the validated partial
    assert report["summary"].startswith("Incomplete (the model failed (TimeoutError)).") and [h["id"] for h in report["hypotheses"]] == ["h-full", "h-dying"]
    summary = json.loads((r1.dir / "summary.json").read_text())
    assert summary["status"] == "paused" and summary["model_calls"] == 2 and len((r1.dir / "calls.jsonl").read_text().splitlines()) > 5
    assert RunsIndex(cfg.runs_dir).rows("status = 'paused'")[0]["run_id"] == "timeout"      # "what is stuck" is a query

    marker.unlink()                                                                # the provider is back
    r2 = runner(cfg, "timeout", ProviderDown(tmp_path / "other", at_turn=99))
    done = r2.resume()
    state = r2.state()
    r2.close()
    assert done["status"] == "completed" and len(state["usage"]) == 5 and state["report"]["validation"]["ok"]
    events = events_of(r2)
    assert [e.seq for e in events] == list(range(1, len(events) + 1)) and [e.type for e in events].count("run_finished") == 1
    assert RunsIndex(cfg.runs_dir).rows()[0]["status"] == "completed"


def test_row2_the_retry_predicate_retries_what_can_pass_and_nothing_else():
    class Status(Exception):
        def __init__(self, code):
            self.status_code = code

    assert all(retryable(e) for e in (TimeoutError(), ConnectionError(), Status(429), Status(500), Status(503), Status(529)))
    assert not any(retryable(e) for e in (Status(400), Status(401), Status(403), Status(404), ValueError("bad"), KeyError("x")))
    named = type("RateLimitError", (Exception,), {})
    assert retryable(named()) and retryable(type("APITimeoutError", (Exception,), {})())


def test_row2_a_refusal_is_asked_once_more_of_the_fallback_profile(tmp_path):
    cfg = replace(load_config(runs_dir=tmp_path), fallback_profile="anthropic")
    cfg = replace(cfg, profiles={**cfg.profiles, "anthropic": replace(cfg.profiles["scripted"], profile="anthropic")})

    def declines_once(messages, tools):
        if turns(messages) == 1:
            return "", [], {"stop_reason": "refusal"}
        return fixture_script.turn(messages, tools)

    def fallback(messages, tools):
        thinking, calls = fixture_script.turn(messages, tools)
        return thinking, calls, {"model_name": "fallback-model"}

    r = runner(cfg, "refusal", declines_once, fallback_script=fallback)
    result = r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    assert result["status"] == "completed" and state["fallback_used"] is True
    nodes = [(u["node"], u["stop_reason"], u["model"]) for u in state["usage"]]
    assert nodes[1] == ("agent", "refusal", "scripted") and nodes[2] == ("fallback", "tool_use", "fallback-model") and len(nodes) == 6
    assert [u["node"] for u in state["usage"]].count("fallback") == 1              # once per run


def test_row2_a_refusal_with_no_fallback_and_a_cut_off_turn_end_the_run_incomplete(cfg):
    for reason, words in (("refusal", "the model declined to continue"), ("max_tokens", "the model ran out of output tokens")):
        def script(messages, tools, reason=reason):
            return ("", [], {"stop_reason": reason}) if turns(messages) == 1 else fixture_script.turn(messages, tools)

        r = runner(cfg, f"stop-{reason}", script)
        result = r.start(fx.TRIGGER, fx.NOW)
        state = r.state()
        r.close()
        assert result["status"] == "incomplete" and words in state["stop_reason"] and words in state["report"]["summary"]
        assert r.record.missing() == [] and events_of(r)[-1].type == "run_finished"


def test_row2_the_request_carries_the_providers_own_fallback(monkeypatch, tmp_path):
    from smalls.agent.models import make_model

    monkeypatch.setenv("SMALLS_LIVE", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-not-used")
    cfg = load_config(runs_dir=tmp_path, profile="anthropic")
    model, _ = make_model(cfg.investigator, timeout_s=cfg.timeout_s)
    payload = model._get_request_payload([("user", "hi")])
    assert payload["fallbacks"] == "default" and payload["betas"] == ["server-side-fallback-2026-07-01"]


# -- axis 8, row 3: a repeated identical call ----------------------------------------------------------------


def test_row3_a_repeated_identical_call_returns_a_pointer_and_costs_nothing(cfg):
    def script(messages, tools):
        k = turns(messages)
        if k == 0:
            return "search", [call("search_logs", 1, ("h", "errors"), terms=["ERROR"], since="-60m"),
                              call("search_logs", 2, ("h", "bad"), terms=["ERROR"], since="-60m", limit=999),
                              call("list_alerts", 0, ("h", "what is alerting"), since="-2h")]
        if k == 1:
            return "again, and the same thing three other ways", [
                call("search_logs", 3, ("h", "the same, said differently"), terms=["ERROR"], since="-1h", until="now", limit=30),
                call("search_logs", 4, ("h", "another window"), terms=["ERROR"], since="-59m"),
                call("search_logs", 5, ("h", "the bad one again"), terms=["ERROR"], since="-60m", limit=999),
                call("list_alerts", 6, ("h", "what the first turn already listed"), since="-2h")]
        return "stop", []

    r = runner(cfg, "repeat", script)
    r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    by_id = {e["call_id"]: e for e in state["ledger"]}
    repeat = by_id["call_03_search_logs"]
    assert (repeat["status"], repeat["repeat_of"], repeat["shown"]) == ("repeat", "call_01_search_logs", [])
    answer = next(m.content for m in state["messages"] if isinstance(m, ToolMessage) and m.tool_call_id == "call_03_search_logs")
    assert answer.startswith("Already run in round 1 with the same arguments (call call_01_search_logs)") and "nothing was charged" in answer
    assert by_id["call_04_search_logs"]["status"] == "ok" and by_id["call_04_search_logs"]["repeat_of"] is None      # a different window is a different call
    assert by_id["call_05_search_logs"]["status"] == "error" and by_id["call_05_search_logs"]["repeat_of"] is None  # only a call that ran makes a repeat
    assert (by_id["call_06_list_alerts"]["status"], by_id["call_06_list_alerts"]["repeat_of"]) == ("repeat", "call_00_list_alerts")
    assert state["calls_used"] == 3                                               # the first search, the listing and the different window
    calls = {c["call_id"]: c for c in map(json.loads, (r.dir / "calls.jsonl").read_text().splitlines())}
    assert calls["call_03_search_logs"]["repeat_of"] == "call_01_search_logs"
    evidence = {json.loads(line)["call_id"] for line in (r.dir / "evidence.jsonl").read_text().splitlines()}
    assert "call_03_search_logs" not in evidence


# -- axis 8, row 4: stuck ------------------------------------------------------------------------------------


def test_row4_three_empty_reads_get_a_hint_from_the_inventory_and_nothing_is_refused(cfg):
    def script(messages, tools):
        k = turns(messages)
        if k < 3:
            return "nothing yet", [call("search_logs", k + 1, ("h", "x"), terms=[f"zz-no-such-{k}"], since="-2h")]
        if k == 3:
            return "one that finds something", [call("update_board", 8, hypotheses=[]), call("search_logs", 9, ("h", "x"), terms=["ERROR"], since="-2h")]
        return "stop", []

    r = runner(cfg, "stuck", script)
    r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    results = [m.content for m in state["messages"] if isinstance(m, ToolMessage)]
    assert ["[stuck:" in text for text in results] == [False, False, True, False, False]       # a read that finds something ends it
    assert "[stuck: the last 3 reads returned nothing. What exists, from the inventory: Logs: sourcetype app; hosts worker-01, worker-02;" in results[2]
    assert results[2].splitlines()[-1].startswith("[board:")                       # the status line is still the last line
    stuck = [e for e in events_of(r) if e.type == "stuck"]
    assert [e.data["empty_reads"] for e in stuck] == [3]
    assert all(g["decision"] == "approved" for g in state["gate_log"] if g["tool"] == "search_logs")      # nothing refused


# -- axis 8, row 5: call budget, turn cap, recursion limit ---------------------------------------------------


def test_row5_the_recursion_limit_renders_a_partial_report_from_state(cfg):
    r = runner(cfg, "recursion")
    r.config["recursion_limit"] = 9
    result = r.start(fx.TRIGGER, fx.NOW)
    r.close()
    assert result["status"] == "incomplete" and "the recursion limit was reached" in result["report"]["summary"]
    Report.model_validate(result["report"])
    types = [e.type for e in events_of(r)]
    assert "error" in types and types[-1] == "run_finished" and r.record.missing() == []


# -- axis 8, row 7: any terminal failure ---------------------------------------------------------------------


def test_row7_a_fault_in_our_own_code_still_leaves_a_full_record(cfg):
    base = module_toolbox("fixture_registry")

    def broken_inventory(ctx) -> Envelope:
        raise RuntimeError("the inventory could not be built")

    r = Runner(cfg, Toolbox(base.read_tools, broken_inventory, base.context, base.describe), "fault", script=fixture_script.turn)
    result = r.start(fx.TRIGGER, fx.NOW)
    r.close()
    assert result["status"] in ("paused", "failed") and result["error"].startswith("RuntimeError")
    assert r.record.missing() == []
    report = json.loads((r.dir / "report.json").read_text())
    Report.model_validate(report)                                                  # a partial passes the same contract
    assert report["hypotheses"] == [] and "the run failed (RuntimeError)" in report["summary"]
    error = next(e for e in events_of(r) if e.type == "error")
    assert error.data["where"] == "run"
    assert events_of(r)[-1].type in ("run_paused", "run_finished")


def test_an_interrupt_leaves_a_full_record_and_a_resumable_run(cfg, tmp_path):
    r1 = runner(cfg, "ctrl-c", ProviderDown(tmp_path / "down", at_turn=3, error=KeyboardInterrupt))
    result = r1.start(fx.TRIGGER, fx.NOW)
    r1.close()
    assert result["status"] == "paused" and r1.record.missing() == []
    error = next(e for e in events_of(r1) if e.type == "error")
    assert error.data["where"] == "interrupt" and events_of(r1)[-1].data["reason"] == "the run was interrupted"
    (tmp_path / "down").unlink()
    r2 = runner(cfg, "ctrl-c")
    assert r2.resume()["status"] == "completed"
    r2.close()


def test_a_real_interrupt_signal_while_a_node_runs_is_still_a_resumable_pause(cfg):
    """Ctrl-C reaches the main thread while the node runs on a worker, which then finishes its turn. Nothing
    is "next" any more, but the finished turn is waiting to be applied, so the run can go on from there."""
    import os
    import signal
    import threading
    import time

    def slow(messages, tools):
        time.sleep(0.5)
        return fixture_script.turn(messages, tools)

    r1 = runner(cfg, "sigint", slow, eval_run=True, decider=None)
    timer = threading.Timer(0.8, lambda: os.kill(os.getpid(), signal.SIGINT))
    timer.start()
    result = r1.start(fx.TRIGGER, fx.NOW)
    timer.cancel()
    r1.close()
    assert result["status"] == "paused" and r1.record.missing() == []
    assert events_of(r1)[-1].type == "run_paused" and events_of(r1)[-1].data["reason"] == "the run was interrupted"
    time.sleep(0.6)                                                                 # let the interrupted turn finish on its worker
    r2 = runner(cfg, "sigint", eval_run=True, decider=None)
    done = r2.resume()
    state = r2.state()
    r2.close()
    assert done["status"] == "completed" and len(state["usage"]) == 5 and [e.type for e in events_of(r2)].count("run_finished") == 1


# -- the event log -------------------------------------------------------------------------------------------


def test_the_reader_skips_a_torn_last_line(tmp_path):
    log = tmp_path / "events.jsonl"
    sink = JsonlSink(log)
    bus = Bus("r", [sink])
    bus.emit("phase", name="one")
    bus.emit("phase", name="two")
    sink.close()
    with log.open("a") as f:
        f.write('{"type": "tool_result", "data": {"call_id": "c1", "sta')           # what a crash leaves behind
    events = read_events(log)
    assert [e.data["name"] for e in events] == ["one", "two"] and last_seq(log) == 2
    assert last_seq(tmp_path / "missing.jsonl") == 0


def test_a_sink_that_fails_is_dropped_and_the_run_goes_on(cfg):
    class Broken:
        calls = 0

        def emit(self, ev):
            Broken.calls += 1
            raise OSError("the terminal went away")

    r = runner(cfg, "sink", sinks=[Broken()])
    result = r.start(fx.TRIGGER, fx.NOW)
    r.close()
    assert result["status"] == "completed" and Broken.calls == 1                   # dropped at its first failure
    summary = json.loads((r.dir / "summary.json").read_text())
    assert summary["dropped_sinks"] == ["Broken: OSError: the terminal went away"]
    events = events_of(r)
    assert events[-1].type == "run_finished" and [e.seq for e in events] == list(range(1, len(events) + 1))      # one writer numbers them


def test_reasoning_deltas_are_coalesced_and_the_whole_reasoning_is_written_once_per_call(cfg):
    r = runner(cfg, "thinking")
    r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    events = events_of(r)
    whole = [e.data["text"] for e in events if e.type == "thinking"]
    deltas = [e.data["text"] for e in events if e.type == "thinking_delta"]
    said = [m.content[0]["thinking"] for m in state["messages"] if isinstance(m, AIMessage)]
    assert whole == said and len(whole) == 5                                       # one full event per model call
    words = sum(len(text.split()) for text in said)
    assert "".join(deltas) == "".join(said) and len(deltas) <= 10 < words         # not one event per token


def test_a_low_cache_read_ratio_raises_an_event_from_the_second_call(cfg):
    r = runner(cfg, "cache")
    r.cached = True                                                                # as a profile with caching on
    r.start(fx.TRIGGER, fx.NOW)
    r.close()
    low = [e.data for e in events_of(r) if e.type == "cache_ratio_low"]
    assert [d["round"] for d in low] == [2, 3, 4, 5] and all(d["ratio"] == 0.0 and d["floor"] == cfg.cache_ratio_floor for d in low)
    quiet = runner(cfg, "nocache")
    quiet.start(fx.TRIGGER, fx.NOW)
    quiet.close()
    assert "cache_ratio_low" not in {e.type for e in events_of(quiet)}             # a profile that does not cache has nothing to fall


# -- run ids, the index, notes, keys -------------------------------------------------------------------------


def test_run_ids_are_minted_here_and_never_used_as_a_path(cfg):
    minted = new_run_id()
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{6}", minted) and check_run_id(minted) == minted and new_run_id() != minted
    for bad in ("../etc", "a/b", "", ".hidden", "x" * 90, "run id"):
        with pytest.raises(SystemExit, match="not a run id"):
            check_run_id(bad)
    r = Runner(cfg, module_toolbox("fixture_registry"), script=fixture_script.turn)
    assert re.fullmatch(r"\d{8}T\d{6}Z-[0-9a-f]{6}", r.run_id) and r.dir.parent == cfg.runs_dir
    r.close()


def test_the_runs_index_has_a_row_per_run_with_its_status_and_cost(cfg):
    done = runner(cfg, "idx-done")
    done.start(fx.TRIGGER, fx.NOW, checkpoint="t2")
    done.close()
    paused = runner(cfg, "idx-paused", stop_at_gate=True, attended=True, decider=None)
    paused.start(fx.TRIGGER, fx.NOW)
    paused.close()
    rows = {row["run_id"]: row for row in RunsIndex(cfg.runs_dir).rows()}
    assert (rows["idx-done"]["status"], rows["idx-done"]["checkpoint"], rows["idx-done"]["profile"]) == ("completed", "t2", "scripted")
    assert rows["idx-paused"]["status"] == "paused" and rows["idx-done"]["prompt_sha"] and rows["idx-done"]["cost_usd"] == 0.0
    assert [row["run_id"] for row in RunsIndex(cfg.runs_dir).rows("status != 'completed'")] == ["idx-paused"]
    assert (cfg.runs_dir / "index.sqlite").exists() and not (done.dir / "index.sqlite").exists()


def test_a_note_given_on_resume_reaches_the_model_as_a_citable_row(cfg):
    def cites_the_note(messages, tools):
        thinking, calls = fixture_script.turn(messages, tools)
        notes = fixture_script.find(messages, "incident commander")
        for c in calls:
            if c["name"] == "submit_report" and notes:
                c["args"]["hypotheses"][0]["support"] = [*c["args"]["hypotheses"][0]["support"], notes[0]]
        return thinking, calls

    r1 = runner(cfg, "note", cites_the_note, stop_at_gate=True, attended=True, decider=None)
    r1.start(fx.TRIGGER, fx.NOW)
    r1.close()
    r2 = runner(cfg, "note", cites_the_note, attended=True)
    result = r2.resume(note="the same job failed last month after a release too")
    state = r2.state()
    r2.close()
    [note] = state["ic_notes"]
    assert re.fullmatch(r"ic:note/[0-9a-f]{8}", note["handle"]) and note["call_id"] is None and state["notes_shown"] == 1
    carrier = next(m.content for m in state["messages"] if isinstance(m, ToolMessage) and note["handle"] in m.content)
    assert f"{note['handle']}  incident commander, " in carrier and "the same job failed last month" in carrier
    assert result["status"] == "completed" and state["validation"]["ok"]            # a note minted in this run may be cited
    assert note["handle"] in next(h for h in state["report"]["hypotheses"] if h["id"] == "h-release")["support"]
    assert json.loads((r2.dir / "notes.jsonl").read_text().splitlines()[0])["handle"] == note["handle"]
    assert [e.data["handle"] for e in events_of(r2) if e.type == "ic_note"] == [note["handle"]]


def test_a_note_added_at_a_gate_card_rides_with_the_decisions(cfg):
    def decide(cards):
        return {**approve(cards), gate.NOTE_KEY: "check the disk on the other worker as well"}

    r = runner(cfg, "card-note", decider=decide, attended=True)
    r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    [note] = state["ic_notes"]
    assert note["text"].startswith("check the disk") and any(note["handle"] in str(m.content) for m in state["messages"] if isinstance(m, ToolMessage))
    assert all(g["decision"] == "approved" for g in state["gate_log"] if g["tool"] == "propose_action")


def test_the_cli_resumes_with_a_note(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SMALLS_RUNS_DIR", str(tmp_path))
    common = ["--registry", "fixture_registry", "--script", "fixture_script:turn", "--profile", "scripted"]
    assert cli.main(["run", "--now", "2026-03-02T02:20:00Z", "--trigger", fx.TRIGGER, "--run-id", "cli-note", "--stop-at-gate", *common]) == 0
    assert cli.main(["resume", "cli-note", "--approve", "--note", "look at worker-02 too"]) == 0
    notes = [json.loads(line) for line in (tmp_path / "cli-note" / "notes.jsonl").read_text().splitlines()]
    assert [n["text"] for n in notes] == ["look at worker-02 too"] and "note ic:note/" in capsys.readouterr().out


def test_no_file_in_a_run_directory_contains_a_key_value(cfg, tmp_path, monkeypatch):
    secret = "sk-test-0123456789abcdef-not-a-real-key"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setenv("FIXTURE_SECRET_TOKEN", "tok-9876543210-fixture")

    class Leaky(ProviderDown):
        def __call__(self, messages, tools):
            if turns(messages) == 2 and self.marker.exists():
                raise ConnectionError(f"401 from provider, header x-api-key: {secret}")     # an error that quotes the key
            return fixture_script.turn(messages, tools)

    marker = tmp_path / "down"
    failed = runner(cfg, "keys-failed", Leaky(marker, at_turn=2))
    assert failed.start(fx.TRIGGER, fx.NOW)["status"] == "paused"
    failed.close()
    marker.unlink()
    done = runner(cfg, "keys-done")
    done.start(fx.TRIGGER, fx.NOW)
    done.close()
    for run in (failed, done):
        assert sorted(p.name for p in run.dir.iterdir() if p.name != "checkpoints.sqlite") == sorted(FILES)
        for path in run.dir.iterdir():
            data = path.read_bytes()
            assert secret.encode() not in data and b"tok-9876543210-fixture" not in data, path.name
        assert {"ANTHROPIC_API_KEY", "FIXTURE_SECRET_TOKEN"} <= set(json.loads((run.dir / "run.json").read_text())["key_variables"])
    error = next(e for e in events_of(failed) if e.type == "error")
    assert "<ANTHROPIC_API_KEY>" in error.data["message"] and scrub(f"x {secret} y") == "x <ANTHROPIC_API_KEY> y"
    assert secret.encode() not in (cfg.runs_dir / "index.sqlite").read_bytes()
