"""The store contract (docs/decisions/ingestion-decisions.md section 3): the replay clock fails closed, the
loader reads only what integration.yaml serves, and nothing under smalls/ can reach the generator."""
import ast
import json
import re
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from smalls.tools import store
from smalls.tools.ingest import base

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "bundles/larkspur-checkout-2026-10-13"
VIEWS = ("visible", "visible_point", "visible_chunk")


@pytest.fixture(scope="module")
def built():
    return store.build(BUNDLE)


def count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_an_empty_clock_shows_nothing(built):
    built.clear_now()
    assert count(built.conn, "record") > 0 and count(built.conn, "point") > 0 and count(built.conn, "chunk") > 0
    assert built.now is None
    assert [count(built.conn, v) for v in VIEWS] == [0, 0, 0]


def test_the_clock_holds_one_row_and_clearing_it_hides_everything(built):
    built.set_now("14:40")
    assert built.now == built.resolve_time("2026-10-13T14:40:00Z")
    assert all(count(built.conn, v) > 0 for v in VIEWS)
    with pytest.raises(sqlite3.IntegrityError, match="at most one row"):
        built.conn.execute("INSERT INTO clock (now) VALUES (0)")
    built.set_now("14:52")
    assert count(built.conn, "clock") == 1
    built.clear_now()
    assert [count(built.conn, v) for v in VIEWS] == [0, 0, 0]


def test_the_views_show_only_what_is_known_before_now(built):
    now = built.set_now("14:40")
    assert built.conn.execute("SELECT max(t_known) FROM visible").fetchone()[0] < now
    assert built.conn.execute("SELECT max(t_known) FROM visible_point").fetchone()[0] < now
    assert count(built.conn, "visible") < count(built.conn, "record")
    hidden = built.conn.execute("SELECT count(*) FROM chunk ch WHERE ch.rid NOT IN (SELECT rid FROM visible)").fetchone()[0]
    assert hidden > 0 and count(built.conn, "visible_chunk") == count(built.conn, "chunk") - hidden
    built.clear_now()


@pytest.mark.parametrize("rel", ["_internal/manifest.jsonl", "_internal/answer_key.yaml", "triggers/1452.md", "scenario/facts.yaml",
                                 "../../scenario/facts.yaml", "/etc/hosts", "integration.yaml", "slack/../_internal/manifest.jsonl",
                                 "datadog/../triggers/1440.md"])
def test_the_loader_refuses_what_is_not_served(rel):
    with pytest.raises(store.PathRefused):
        store.Bundle(BUNDLE).text(rel)


def test_the_loader_refuses_forbidden_folders_even_when_integration_lists_them(tmp_path):
    copy = tmp_path / BUNDLE.name
    shutil.copytree(BUNDLE, copy)
    (copy / "scenario").mkdir()
    (copy / "scenario/facts.yaml").write_text("zones: {}\n")
    (copy / "slack/leak.json").symlink_to(copy / "_internal/manifest.jsonl")
    integ = yaml.safe_load((copy / "integration.yaml").read_text())
    for key in ("_internal", "triggers", "scenario"):
        integ["sources"][key] = {"zone": "UTC", "marker": "none"}
    (copy / "integration.yaml").write_text(yaml.safe_dump(integ, sort_keys=False))
    bundle = store.Bundle(copy)
    for rel in ("_internal/manifest.jsonl", "triggers/1452.md", "scenario/facts.yaml", "slack/leak.json"):
        with pytest.raises(store.PathRefused):
            bundle.text(rel)
    with pytest.raises(store.PathRefused):
        bundle.files("slack")       # the link into _internal/ stops the whole source, loudly
    (copy / "slack/leak.json").unlink()
    built = store.build(copy)
    assert len(built.report.refused) == 3
    paths = [p for (p,) in built.conn.execute("SELECT DISTINCT path FROM record UNION SELECT DISTINCT path FROM point")]
    assert paths and not [p for p in paths if p.split("/")[0] in store.FORBIDDEN]


