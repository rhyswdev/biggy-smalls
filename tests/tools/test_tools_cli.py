"""`smalls tools` (build row B4): call one tool at a given `now` and print its text, then its handles."""
import json

import pytest

import parity
from smalls import cli

BUNDLE = str(parity.BUNDLE)


def test_tools_list_says_which_tools_are_built(capsys):
    assert cli.main(["tools", "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 11 and lines[0].startswith("list_alerts") and "cap 6000 tokens, 40 rows" in lines[0]
    built = {line.split()[0] for line in lines if " built " in line and "not built" not in line}
    assert {"search_logs", "list_alerts", "list_changes", "read_chat", "get_record"} <= built


def test_tools_schema_prints_the_definitions_the_model_is_given(capsys):
    assert cli.main(["tools", "schema", "search_logs", "--tags", "off"]) == 0
    [definition] = json.loads(capsys.readouterr().out)
    assert definition["name"] == "search_logs" and "signal" not in definition["parameters"]["properties"]
    assert cli.main(["tools", "schema", "--tags", "on"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 11
    with pytest.raises(SystemExit, match="no tool named"):
        cli.main(["tools", "schema", "tail_logs"])


def test_tools_call_prints_the_text_then_the_shown_and_referenced_handles(capsys):
    code = cli.main(["tools", "call", "search_logs", "--now", "14:52", "--bundle", BUNDLE, "since=-10m", 'terms=["error"]', "limit=2"])
    out = capsys.readouterr().out.splitlines()
    assert code == 0 and out[-3] == "--- status: ok" and out[-2].startswith("--- shown (2): log:") and out[-1].startswith("--- referenced (0):")
    assert out[0].endswith("2026-10-13T14:52:00Z") and any(line.startswith("[SPL] search ") for line in out)
    shown = out[-2].split(": ", 1)[1].split()
    assert all(any(line.startswith(handle + "  ") for line in out) for handle in shown)


def test_a_text_input_is_taken_as_written_and_other_values_are_read_as_json(capsys):
    from smalls.tools import registry

    surface = registry.surface(tags=True)
    assert cli.tool_arguments(surface["read_chat"], ["since=-3h", "thread_ts=1791900530.000004"]) == {"since": "-3h", "thread_ts": "1791900530.000004"}
    assert cli.tool_arguments(surface["search_logs"], ['terms=["a","b"]', "limit=5", "group_by=minute", "since=2026-10-13T14:00:00Z"]) == \
        {"terms": ["a", "b"], "limit": 5, "group_by": "minute", "since": "2026-10-13T14:00:00Z"}
    assert cli.tool_arguments(surface["itsm_query"], ['filters=[{"name":"state","value":"New"}]', "table=incident"]) == \
        {"filters": [{"name": "state", "value": "New"}], "table": "incident"}
    with pytest.raises(SystemExit, match="not key=value"):
        cli.tool_arguments(surface["get_record"], ["handle"])
    code = cli.main(["tools", "call", "read_chat", "--now", "14:52", "--bundle", BUNDLE, "since=-3h", "thread_ts=1791900530.000004"])
    assert code == 0 and "--- shown (2): slack:" in capsys.readouterr().out


def test_an_error_result_exits_non_zero_and_an_unbuilt_tool_is_refused(capsys):
    assert cli.main(["tools", "call", "get_record", "--now", "14:52", "--bundle", BUNDLE, "handle=conf:NO-SUCH-PAGE"]) == 1
    assert "conf:NO-SUCH-PAGE: not in this bundle." in capsys.readouterr().out
    with pytest.raises(SystemExit, match="no tool named tail_logs"):
        cli.main(["tools", "call", "tail_logs", "--now", "14:52", "--bundle", BUNDLE])
    with pytest.raises(SystemExit, match="name a bundle"):
        cli.main(["tools", "call", "get_record", "--now", "14:52", "handle=conf:X", "--bundle", ""])
