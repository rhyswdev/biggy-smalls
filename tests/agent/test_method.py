"""The method in code (build row C1): a unit test per cap and per status-line flag, the opening sweep, the
validator's structure checks and its alert verdicts, the provider schema adapter, and the verifier's
verdict and packets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

import fixture_registry as fx
import fixture_script
from smalls.agent import board, gate, internal_tools, validator, verifier
from smalls.agent.config import load_config
from smalls.agent.internal_tools import HypothesisFields, HypothesisUpdate
from smalls.agent.models import provider_tools
from smalls.agent.models import scripted_call as call
from smalls.agent.runner import Runner, Toolbox, module_toolbox
from smalls.agent.state import merge_hypotheses
from smalls.tools import registry
from smalls.tools.registry import ToolContext

LOG, LOG2, CHAT, NOTE = "log:worker-01/0a1b2c3d", "log:worker-01/1b2c3d4e", "slack:ops/0a1b2c3d", "ic:note/0a1b2c3d"
NEW = {"statement": "s", "role": "cause", "status": "open", "subject": "x", "confidence": 0.3, "source": "derived"}


def upd(hid: str, **fields) -> HypothesisUpdate:
    return HypothesisUpdate(id=hid, **fields)


def apply(current: dict, *updates, round: int = 1, **kwargs):
    merged, problems, notes = board.apply_update(current, list(updates), round=round, call_id=f"c{round}", **kwargs)
    return merge_hypotheses(current, merged), problems, notes


@pytest.fixture
def cfg(tmp_path: Path):
    return load_config(runs_dir=tmp_path)


def runner(cfg, run_id: str, script, **kwargs) -> Runner:
    kwargs.setdefault("decider", lambda cards: {c["call_id"]: {"approve": True} for c in cards})
    return Runner(cfg, module_toolbox("fixture_registry"), run_id, script=script, **kwargs)


# -- caps --------------------------------------------------------------------------------------------------


def test_cap_confidence_is_clamped_to_a_half_when_nothing_but_chat_supports_it():
    b, problems, notes = apply({}, upd("h", **{**NEW, "confidence": 0.8, "support": [CHAT, NOTE]}))
    assert not problems and b["h"]["confidence"] == 0.5 and notes == ["h: confidence 0.80 clamped to 0.50, because nothing but chat supports it"]
    b, _, notes = apply({}, upd("h", **{**NEW, "confidence": 0.8}))               # no support at all is no better than chat
    assert b["h"]["confidence"] == 0.5 and notes
    b, _, notes = apply({}, upd("h", **{**NEW, "confidence": 0.8, "support": [CHAT, LOG]}))
    assert b["h"]["confidence"] == 0.8 and not notes
    b, _, notes = apply(b, upd("h", support=[CHAT]), round=2)                      # the cap follows the support, not the update
    assert b["h"]["confidence"] == 0.5 and notes and b["h"]["history"][-1]["to"]["confidence"] == 0.5
    b, _, notes = apply({}, upd("h", **{**NEW, "confidence": 0.5, "support": [CHAT]}))
    assert b["h"]["confidence"] == 0.5 and not notes                              # at the cap is not above it


def test_cap_a_second_leading_hypothesis_is_refused():
    b, _, _ = apply({}, upd("a", **{**NEW, "status": "leading", "support": [LOG]}))
    after, problems, _ = apply(b, upd("b", **{**NEW, "status": "leading", "support": [LOG2]}), round=2)
    assert after == b and problems and "only one hypothesis can be leading" in problems[0] and "a, b" in problems[0]
    after, problems, _ = apply(b, upd("b", **{**NEW, "status": "leading", "support": [LOG2]}), upd("a", status="open"), round=2)
    assert not problems and after["b"]["status"] == "leading" and after["a"]["status"] == "open"   # demoted in the same call


def test_a_new_hypothesis_needs_its_fields_and_source_cannot_change():
    _, problems, _ = apply({}, upd("h", statement="s", status="open"))
    assert problems == ["h: a new hypothesis needs role, subject, confidence, source"]
    b, _, _ = apply({}, upd("h", **NEW))
    after, problems, _ = apply(b, upd("h", source="stated"), round=2)
    assert after == b and problems == ["h: source is set when a hypothesis is created and cannot change (it is derived)"]
    _, problems, _ = apply(b, HypothesisFields(id="ghost", confidence=0.4), round=2, allow_new=False)
    assert problems == ["ghost: the board holds no hypothesis with this id; create it with update_board first"]


def test_an_update_carries_only_what_it_sets_and_a_list_that_is_set_replaces_the_list():
    b, _, _ = apply({}, upd("h", **{**NEW, "support": [LOG, LOG2], "entities": ["x", "y"]}))
    b, _, _ = apply(b, upd("h", confidence=0.4, because=[LOG]), round=2)
    assert b["h"]["support"] == [LOG, LOG2] and b["h"]["entities"] == ["x", "y"] and b["h"]["confidence"] == 0.4
    assert b["h"]["history"][-1] == {"round": 2, "call_id": "c2", "from": {"status": "open", "confidence": 0.3},
                                     "to": {"status": "open", "confidence": 0.4}, "because": [LOG]}
    b, _, _ = apply(b, upd("h", support=[LOG2]), round=3)
    assert b["h"]["support"] == [LOG2]
    same, _, _ = apply(b, upd("h", statement="reworded"), round=4)
    assert len(same["h"]["history"]) == len(b["h"]["history"])                    # a history row only when belief moved


# -- ready (J2) and the status line (J14) ------------------------------------------------------------------


def ready_board() -> dict:
    b, _, _ = apply({}, upd("lead", **{**NEW, "status": "leading", "support": [LOG], "discriminating": [LOG2]}),
                    upd("trig", **{**NEW, "role": "trigger", "support": [LOG]}),
                    upd("rival", **{**NEW, "status": "rejected", "against": [LOG]}))
    return b


def test_ready_needs_each_part_of_j2():
    b = ready_board()
    assert board.missing_for_ready(b) == []
    assert board.missing_for_ready({}) == ["no leading hypothesis"]
    assert board.missing_for_ready({"x": {**b["trig"], "id": "x"}}) == ["no leading hypothesis"]
    chat_only = merge_hypotheses(b, {"lead": {"support": [CHAT, NOTE]}})
    assert board.missing_for_ready(chat_only) == ["leading lead has no non-chat support"]
    undiscriminated = merge_hypotheses(b, {"lead": {"discriminating": [CHAT]}})
    assert board.missing_for_ready(undiscriminated) == ["leading lead has no discriminating evidence"]
    loose_trigger = merge_hypotheses(b, {"trig": {"support": [CHAT]}})
    assert board.missing_for_ready(loose_trigger) == ["trig (trigger) has no non-chat support"]
    contributing = merge_hypotheses(b, {"trig": {"role": "contributing", "support": []}})
    assert board.missing_for_ready(contributing) == ["trig (contributing) has no non-chat support"]
    open_rival = merge_hypotheses(b, {"rival": {"status": "open", "against": []}})
    assert board.missing_for_ready(open_rival) == ["rival rival has no against"]
    symptom = merge_hypotheses(b, {"s": {**NEW, "id": "s", "role": "symptom", "support": []}})
    assert board.missing_for_ready(symptom) == []                                 # a symptom owes nothing
    assert board.missing_for_ready(b, carried=frozenset({LOG})) == ["leading lead has no non-chat support", "trig (trigger) has no non-chat support"]


def test_status_line_flag_ready():
    assert board.status_line(ready_board(), round=1, used=4, limit=30, turns_left=33) == "[board: ready; budget 4/30, 33 turns left]"


def test_status_line_flag_not_ready_names_what_is_missing():
    assert board.status_line({}, round=0, used=0, limit=30, turns_left=36) == "[board: not ready: no leading hypothesis; budget 0/30, 36 turns left]"
    b = merge_hypotheses(ready_board(), {"lead": {"support": [CHAT], "discriminating": []}, "rival": {"status": "open", "against": []}})
    assert board.status_line(b, round=1, used=1, limit=30, turns_left=35) == \
        "[board: not ready: leading lead has no non-chat support, leading lead has no discriminating evidence, rival rival has no against; budget 1/30, 35 turns left]"


def test_status_line_flag_stale_hypotheses():
    b = ready_board()                                                             # every row written in round 1
    assert "stale" not in board.status_line(b, round=2, used=0, limit=30, turns_left=34)
    line = board.status_line(b, round=3, used=0, limit=30, turns_left=33)
    assert line == "[board: ready; stale: lead, trig; budget 0/30, 33 turns left]"   # a rejected hypothesis is never stale
    b, _, _ = apply(b, upd("lead", confidence=0.4), round=3)
    assert "stale: trig;" in board.status_line(b, round=3, used=0, limit=30, turns_left=33)


def test_status_line_flag_budget_and_turns():
    assert board.status_line(ready_board(), round=1, used=30, limit=30, turns_left=0).endswith("; budget 30/30, 0 turns left]")
    assert board.status_line(ready_board(), round=1, used=3, limit=35, turns_left=12).endswith("; budget 3/35, 12 turns left]")


def test_a_stale_board_makes_the_gate_refuse_reads_and_only_a_board_write_lifts_it(cfg):
    def reader(messages, tools):
        k = sum(isinstance(m, AIMessage) for m in messages)
        if k < 5:
            return "read on", [call("search_logs", k + 1, ("h", "more"), since="-2h", terms=[f"t{k}"])]
        if k == 5:
            return "record", [call("update_board", 9, hypotheses=[{"id": "h", **NEW}]), call("search_logs", 10, ("h", "x"), since="-2h", terms=["disk"])]
        return "stop", []

    r = runner(cfg, "stale", reader)
    r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    decisions = [g for g in state["gate_log"] if g["tool"] == "search_logs"]
    assert [g["decision"] for g in decisions] == ["approved", "approved", "approved", "rejected", "rejected", "approved"]
    refused = decisions[3]
    assert refused["by"]["who"] == "policy" and "the board has not changed in 3 rounds" in refused["reason"] and "submit now" in refused["reason"]
    assert state["calls_used"] == 4                                               # a refusal is not charged


# -- the opening sweep (J12) -------------------------------------------------------------------------------


def test_the_sweep_is_harness_calls_through_the_same_gate_and_one_user_message(cfg):
    r = runner(cfg, "sweep", fixture_script.turn)
    r.start(fx.TRIGGER, fx.NOW, channel="ops")
    state = r.state()
    r.close()
    first, second, third = state["messages"][:3]
    assert isinstance(first, HumanMessage) and isinstance(second, HumanMessage) and isinstance(third, AIMessage)   # no assistant turn is fabricated
    assert second.content.startswith("Opening sweep, run by the harness before your first turn.")
    blocks = second.content.split("\n\n== ")[1:]
    assert [b.split("\n")[0] for b in blocks] == ['list_alerts {"since": "-2h"}', 'list_changes {"since": "-24h"}',
                                                  'read_chat {"since": "-2h", "channel": "ops"}']
    assert second.content.splitlines()[-1] == "[board: not ready: no leading hypothesis; budget 0/30, 36 turns left]"
    sweep = [e for e in state["ledger"] if e["by"] == "harness" and e["tool"] != "inventory"]
    assert [(e["tool"], e["round"], e["status"]) for e in sweep] == [("list_alerts", 0, "ok"), ("list_changes", 0, "ok"), ("read_chat", 0, "ok")]
    assert all(row in second.content for e in sweep for row in e["shown"])       # the rows as printed
    assert state["swept"] == sweep[0]["shown"] and len(state["swept"]) == 3
    assert all(not {"signal", "act", "change_kind"} & set(e["args"]) for e in sweep)       # the sweep never uses a tag filter
    decisions = {g["call_id"]: g for g in state["gate_log"]}
    assert all(decisions[e["call_id"]]["by"] == {"who": "auto", "via": "headless"} for e in sweep)
    assert state["calls_used"] == 9                                               # the sweep is not charged to the budget
    calls = [json.loads(line) for line in (r.dir / "calls.jsonl").read_text().splitlines()]
    assert [c["by"] for c in calls[:4]] == ["harness"] * 4 and calls[1]["rationale"]["tests"] == "the opening sweep"
    evidence = {json.loads(line)["call_id"] for line in (r.dir / "evidence.jsonl").read_text().splitlines()}
    assert {e["call_id"] for e in sweep} <= evidence


def test_in_interactive_mode_the_sweep_is_one_card(tmp_path):
    cfg = load_config(runs_dir=tmp_path, interactive=True)
    cards_seen: list[list[dict]] = []

    def decide(cards):
        cards_seen.append(cards)
        return {c["call_id"]: {"approve": True} for c in cards}

    r = runner(cfg, "card", fixture_script.turn, decider=decide, attended=True)
    r.start(fx.TRIGGER, fx.NOW)
    r.close()
    assert [c["tool"] for c in cards_seen[0]] == ["list_alerts", "list_changes", "read_chat"] and {c["by"] for c in cards_seen[0]} == {"harness"}
    assert all(c["by"] == "agent" for cards in cards_seen[1:] for c in cards)


def test_a_rejected_sweep_reaches_the_model_as_a_rejection_and_nothing_is_swept(tmp_path):
    cfg = load_config(runs_dir=tmp_path, interactive=True)

    def decide(cards):
        harness = all(c["by"] == "harness" for c in cards)
        return {c["call_id"]: ({"approve": False, "reason": "not yet"} if harness else {"approve": True}) for c in cards}

    r = runner(cfg, "nosweep", lambda messages, tools: ("stop", []), decider=decide, attended=True)
    r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    assert state["messages"][1].content.count("REJECTED by human: not yet.") == 3 and state["swept"] == []
    assert all(e["status"] == "rejected" for e in state["ledger"] if e["call_id"].startswith("sweep_"))


def test_a_registry_without_the_sweep_tools_skips_the_sweep(cfg):
    base = module_toolbox("fixture_registry")
    only_logs = Toolbox({"search_logs": base.read_tools["search_logs"]}, base.inventory, base.context, base.describe)
    r = Runner(cfg, only_logs, "bare", script=lambda messages, tools: ("stop", []))
    r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    assert [type(m).__name__ for m in state["messages"]] == ["HumanMessage", "AIMessage"] and not state.get("swept")


# -- the validator -----------------------------------------------------------------------------------------


def report_from(script, cfg, run_id: str, **kwargs):
    r = runner(cfg, run_id, script, **kwargs)
    r.start(fx.TRIGGER, fx.NOW)
    state = r.state()
    r.close()
    return state


def edit_report(change):
    """The fixture script, with its report changed by `change(args, attempt)`."""
    def script(messages, tools):
        thinking, calls = fixture_script.turn(messages, tools)
        attempt = sum(1 for m in messages if isinstance(m, ToolMessage) and m.name == "submit_report")
        for c in calls:
            if c["name"] == "submit_report":
                change(c["args"], attempt)
        return thinking, calls
    return script


def test_a_report_with_a_missing_alert_verdict_gets_its_retry(cfg):
    def forget_then_fix(args, attempt):
        if attempt == 0:
            args["alerts"] = args["alerts"][:1]

    state = report_from(edit_report(forget_then_fix), cfg, "verdicts")
    refusal = next(m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "submit_report")
    missing = state["swept"][1:]
    assert refusal.status == "error" and refusal.content.startswith("Report not accepted.")
    assert f"These alert streams from the opening sweep have no verdict: {', '.join(missing)}. Give each one this_incident, other_incident or noise." in refusal.content
    assert state["report_attempts"] == 1 and state["validation"]["ok"] is True and state["validation"]["attempts"] == 2
    assert [a["handle"] for a in state["report"]["alerts"]] == state["swept"] and "unassessed" not in json.dumps(state["report"]["alerts"])


def test_after_the_retry_code_records_the_rest_as_unassessed(cfg):
    state = report_from(edit_report(lambda args, attempt: args.update(alerts=args["alerts"][:1])), cfg, "unassessed")
    alerts = state["report"]["alerts"]
    assert [a["verdict"] for a in alerts] == ["this_incident", "unassessed", "unassessed"] and [a["handle"] for a in alerts] == state["swept"]
    assert state["validation"]["ok"] is False and state["status"] == "completed"
    with pytest.raises(ValidationError):                                          # the model cannot write the word itself
        internal_tools.SubmitReportArgs.model_validate({**json.loads(json.dumps(VALID_REPORT)), "alerts": [{"handle": "dd:ev1", "verdict": "unassessed"}]})


VALID_REPORT = {
    "summary": "s", "impact": {"business_service": "b", "cis": [], "priority": {"suggested": "P3", "impact": "low", "urgency": "low"},
                               "assignment_group": "g", "oncall": "o", "customer": "c"},
    "hypotheses": [], "timeline": [], "actions": [], "do_not": [], "alerts": [], "open_questions": ["q"],
    "stakeholder_update": {"status": "s", "customer_impact": "c", "scope": "s", "changed_since_last": "c", "next_steps": "n", "next_update_at": "t"}}


def test_structure_findings_one_per_rule():
    hyp = {"id": "h", "status": "open", "support": [LOG], "against": []}
    base = {"hypotheses": [{**hyp, "status": "leading"}], "timeline": [], "actions": [], "do_not": [], "open_questions": []}
    assert validator.structure(base) == []
    assert validator.structure({**base, "hypotheses": [{**hyp, "status": "leading", "support": [CHAT, NOTE]}]}) == \
        [("h:h", "the leading hypothesis needs support that is not chat")]
    assert validator.structure({**base, "hypotheses": [{**hyp, "status": "leading"}, {"id": "r", "status": "rejected", "support": [LOG], "against": []}]}) == \
        [("h:r", "a rejected hypothesis needs the handle that contradicts it in against")]
    assert validator.structure({**base, "hypotheses": [{**hyp, "status": "leading"}, {"id": "o", "status": "open", "support": [], "against": []}]}) == [("h:o", "cites no handle")]
    assert validator.structure({**base, "timeline": [{"t_utc": "t", "event": "e", "evidence": [LOG]}, {"t_utc": "t", "event": "e", "evidence": []}]}) == [("t:2", "cites no handle")]
    assert validator.structure({**base, "actions": [{"rank": 3, "action": "a", "owner": "o", "evidence": []}]}) == [("a:3", "cites no handle")]
    assert validator.structure({**base, "do_not": [{"action": "a", "why": "w", "evidence": []}]}) == [("d:1", "cites no handle")]
    inconclusive = {**base, "hypotheses": [hyp]}
    assert validator.structure(inconclusive) == [("report", "with no leading hypothesis the report is inconclusive and needs at least one open question")]
    assert validator.structure({**inconclusive, "open_questions": ["what changed?"]}) == []      # inconclusive is a valid outcome (J6)


def test_a_claim_with_no_handle_gets_the_retry_then_is_kept_and_flagged(cfg):
    def strip_evidence(args, attempt):
        args["timeline"][0]["evidence"] = []
        args["do_not"][0]["evidence"] = []

    state = report_from(edit_report(strip_evidence), cfg, "flagged")
    refusal = next(m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "submit_report")
    assert "t:1: cites no handle." in refusal.content and "d:1: cites no handle." in refusal.content
    assert state["validation"] == {"ok": False, "attempts": 2, "dropped": [], "flagged": ["d:1", "t:1"], "continuity": None}
    assert len(state["report"]["timeline"]) == 2 and state["report"]["timeline"][0]["evidence"] == []     # kept, and flagged


def test_no_unknown_or_referenced_only_citation_survives_the_validator(cfg):
    def cite_badly(args, attempt):
        args["hypotheses"][0]["support"] += ["log:worker-01/deadbeef", fx.COMMIT]            # one never seen, one only listed
        args["timeline"][0]["evidence"] = [fx.COMMIT]
        args["actions"][0]["evidence"].append("log:worker-01/deadbeef")
        args["summary"] += f" See also {fx.COMMIT} and (log:worker-01/deadbeef)."
        args["alerts"].append({"handle": "dd:ev9999999999", "verdict": "noise"})

    state = report_from(edit_report(cite_badly), cfg, "citations")
    refusal = next(m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "submit_report")
    assert "No tool result in this run showed these handles: dd:ev9999999999, log:worker-01/deadbeef." in refusal.content
    assert f"These handles were only listed, never shown: {fx.COMMIT}." in refusal.content
    report = {k: v for k, v in state["report"].items() if k != "validation"}
    text = json.dumps(report)
    assert "deadbeef" not in text and fx.COMMIT not in text and "dd:ev9999999999" not in text
    assert "[citation dropped]" in report["summary"]
    assert state["validation"]["dropped"] == ["dd:ev9999999999", "log:worker-01/deadbeef", fx.COMMIT] and state["validation"]["flagged"] == ["t:1"]
    seen = {h for e in state["ledger"] if e["status"] == "ok" for h in e["shown"]}
    assert validator.report_handles(report) <= seen                               # everything left was shown in this run


def test_inline_handles_are_found_in_prose_however_they_are_wrapped():
    text = "See (log:a/0a1b2c3d), `jk:job#12`, snow:INC0000001. And dd:ev12,dd:ev13; not a:handle or http://x.y/z."
    assert validator.inline_handles(text) == {"log:a/0a1b2c3d", "jk:job#12", "snow:INC0000001", "dd:ev12", "dd:ev13"}
    metric = "metric:avg:system.cpu{host:a,env:prod}by{host}@1400-1452"
    assert validator.inline_handles(f"the series {metric}.") == {metric}


def test_an_action_on_evidence_no_tool_showed_never_reaches_a_person(cfg):
    asked: list[str] = []

    def script(messages, tools):
        if sum(isinstance(m, AIMessage) for m in messages) == 0:
            return "act", [call("propose_action", 1, ("h", "x"), kind=fixture_script.action_kind(tools), target="t", evidence=["log:worker-01/deadbeef"])]
        return "stop", []

    def decide(cards):
        asked.extend(c["tool"] for c in cards)
        return {c["call_id"]: {"approve": True} for c in cards}

    state = report_from(script, cfg, "fabricated", decider=decide)
    assert "propose_action" not in asked
    answer = next(m for m in state["messages"] if isinstance(m, ToolMessage))
    assert answer.status == "error" and answer.content.startswith("REJECTED by policy: No tool result in this run showed these handles: log:worker-01/deadbeef")
    [proposal] = state["report"]["proposed_actions"]
    assert proposal["decision"] == "rejected" and proposal["by"]["who"] == "policy"


def test_an_input_sent_as_null_is_read_as_left_out(cfg):
    def script(messages, tools):
        if sum(isinstance(m, AIMessage) for m in messages) == 0:
            return "nulls", [call("search_logs", 1, ("h", "x"), since="-2h", until=None, host=None, group_by=None, terms=["ERROR"])]
        return "stop", []

    state = report_from(script, cfg, "nulls")
    entry = next(e for e in state["ledger"] if e["tool"] == "search_logs")
    assert entry["status"] == "ok" and entry["args"] == {"since": "-2h", "terms": ["ERROR"]}


# -- the provider schema adapter ---------------------------------------------------------------------------


def definitions() -> list[dict]:
    return [registry.definition(spec) for spec in internal_tools.toolset(registry.surface(tags=True)).values()]


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk(item)


def test_the_strict_adapter_for_the_default_provider():
    tools = provider_tools(definitions(), "anthropic")
    assert len(tools) == 14 and all(set(t) == {"name", "description", "input_schema", "strict"} and t["strict"] is True for t in tools)
    bounds = {"minimum", "maximum", "minLength", "maxLength", "maxItems", "minItems", "default"}
    for tool in tools:
        for node in walk(tool["input_schema"]):
            assert not bounds & set(node) or "properties" in node and bounds & set(node["properties"]), tool["name"]
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False, tool["name"]
    logs = next(t for t in tools if t["name"] == "search_logs")["input_schema"]
    assert logs["required"][0] == "rationale" and "since" in logs["required"] and "until" not in logs["required"]
    assert "maximum: 50" in logs["properties"]["limit"]["description"]            # the bound is said in words, and we still enforce it
    relaxed = provider_tools(definitions(), "anthropic", strict=False)
    assert all("strict" not in t for t in relaxed) and relaxed[2]["input_schema"]["properties"]["limit"]["maximum"] == 50


def test_the_strict_adapter_for_a_provider_that_wants_every_property_required():
    tools = provider_tools(definitions(), "openai")
    logs = next(t for t in tools if t["function"]["name"] == "search_logs")["function"]
    assert logs["strict"] is True and logs["parameters"]["required"] == list(logs["parameters"]["properties"])
    assert logs["parameters"]["properties"]["until"]["anyOf"][1] == {"type": "null"} and "anyOf" not in logs["parameters"]["properties"]["since"]
    assert "maximum" not in logs["parameters"]["properties"]["limit"]["anyOf"][0]
    board_tool = next(t for t in tools if t["function"]["name"] == "update_board")["function"]["parameters"]
    update = board_tool["$defs"]["HypothesisUpdate"]
    assert update["required"] == list(update["properties"]) and update["properties"]["id"] == {"type": "string", "description": "PLACEHOLDER"}
    plain = provider_tools(definitions(), "scripted")
    assert plain[0]["type"] == "function" and "strict" not in plain[0]["function"]


# -- the verifier's verdict and packets (J13) --------------------------------------------------------------


REPORT = {
    "hypotheses": [{"id": "lead", "statement": "The release did it.", "role": "cause", "status": "leading", "subject": "svc", "confidence": 0.65,
                    "support": [LOG, LOG2], "against": [], "changes": []},
                   {"id": "rival", "statement": "The disk is dying.", "role": "cause", "status": "rejected", "subject": "host", "confidence": 0.1,
                    "support": [CHAT], "against": ["q:0a1b2c3d"], "changes": []}],
    "timeline": [{"t_utc": "2026-03-02T02:09:31Z", "event": "Writes failed.", "evidence": [LOG]}],
    "actions": [{"rank": 1, "action": "Roll back.", "owner": "Reporting", "evidence": [LOG2]}, {"rank": 2, "action": "Purge.", "owner": "Reporting", "evidence": []}],
    "do_not": [{"action": "Replace the disk.", "why": "Nothing says it is failing.", "evidence": ["q:0a1b2c3d"]}],
    "summary": "s", "open_questions": [], "alerts": []}
EVIDENCE = [{"call_id": "c1", "handle": LOG, "t_known": "2026-03-02T02:09:31Z", "sha256": "x", "text": f"{LOG}  ERROR write failed"},
            {"call_id": "c2", "handle": LOG2, "t_known": "2026-03-02T02:10:02Z", "sha256": "x", "text": f"{LOG2}  WARN cleanup skipped\n    at frame"},
            {"call_id": "c3", "handle": "q:0a1b2c3d", "t_known": None, "sha256": "x", "text": "q:0a1b2c3d  count by minute"},
            {"call_id": "c4", "handle": "log:worker-02/aaaaaaaa", "t_known": "2026-03-02T02:05:41Z", "sha256": "x", "text": "log:worker-02/aaaaaaaa  WARN gc pause " + "z" * 300},
            {"call_id": "c5", "handle": "log:worker-02/bbbbbbbb", "t_known": "2026-03-02T02:15:00Z", "sha256": "x", "text": "log:worker-02/bbbbbbbb  INFO health"}]


def test_claim_ids_and_what_counts_as_the_leading_hypothesis():
    assert list(validator.report_claims(REPORT)) == ["h:lead", "h:rival", "t:1", "a:1", "a:2", "d:1"]
    assert verifier.leading_claims(REPORT) == ["h:lead", "a:1", "a:2"]
    assert verifier.leading_claims({**REPORT, "hypotheses": REPORT["hypotheses"][1:]}) == ["a:1", "a:2"]


def test_a_packet_is_a_claim_and_the_rows_of_its_handles_as_the_model_saw_them():
    text = verifier.packets(REPORT, EVIDENCE)
    blocks = text.split("\n\n")
    assert [b.split(":")[0] + ":" + b.split(":")[1].split(" ")[0] for b in blocks] == ["Claim h:lead", "Claim h:rival", "Claim t:1", "Claim a:1", "Claim a:2", "Claim d:1"]
    assert blocks[0].startswith("Claim h:lead: [leading cause, confidence 0.65, blames svc] The release did it.\n  support:\n")
    assert f"    {LOG}  ERROR write failed" in blocks[0] and "        at frame" in blocks[0] and "  against: (none)" in blocks[0]
    assert f"    {CHAT}  (no tool result in this run showed this handle)" in blocks[1]
    assert blocks[4] == "Claim a:2: do: Purge. (owner: Reporting)\n  evidence: (none)"
    only = verifier.packets(REPORT, EVIDENCE, only={"t:1", "d:1"})
    assert only.count("Claim ") == 2 and "Claim t:1" in only and "Claim d:1" in only
    long = verifier.packets(REPORT, [{**EVIDENCE[0], "text": "x" * 4000}])
    assert "x" * 1500 + " […cut for the packet]" in long and "x" * 1501 not in long


def test_the_digest_is_the_uncited_rows_newest_first_inside_a_budget():
    text = verifier.digest(REPORT, EVIDENCE, tokens=1500)
    lines = text.splitlines()
    assert [ln.split("  ")[0] for ln in lines] == ["log:worker-02/bbbbbbbb", "log:worker-02/aaaaaaaa"]      # newest first, cited rows left out
    assert lines[1].endswith(" […]") and len(lines[1]) == 200 + len(" […]")
    assert verifier.digest(REPORT, EVIDENCE[:3], tokens=1500) == "(every row that was shown is cited)"
    tight = verifier.digest(REPORT, EVIDENCE, tokens=20)
    assert tight.splitlines()[-1] == "[1 more uncited rows are not listed]" and len(tight.splitlines()) == 2


def test_the_verdict_schema_is_the_one_tool_a_verifier_is_given():
    spec = verifier.spec()
    assert (spec.name, spec.kind) == ("submit_verdict", "internal")
    verdict = verifier.SubmitVerdictArgs.model_validate({
        "claims": [{"id": "h:lead", "verdict": "supported", "quote": "write failed", "handle": LOG}],
        "alternatives": [{"statement": "A full disk from another job.", "fit": "equal", "check": {"tool": "search_logs", "args": [{"name": "host", "value": "worker-02"}]}}],
        "ignored": ["log:worker-02/aaaaaaaa"], "overall": "revise"})
    assert verdict.overall == "revise" and verdict.alternatives[0].check.args[0].name == "host"
    for bad in ({"overall": "maybe"}, {"overall": "pass", "claims": [{"id": "x", "verdict": "true", "quote": "q"}]},
                {"overall": "pass", "alternatives": [{"statement": "s", "fit": "equal"}] * 4}, {"overall": "pass", "alternatives": [{"statement": "s", "fit": "stronger"}]}):
        with pytest.raises(ValidationError):
            verifier.SubmitVerdictArgs.model_validate(bad)
    definition = registry.definition(spec)
    assert "rationale" not in definition["parameters"]["properties"] and set(definition["parameters"]["$defs"]) == {"ClaimVerdict", "Alternative", "Check", "Pair"}


def test_the_gate_policy_is_a_pure_function():
    tools = internal_tools.toolset(fx.registry())
    calls = [{"call_id": "1", "tool": "search_logs", "args": {}, "by": "agent"}, {"call_id": "2", "tool": "propose_action", "args": {}, "by": "agent"},
             {"call_id": "3", "tool": "update_board", "args": {}, "by": "agent"}, {"call_id": "4", "tool": "tail_logs", "args": {}, "by": "agent"},
             {"call_id": "5", "tool": "list_alerts", "args": {}, "by": "harness"}]
    decisions, ask = gate.apply(calls, tools, used=0, budget=5, auto_approve=True, via="cli")
    assert [c["call_id"] for c in ask] == ["2"]                                   # an action asks even when reads do not
    tight, ask_tight = gate.apply(calls, tools, used=0, budget=1, auto_approve=True, via="cli")
    assert ask_tight == [] and tight["2"]["decision"] == "rejected"               # counted per call: the batch cannot overshoot
    assert decisions["1"]["by"]["who"] == "auto" and decisions["3"]["by"]["who"] == "policy" and decisions["5"]["by"]["who"] == "auto"
    assert decisions["4"]["decision"] == "rejected" and "no tool named tail_logs" in decisions["4"]["reason"]
    over, ask = gate.apply(calls[:2], tools, used=1, budget=1, auto_approve=True, via="cli")
    assert {d["decision"] for d in over.values()} == {"rejected"} and ask == [] and "submit now" in over["1"]["reason"]
    free, _ = gate.apply(calls[4:], tools, used=99, budget=1, auto_approve=True, via="cli")
    assert free["5"]["decision"] == "approved"                                    # the harness's calls are never charged
    _, ask = gate.apply(calls[:1], tools, used=0, budget=5, auto_approve=False, via="cli")
    assert [c["tool"] for c in ask] == ["search_logs"]
    assert ToolContext(store=None, now=fx.NOW, tags=False).run_id is None