def test_the_loader_reads_only_listed_sources(tmp_path):
    copy = tmp_path / BUNDLE.name
    shutil.copytree(BUNDLE, copy)
    integ = yaml.safe_load((copy / "integration.yaml").read_text())
    del integ["sources"]["slack"]
    (copy / "integration.yaml").write_text(yaml.safe_dump(integ, sort_keys=False))
    built = store.build(copy)
    assert count(built.conn, "record") > 0
    assert built.conn.execute("SELECT count(*) FROM record WHERE path LIKE 'slack/%'").fetchone()[0] == 0
    assert built.conn.execute("SELECT count(*) FROM ref WHERE source = 'slack'").fetchone()[0] == 0


def _python_files(*parts):
    return sorted((ROOT.joinpath(*parts)).rglob("*.py"))


def test_nothing_under_smalls_imports_the_generator():
    offenders = []
    for path in _python_files("smalls"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            offenders += [f"{path.relative_to(ROOT)}: {n}" for n in names if n == "smalls_data" or n.startswith("smalls_data.")]
    assert not offenders


def test_no_tool_reads_the_base_tables():
    """Tools read the `visible` views. Only the store and the adapters may name `record`, `point` or `chunk`,
    and only the store may name `clock`: no tool sets the time it is asked about."""
    base_table = re.compile(r"\b(?:FROM|JOIN|INTO|UPDATE)\s+(?:record|point|chunk|clock)\b", re.I)
    exempt = {ROOT / "smalls/tools/store.py"}
    offenders = [p.relative_to(ROOT) for p in _python_files("smalls") if p not in exempt and base_table.search(p.read_text())]
    assert not offenders


def test_integration_clocks_and_daylight_saving_folds():
    integ = store.Bundle(BUNDLE).integration
    assert integ.parse("servicenow", "2026-10-13 10:33:41") == (base.ms_of(datetime.fromisoformat("2026-10-13T14:33:41+00:00")), False)
    assert integ.parse("splunk/raw/f5", "Oct 13 14:36:40")[0] == base.ms_of(datetime.fromisoformat("2026-10-13T14:36:40+00:00"))
    assert integ.parse("paycore", "2026-10-13T08:10:22.000-07:00")[0] == base.ms_of(datetime.fromisoformat("2026-10-13T15:10:22+00:00"))
    # 01:30 on 1 November 2026 happens twice in New York: the first reading is kept and the fold is flagged.
    ms, ambiguous = integ.parse("servicenow", "2026-11-01 01:30:00")
    assert ambiguous and ms == base.ms_of(datetime.fromisoformat("2026-11-01T05:30:00+00:00"))
    assert base.iso(ms) == "2026-11-01T05:30:00.000Z"


def test_json_spans_are_the_served_bytes():
    text = '{\n  "result": [\n    {"a": 1,\n     "b": "x, ]}"},\n    {"a": 2}\n  ],\n  "n": 2\n}\n'
    got = list(base.elements(text, "/result"))
    assert [(p, v) for p, _, v in got] == [("/result/0", {"a": 1, "b": "x, ]}"}), ("/result/1", {"a": 2})]
    assert got[0][1] == '{"a": 1,\n     "b": "x, ]}"}' and got[0][1] in text
    assert text[slice(*base.span(text, "/n"))] == "2"
    assert text[slice(*base.span(text, "/"))] == text.rstrip("\n")


def test_render_native_leaves_out_withheld_fields():
    native = json.dumps({"id": 1, "overall_state": "Alert", "series": [{"end": 9, "scope": "a"}, {"end": 9, "scope": "b"}]})
    assert store.render_native(native, "{}") == native
    shown = json.loads(store.render_native(native, json.dumps({"withheld": ["overall_state", "series[].end"]})))
    assert shown == {"id": 1, "series": [{"scope": "a"}, {"scope": "b"}]}
    assert base.withhold([{"members": [1], "name": "x"}], ("[].members",)) == [{"name": "x"}]


def test_tags_are_on_unless_turned_off(monkeypatch):
    monkeypatch.delenv("SMALLS_TAGS", raising=False)
    assert store.tags_enabled() is True
    monkeypatch.setenv("SMALLS_TAGS", "off")
    assert store.tags_enabled() is False
    monkeypatch.setenv("SMALLS_TAGS", "on")
    assert store.tags_enabled() is True
    monkeypatch.setenv("SMALLS_TAGS", "maybe")
    with pytest.raises(ValueError):
        store.tags_enabled()
