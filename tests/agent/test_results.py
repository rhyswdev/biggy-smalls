"""The results ledger and the tuning log (docs/decisions/eval-loop.md, V7 and row P2): a row is built from a run
record by code, `add` refuses a batch nobody described, and the log has a table per batch and a line per measure
across batches. Scripted: no key, no network."""

from __future__ import annotations

import json

import pytest
import yaml

import golden
import parity
import test_agent_real_tools
from eval import results
from smalls.agent.config import load_config
from smalls.agent.runner import Runner, bundle_toolbox

TRIGGER = (parity.BUNDLE / "triggers" / f"{golden.SECOND}.md").read_text(encoding="utf-8")


@pytest.fixture
def record(tmp_path):
    """One scripted run at the second checkpoint, on its own trigger, with the bundle's tools."""
    cfg = load_config(runs_dir=tmp_path / "runs")
    r = Runner(cfg, bundle_toolbox(parity.BUNDLE, True), "fixture-C-1452-tags-r1", script=test_agent_real_tools.script, eval_run=True)
    r.start(TRIGGER, golden.ctx(golden.SECOND).now, checkpoint=golden.SECOND)
    r.close()
    return tmp_path / "runs" / "fixture-C-1452-tags-r1"


@pytest.fixture
def described(tmp_path):
    path = tmp_path / "batches.yaml"
    path.write_text(yaml.safe_dump({"batches": [{"name": "base", "phase": 2, "pass": 6, "what": "A fixture batch."}]}), encoding="utf-8")
    return path


def test_a_row_is_built_from_a_run_record_by_code(record):
    summary = json.loads((record / "summary.json").read_text(encoding="utf-8"))
    report = json.loads((record / "report.json").read_text(encoding="utf-8"))
    row = results.row(record, batch="base", pass_=6, phase=2)
    assert (row["run_id"], row["batch"], row["pass"], row["phase"]) == ("fixture-C-1452-tags-r1", "base", "6", "2")
    assert (row["arm"], row["checkpoint"], row["tags"], row["trigger"], row["status"]) == ("C", golden.SECOND, True, "standard", summary["status"])
    assert row["tool_calls"] == summary["process"]["tool_calls"] and row["model_turns"] == summary["process"]["model_turns"]
    assert row["wall_s"] == summary["wall_s_total"] and row["cost_usd"] == summary["tokens"]["cost_usd"]
    assert row["roles"]["investigator"] == row["profiles"]["investigator"] == "scripted"
    assert set(row["roles"]) == set(row["profiles"]) == {"investigator", "contrarian", "claims", "overfit", "drafter"}
    assert row["prompt_sha"] and row["tools_sha"] and row["commit"] is not None and row["date"] == row["created"][:10]
    assert row["leading"] is None and row["rejected"] == []                                  # the script's report is inconclusive
    assert row["open"] == [h["subject"] for h in report["hypotheses"] if h["status"] == "open"] == ["unknown"]
    assert row["quiet_board"] == summary["process"]["quiet_board"] and row["board_refusals"] == 0
    assert row["malformed_retries"] == 0 and row["scores"] == {}
    assert set(row["cost_by_role"]) <= {"investigator", "claims", "contrarian"}


def test_add_replaces_a_runs_row_and_refuses_a_batch_nobody_described(record, described, tmp_path):
    ledger = tmp_path / "runs.jsonl"
    results.add([str(record)], batch="base", ledger=ledger, batches_file=described)
    results.add([str(record)], batch="base", ledger=ledger, batches_file=described)        # the same run again: one row
    rows = results.load(ledger)
    assert [r["run_id"] for r in rows] == ["fixture-C-1452-tags-r1"] and rows[0]["pass"] == "6" and rows[0]["phase"] == "2"
    with pytest.raises(SystemExit, match="no batch named 'other'"):
        results.add([str(record)], batch="other", ledger=ledger, batches_file=described)


def test_a_custom_trigger_is_told_apart_from_the_standard_one(record, tmp_path):
    run = json.loads((record / "run.json").read_text(encoding="utf-8"))
    assert results.trigger_kind(run) == "standard"
    assert results.trigger_kind({**run, "trigger": "Is the database the cause?"}) == "custom"
    framing = sorted(results.FRAMING.glob("*.md"))[0]
    assert results.trigger_kind({**run, "trigger": framing.read_text(encoding="utf-8")}) == f"framing:{framing.stem}"


def test_the_log_has_a_table_per_batch_and_a_line_per_measure_across_batches(record, described):
    one = results.row(record, batch="base", pass_=6, phase=2)
    two = {**one, "run_id": "later-r1", "batch": "later", "created": "2999-01-01T00:00:00+00:00", "cost_usd": 0.5,
           "leading": {"subject": "Some Service", "confidence": 0.8, "role": "cause"}}
    three = {**two, "run_id": "later-r2", "created": "2999-01-01T00:01:00+00:00", "leading": {**two["leading"], "subject": "some-service"}}
    text = results.render([three, two, one], results.batches(described))
    assert text.index("### base") < text.index("### later")                                # batches in the order they ran
    assert "A fixture batch." in text and "phase 2; pass 6." in text
    for name, _ in results.MEASURES:
        line = next(ln for ln in text.splitlines() if ln.startswith(f"- **{name}.**"))
        assert "base: " in line and "later: " in line                                      # each measure across every batch
    assert text.count("| Run | At |") == 2 and "`fixture-C-1452-tags-r1`" in text and "`later-r1`" in text
    assert "3 runs in 2 batches" in text and "later: Some Service 0.80 ×2" in text           # one subject, however it is written


def test_rebuild_keeps_each_rows_batch_and_scores_and_export_writes_the_pages_two_files(record, described, tmp_path):
    ledger = tmp_path / "runs.jsonl"
    results.add([str(record)], batch="base", ledger=ledger, batches_file=described)
    rows = results.load(ledger)
    results.save([{**rows[0], "scores": {"outcome": 1}, "board_only_turns": None}, {**rows[0], "run_id": "gone", "batch": "base"}], ledger)
    n, missing = results.rebuild(ledger=ledger, batches_file=described, records=(record.parent,))
    rebuilt = {r["run_id"]: r for r in results.load(ledger)}
    assert (n, missing) == (2, ["gone"]) and rebuilt["gone"]["batch"] == "base"                 # a row with no record here is kept
    assert rebuilt[record.name]["scores"] == {"outcome": 1} and rebuilt[record.name]["board_only_turns"] is not None
    runs_path, batches_path = results.export(tmp_path / "page", [rebuilt[record.name]], results.batches(described))
    runs, batch_rows = json.loads(runs_path.read_text()), json.loads(batches_path.read_text())
    assert [r["run_id"] for r in runs] == [record.name] and runs[0]["investigator"] == "scripted" and runs[0]["batch_order"] == 1
    assert {"trigger_on_board", "board_only_turns", "cost_usd", "partial", "nudges"} <= set(runs[0])
    assert batch_rows == [{"batch": "base", "order": 1, "phase": "2", "pass": "6", "finding": "", "result": "", "what": "A fixture batch.",
                           "moved": "", "runs": 1}]
