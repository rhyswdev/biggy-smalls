"""The report contract (docs/decisions/tools-agent-build.md section 5; build row A1): a full report and a
partial one validate, the committed schema file is the model's, and `python -m smalls.agent schema` prints
the whole surface with tags on and off. The example reports describe a made-up incident."""
import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from smalls.agent import __main__ as cli
from smalls.agent import internal_tools
from smalls.contracts import report
from smalls.contracts.report import Report

HERE = Path(__file__).resolve().parent
EXAMPLES = {name: json.loads((HERE / "reports" / f"{name}.json").read_text()) for name in ("full", "partial")}
ELEVEN = ["list_alerts", "get_record", "search_logs", "query_metrics", "list_changes", "itsm_query", "search_knowledge",
          "get_topology", "get_oncall", "read_chat", "check_status_page"]
MODEL_WRITTEN = {"summary", "impact", "hypotheses", "timeline", "actions", "do_not", "alerts", "open_questions", "stakeholder_update"}
CODE_WRITTEN = {"as_of", "proposed_actions", "validation"}


@pytest.mark.parametrize("name", ["full", "partial"])
def test_a_full_report_and_a_partial_one_both_validate(name):
    Report.model_validate(EXAMPLES[name])
    jsonschema.validate(EXAMPLES[name], json.loads(report.SCHEMA_PATH.read_text()))


def test_a_partial_report_has_no_impact_and_no_stakeholder_update():
    partial = Report.model_validate(EXAMPLES["partial"])
    assert partial.impact is None and partial.stakeholder_update is None and partial.validation is None


def test_the_committed_schema_file_is_the_models():
    assert report.SCHEMA_PATH.read_text() == report.schema_text()


def test_the_report_schema_carries_no_description_text():
    def keys(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k not in ("properties", "$defs"):
                    yield k
                    yield from keys(v)
                else:
                    for sub in v.values():
                        yield from keys(sub)
        elif isinstance(node, list):
            for item in node:
                yield from keys(item)

    assert "description" not in set(keys(report.report_schema()))
    # A field that is itself called `description` or `title` would still be kept.
    assert report.strip_keys({"properties": {"description": {"type": "string", "description": "x"}}}, ("description",)) == \
        {"properties": {"description": {"type": "string"}}}


def test_the_report_holds_the_ruled_fields():
    assert set(Report.model_fields) == MODEL_WRITTEN | CODE_WRITTEN
    hypothesis = report.Hypothesis.model_fields
    assert {"id", "statement", "role", "status", "subject", "entities", "changes", "confidence", "support", "against",
            "source", "history"} == set(hypothesis)
    assert "trigger" not in Report.model_fields       # the trigger is the hypothesis with that role
    assert set(report.ReportAlert.model_fields["verdict"].annotation.__args__) == {"this_incident", "other_incident", "noise", "unassessed"}
    assert set(report.AlertVerdict.model_fields["verdict"].annotation.__args__) == {"this_incident", "other_incident", "noise"}
    assert set(report.StakeholderUpdate.model_fields) == {"status", "customer_impact", "scope", "changed_since_last", "next_steps", "next_update_at"}
    assert set(report.ProposedAction.model_fields) == {"call_id", "tool", "args", "rationale", "decision", "by", "reason", "decided_at"}


def test_submit_report_carries_exactly_the_model_written_fields():
    assert set(internal_tools.SubmitReportArgs.model_fields) == MODEL_WRITTEN
    update = set(internal_tools.HypothesisUpdate.model_fields)
    written = set(internal_tools.HypothesisFields.model_fields)
    assert written == set(report.Hypothesis.model_fields) - {"source", "history"}
    assert update - written == {"source", "because", "discriminating"}
    assert set(internal_tools.UpdateBoardArgs.model_fields) == {"hypotheses", "timeline", "open_questions"}


@pytest.mark.parametrize("bad", [
    {"confidence": 1.2}, {"role": "culprit"}, {"status": "confirmed"}, {"source": "guessed"}, {"surprise": 1}])
def test_a_report_with_a_bad_hypothesis_is_refused(bad):
    doc = json.loads(json.dumps(EXAMPLES["full"]))
    doc["hypotheses"][0].update(bad)
    with pytest.raises(ValidationError):
        Report.model_validate(doc)


@pytest.mark.parametrize("tags", ["on", "off"])
def test_the_schema_command_prints_every_tool_and_the_report_schema(capsys, tags):
    assert cli.main(["schema", "--tags", tags]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["tags"] == tags and doc["report"] == report.report_schema()
    assert [t["name"] for t in doc["tools"]] == ELEVEN + ["propose_action", "update_board", "submit_report"]
    tag_inputs = {t["name"]: sorted(set(t["parameters"]["properties"]) & {"signal", "act", "change_kind"}) for t in doc["tools"]}
    wanted = {"list_alerts": ["signal"], "search_logs": ["signal"], "list_changes": ["change_kind"], "itsm_query": ["signal"], "read_chat": ["act"]}
    assert {k: v for k, v in tag_inputs.items() if v} == (wanted if tags == "on" else {})
    group_by = next(t for t in doc["tools"] if t["name"] == "search_logs")["parameters"]["properties"]["group_by"]["enum"]
    assert ("signal" in group_by) == (tags == "on")


def test_the_schema_command_follows_the_tags_default(capsys, monkeypatch):
    monkeypatch.delenv("SMALLS_TAGS", raising=False)
    cli.main(["schema"])
    assert json.loads(capsys.readouterr().out)["tags"] == "on"
    monkeypatch.setenv("SMALLS_TAGS", "off")
    cli.main(["schema"])
    assert json.loads(capsys.readouterr().out)["tags"] == "off"


def test_the_schema_command_prints_the_prefix_size(capsys):
    assert cli.main(["schema", "--size"]) == 0
    sizes = json.loads(capsys.readouterr().out)
    for arm in ("tags_on", "tags_off"):
        assert sizes[arm]["prefix_chars"] == sizes[arm]["system_prompt_chars"] + sizes[arm]["tool_definitions_chars"]
        assert sizes[arm]["prefix_tokens"] == sizes[arm]["prefix_chars"] // 4
    assert sizes["tags_on"]["tool_definitions_chars"] > sizes["tags_off"]["tool_definitions_chars"] > 0
