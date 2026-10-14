"""The answer key and the scorer's deterministic checks (rows S1 to S3 of docs/decisions/eval-loop.md).

The reports here are built by hand from handles the bundle holds, so each check is shown passing and failing
on a report whose right answer is known. No model is called.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from eval.key import load_aliases, load_key, load_manifest, validate
from eval.scorer import RunLog, cited, score_report, score_run

BUNDLE = "larkspur-checkout-2026-10-13"
AS_OF = "2026-10-13T14:52:00Z"

# Real handles, each with the key ref it resolves to.
CHANGE = "jk:payments-gateway-deploy#412"     # sig.jk412_changeset (F.retry_change)
CHG = "snow:CHG0031877"
PSP = "dt:P-26101350"                         # F.psp_slow
POOL = "spl:alert/891f48a7"                   # F.pool_exhausted
IDLE = "snow:jn/ec7656f6"                     # F.idle_in_txn, a work note
IDLE_CHAT = "slack:inc-checkout-latency/cd4b848c"
ROLLBACK = "jk:checkout-api-deploy#898"       # F.rollback_no_fix
RECON = "jk:payments-reconciler#2311"
ONCALL = "pd:oncall/fafc2fbc"
FUTURE = "snow:INC0048811"                    # a version known at 14:53 exists, but earlier ones too
SHOWN = [CHANGE, CHG, PSP, POOL, IDLE, IDLE_CHAT, ROLLBACK, RECON, ONCALL]


def hyp(id_: str, subject: str, role: str, status: str, conf: float, support=(), against=(), changes=(), statement=""):
    return {"id": id_, "statement": statement or f"{subject} is a {role}", "role": role, "status": status, "subject": subject,
            "entities": [subject], "changes": list(changes), "confidence": conf, "support": list(support), "against": list(against),
            "source": "derived", "history": []}


def correct() -> dict:
    return {
        "as_of": AS_OF,
        "summary": f"The gateway release holds a transaction across slow provider calls {CHANGE}.",
        "impact": {"business_service": "Online Checkout", "cis": ["payments-gateway"],
                   "priority": {"suggested": "P1", "impact": "high", "urgency": "high"},
                   "assignment_group": "Payments Engineering", "oncall": "Priya Raman", "customer": "payments fail"},
        "hypotheses": [
            hyp("h1", "payments-gateway", "cause", "leading", 0.8, [CHANGE, POOL, IDLE], changes=[CHG, CHANGE]),
            hyp("h2", "PayCore", "trigger", "open", 0.7, [PSP]),
            hyp("h3", "payments-reconciler", "contributing", "open", 0.6, [RECON]),
            hyp("h4", "checkout-api", "cause", "rejected", 0.1, [], [ROLLBACK]),
            hyp("h5", "pay-db-01", "cause", "rejected", 0.1, [], [IDLE]),
        ],
        "timeline": [], "actions": [{"rank": 1, "action": "Roll the gateway back", "owner": "Priya Raman", "evidence": [CHANGE]}],
        "do_not": [{"action": "Roll back checkout again", "why": "it did not help", "evidence": [ROLLBACK]}],
        "alerts": [{"handle": PSP, "verdict": "this_incident"}, {"handle": POOL, "verdict": "this_incident"}],
        "open_questions": [], "stakeholder_update": None, "proposed_actions": [],
        "validation": {"ok": True, "attempts": 1, "dropped": [], "flagged": [], "continuity": None, "verifier": "pass", "disputed": []},
    }


class Log(RunLog):
    def __init__(self, shown=SHOWN, referenced=(), notes=(), alert_streams=()):
        self.shown, self.referenced, self.notes, self.alert_streams = set(shown), set(referenced), set(notes), list(alert_streams)


def score(report: dict, log: RunLog | None = None, checkpoint: str = "1452", as_of: str = AS_OF, **kw) -> dict:
    return score_report(report, checkpoint=checkpoint, as_of=as_of, log=log or Log(), bundle=BUNDLE, **kw)


# -- S1: the key -----------------------------------------------------------------------------------------


def test_the_key_validates():
    assert validate(BUNDLE) == []


def test_a_key_with_a_trap_on_an_unknown_entity_fails(tmp_path, monkeypatch):
    import eval.key as k
    bundle = tmp_path / "b"
    (bundle / "_internal").mkdir(parents=True)
    src = k.bundle_dir(BUNDLE) / "_internal"
    (bundle / "_internal" / "manifest.jsonl").write_text((src / "manifest.jsonl").read_text())
    text = (src / "answer_key.yaml").read_text().replace("match: {entity: redis-sessions}", "match: {entity: no-such-thing}")
    (bundle / "_internal" / "answer_key.yaml").write_text(text)
    errors = validate(str(bundle))
    assert any("no-such-thing" in e for e in errors)


def test_subjects_resolve_through_any_alias():
    a = load_aliases()
    assert a.entities_in("Payments Gateway") == ["payments-gateway"]
    assert a.entities_in("api.paycore.example") == ["paycore"]
    assert a.entities_in("payments-gateway 3.8.0 retry path") == ["payments-gateway"]   # not the database named `payments`
    assert a.entities_in("network") == []


# -- S2: the checks ----------------------------------------------------------------------------------------


def test_the_correct_report_scores_full():
    s = score(correct())
    assert s["integrity"]["ok"] and s["cause"]["score"] == 1.0 and s["trigger"]["score"] == 1.0
    assert s["facts"]["required"] == 5 and s["traps"]["passed"] == s["traps"]["of"]
    assert s["owner"]["pass"] and not s["confidence"]["hard_fail"] and s["contributing"]["found"] == 1
    assert s["bar"]["met"]


def test_a_fabricated_citation_scores_zero_on_integrity():
    r = correct()
    r["hypotheses"][0]["support"].append("log:pay-02/deadbeef")
    s = score(r)
    assert s["integrity"]["fabricated"] == ["log:pay-02/deadbeef"] and not s["integrity"]["ok"] and not s["bar"]["met"]


def test_a_future_citation_scores_zero_on_integrity():
    r = correct()
    r["summary"] += " Rolled back by jk:payments-gateway-deploy#413."
    s = score(r, Log(shown=SHOWN + ["jk:payments-gateway-deploy#413"]))
    assert "jk:payments-gateway-deploy#413" in s["integrity"]["future"] and not s["integrity"]["ok"]


def test_a_metric_window_past_now_is_future():
    r = correct()
    m = "metric:sum:postgresql.connections{host:pay-db-01}by{db}@1430-1500"
    r["hypotheses"][0]["support"].append(m)
    s = score(r, Log(shown=SHOWN + [m]))
    assert s["integrity"]["future"] == [m]


def test_an_unseen_citation_is_counted_but_not_fatal():
    r = correct()
    r["hypotheses"][2]["support"].append("jk:payments-reconciler#2310")
    s = score(r, Log(referenced=["jk:payments-reconciler#2310"]))
    assert s["integrity"]["unseen"] == ["jk:payments-reconciler#2310"] and s["integrity"]["ok"]


def test_an_ic_note_minted_in_the_run_is_accepted_as_chat():
    r = correct()
    r["hypotheses"][0]["support"] = ["ic:note/1c9e44d2"]
    s = score(r, Log(notes=["ic:note/1c9e44d2"]))
    assert s["integrity"]["ok"] and s["causal_links"]["without"] == ["h1"] and not s["bar"]["cause_with_non_chat"]


def test_cause_without_the_change_scores_half():
    r = correct()
    r["hypotheses"][0].update(changes=[], support=[POOL, IDLE])
    assert score(r)["cause"]["score"] == 0.5


def test_cause_named_by_version_in_the_statement_scores_full():
    r = correct()
    r["hypotheses"][0].update(changes=[], support=[POOL], statement="payments-gateway 3.8.0 holds connections")
    assert score(r)["cause"]["score"] == 1.0


def test_the_wrong_leading_cause_scores_zero_and_is_a_hard_fail():
    r = correct()
    r["hypotheses"][0]["status"] = "open"
    r["hypotheses"][0]["confidence"] = 0.5
    r["hypotheses"][4].update(status="leading", confidence=0.8, support=[POOL], against=[])
    s = score(r)
    assert s["cause"]["score"] == 0.5          # the right subject is still an open cause
    assert s["confidence"]["hard_fail"] and s["traps"]["traps"]["db-capacity"]["pass"] is False


def test_a_right_leading_cause_below_the_minimum_is_a_soft_fail():
    r = correct()
    r["hypotheses"][0]["confidence"] = 0.5
    s = score(r)
    assert s["confidence"]["soft_fail"] and not s["confidence"]["hard_fail"]


def test_trigger_as_another_role_scores_half_and_absent_scores_zero():
    r = correct()
    r["hypotheses"][1]["role"] = "contributing"
    assert score(r)["trigger"]["score"] == 0.5
    r["hypotheses"].pop(1)
    assert score(r)["trigger"]["score"] == 0.0


def test_fact_recall_needs_a_handle_that_is_not_chat():
    r = correct()
    r["hypotheses"][0]["support"] = [CHANGE, POOL, IDLE_CHAT]
    r["hypotheses"][4]["against"] = [ROLLBACK]
    s = score(r)
    assert s["facts"]["missing"] == ["F.idle_in_txn"]


def test_traps_read_the_role_as_the_claim():
    r = correct()
    # a rejected claim never falls: the rejected database cause passes
    assert score(r)["traps"]["traps"]["db-capacity"]["pass"]
    # an open database cause is still held, so it falls
    r["hypotheses"][4].update(status="open", confidence=0.4)
    assert score(r)["traps"]["traps"]["db-capacity"] == {"pass": False, "why": "fell", "by": ["h5"]}
    # an open database symptom is allowed
    r["hypotheses"][4]["role"] = "symptom"
    assert score(r)["traps"]["traps"]["db-capacity"]["pass"]


def test_a_trap_placed_inside_the_leading_chain_is_addressed():
    r = correct()
    r["hypotheses"].pop(4)
    assert score(r)["traps"]["traps"]["db-capacity"] == {"pass": False, "why": "absent"}
    r["hypotheses"][0]["entities"].append("pay-db-01")
    assert score(r)["traps"]["traps"]["db-capacity"] == {"pass": True, "why": "in_leading"}


def test_an_open_trap_is_allowed_at_the_first_checkpoint_only():
    r = correct()
    r["hypotheses"][3].update(status="open", confidence=0.3)
    assert score(r, checkpoint="1440", as_of="2026-10-13T14:40:00Z")["traps"]["traps"]["checkout-deploy"]["pass"]
    assert not score(r)["traps"]["traps"]["checkout-deploy"]["pass"]


def test_owner_from_oncall_not_the_cmdb():
    r = correct()
    assert score(r)["owner"]["pass"]
    r["impact"]["assignment_group"] = "Marcus Okafor"
    assert not score(r)["owner"]["pass"]
    r = correct()
    r["impact"].update(oncall="unknown", assignment_group="unknown")
    r["actions"][0]["owner"] = "unknown"
    assert score(r)["owner"] == {"pass": False, "named": False, "ruled_out_named": False}


def test_alert_verdicts_are_checked_against_the_key():
    r = correct()
    log_volume = "dd:ev7820516885"        # the checkout rollout's log flood: not this incident
    r["alerts"].append({"handle": log_volume, "verdict": "this_incident"})
    r["alerts"].append({"handle": "dd:ev7820520800", "verdict": "noise"})          # the marketing synthetic: another incident
    s = score(r, Log(shown=SHOWN + [log_volume, "dd:ev7820520800", "sw:54977"], alert_streams=[PSP, POOL, log_volume, "dd:ev7820520800", "sw:54977"]))
    a = s["alerts"]
    assert (a["given"], a["side_right"], a["exact_right"]) == (4, 3, 2)
    assert a["wrong"] == [{"handle": log_volume, "verdict": "this_incident", "expected": "noise"}]
    assert a["missing"] == ["sw:54977"]


def test_open_rival_causes_are_not_links_of_the_chain():
    r = correct()
    r["hypotheses"].append(hyp("h6", "network", "cause", "open", 0.2, [IDLE_CHAT]))
    s = score(r)
    assert s["causal_links"] == {"links": 3, "with_non_chat": 3, "without": []}
    r["hypotheses"][1]["support"] = [IDLE_CHAT]
    assert score(r)["causal_links"]["without"] == ["h2"]


def test_cited_reads_handles_written_into_prose():
    r = correct()
    r["open_questions"] = [f"Does ({RECON}) still hold 40 connections?"]
    assert {CHANGE, RECON} <= cited(r)


# -- S3: process to the first correct leading hypothesis -------------------------------------------------


def _event(type_: str, **data) -> dict:
    return {"type": type_, "data": data}


def test_first_correct_counts_turns_calls_and_tokens(tmp_path):
    events = [
        _event("model_call", node="agent", usage={"total_tokens": 100}),
        _event("tool_result", kind="read"), _event("tool_result", kind="internal"),
        _event("hypothesis_update", subject="pay-db-01", role="cause", status="leading"),
        _event("model_call", node="agent", usage={"total_tokens": 250}),
        _event("tool_result", kind="read"),
        _event("hypothesis_update", subject="Payments Gateway", role="cause", status="leading"),
        _event("model_call", node="agent", usage={"total_tokens": 999}),
    ]
    s = score(correct(), events=events)
    assert s["first_correct"] == {"model_turns": 2, "tool_calls": 2, "tokens": 350}


def test_first_correct_is_absent_not_zero_when_never_reached():
    events = [_event("model_call", node="agent", usage={"total_tokens": 5}),
              _event("hypothesis_update", subject="pay-db-01", role="cause", status="leading")]
    assert score(correct(), events=events)["first_correct"] is None


def test_score_run_reads_a_record_folder(tmp_path):
    folder = tmp_path / "run1"
    folder.mkdir()
    (folder / "run.json").write_text(json.dumps({"run_id": "run1", "arm": "C", "checkpoint": "1452", "now": AS_OF, "bundle": BUNDLE}))
    (folder / "report.json").write_text(json.dumps(correct()))
    (folder / "calls.jsonl").write_text(json.dumps({"tool": "list_alerts", "shown": [PSP, POOL]}) + "\n"
                                        + json.dumps({"tool": "get_record", "shown": SHOWN}) + "\n")
    (folder / "summary.json").write_text(json.dumps({"status": "completed", "stop_reason": None}))
    s = score_run(folder)
    assert s["run_id"] == "run1" and s["has_report"] and s["cause"]["score"] == 1.0 and s["alerts"]["missing"] == []
