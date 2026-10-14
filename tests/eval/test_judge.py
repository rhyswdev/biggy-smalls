"""The judge, offline: its packet, its quote checks and its budget (row S4 of docs/decisions/eval-loop.md).
No model is called; the judge's answer is scripted."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval import judge as judging
from eval.key import load_key
from eval.score import score_hand

ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "eval" / "reports"
CP = load_key("larkspur-checkout-2026-10-13").checkpoint("1452")


def correct() -> dict:
    return json.loads((REPORTS / "correct.json").read_text())


def test_the_prompt_names_no_bundle_string():
    from smalls_data.prompt_lint import bundle_strings      # the strings lint invariant 16 checks prompts against
    text = judging.PROMPT.read_text().lower()
    assert not [s for s in bundle_strings() if s.lower() in text]


def test_a_yes_counts_only_with_a_quote_from_the_report():
    def script(turn: str) -> dict:
        return {"checklist": [
            {"id": "pool_exhaustion", "yes": True, "quote": "the gateway pools hit their limit"},
            {"id": "caller_timeouts", "yes": True, "quote": "a sentence the report never wrote"},
            {"id": "restart_redis", "yes": False, "quote": ""}], "claims": []}
    j = judging.judge(correct(), CP, {}, label="t", script=script)
    items = j["mechanism"]["items"]
    assert items["pool_exhaustion"]["yes"] and not items["caller_timeouts"]["yes"] and items["caller_timeouts"]["said_yes"]
    assert not items["retry_or_timeout_increase"]["answered"] and j["mechanism"]["yes"] == 1


def test_claims_are_packed_leading_first_with_each_row_once():
    r = correct()
    evidence = {"jk:payments-gateway-deploy#412": "build 412 VERSION 3.8.0", "dt:P-26101350": "problem on the provider",
                "jk:checkout-api-deploy#898": "rollback " + "x" * 5000}
    packet, rows = judging.claims(r, evidence, limit=12, chars=300)
    assert [c["id"] for c in packet] == ["h-gw", "h-psp", "h-co"]
    assert set(rows) == set(evidence) and judging.CUT in rows["jk:checkout-api-deploy#898"] and len(rows["jk:checkout-api-deploy#898"]) < 320
    turn = judging.user_turn(r, CP, packet, rows)
    assert turn.count("[jk:payments-gateway-deploy#412]") == 1 and "THE CHECKLIST" in turn


def test_a_claim_quote_must_come_from_a_row_it_cites():
    r = correct()
    evidence = {"jk:payments-gateway-deploy#412": "build 412 VERSION 3.8.0", "dt:P-26101350": "problem on the provider"}

    def script(turn: str) -> dict:
        return {"checklist": [], "claims": [
            {"id": "h-gw", "verdict": "partial", "quote": "VERSION 3.8.0", "handle": "jk:payments-gateway-deploy#412"},
            {"id": "h-psp", "verdict": "supported", "quote": "VERSION 3.8.0", "handle": "jk:payments-gateway-deploy#412"}]}
    j = judging.judge(r, CP, evidence, label="t", script=script)
    by_id = j["claims"]["by_id"]
    assert by_id["h-gw"] == {"verdict": "partial", "quote_found": True}
    assert by_id["h-psp"] == {"verdict": "supported", "quote_found": False}      # a row h-psp does not cite
    assert j["claims"]["quote_missing"] == 1 and j["claims"]["of"] == 2


def test_a_live_call_past_the_budget_is_refused_before_it_is_made(tmp_path):
    spend = tmp_path / "spend.jsonl"
    spend.write_text(json.dumps({"cost_usd": 4.95}) + "\n")
    with pytest.raises(SystemExit, match="ask before spending more"):
        judging.judge(correct(), CP, {}, label="t", cfg={**judging.config(), "budget_usd": 5.0}, spend=spend)


def test_hand_written_reports_are_separated():
    scores = {name: score_hand(REPORTS / f"{name}.json", "1452", with_judge=False) for name in ("correct", "checkout-deploy", "database")}
    assert scores["correct"]["cause"]["score"] == 1.0 and scores["correct"]["bar"]["met"]
    for wrong in ("checkout-deploy", "database"):
        assert scores[wrong]["cause"]["score"] == 0.0 and scores[wrong]["confidence"]["hard_fail"]
        assert scores[wrong]["traps"]["passed"] < scores["correct"]["traps"]["passed"]
