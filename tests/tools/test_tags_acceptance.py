"""Tags, across every tool (build row D9; section 6.3 of docs/decisions/tools-agent-build.md). With tags on,
each rule of 6.3 holds for every tool; with no tag input used, a tags-on result with its labels and its
tag block removed equals the tags-off result; with tags off no tool mentions a tag. The leak test with
tags on is `tests/test_leak_tools.py`, which runs every call here at both settings."""
import json
import re

import pytest

import golden
import test_leak_tools
from smalls.agent import graph, record
from smalls.tools import inventory, registry, schemas
from smalls.tools.ingest.tagging import profiles

CHECKPOINTS = list(golden.CHECKPOINTS)
WIDEST = test_leak_tools.WIDEST
LABEL = re.compile(r" \[tag ([a-z_]+)=([a-z_,]+)\]")
FOOTER = re.compile(r"\[tag filter ([a-z_]+)=([a-z_]+): (\d+) of the (\d+) [a-z ]+ that match without it\. (\d+) of those (\d+) carry no ([a-z_]+) tag, "
                    r"and a missing tag says nothing about an? [a-z ]+\]")
# The five tag inputs: the tool, its facet, and the widest call with no other filter.
INPUTS = [("search_logs", "signal", {"since": WIDEST, "limit": 50}), ("list_alerts", "signal", {"since": WIDEST}),
          ("list_changes", "change_kind", {"since": WIDEST}), ("read_chat", "act", {"since": WIDEST}), ("itsm_query", "signal", {"table": "incident"})]


def strip(text: str) -> str:
    return LABEL.sub("", text)


def test_the_tag_inputs_follow_the_profiles_and_exist_only_with_tags_on():
    facets = profiles.facets()
    assert set(schemas.offered_facets()) == set(facets) == {"signal", "act", "change_kind"} and schemas.NOT_OFFERED == frozenset()
    on, off = schemas.args_models(tags=True), schemas.args_models(tags=False)
    assert {name: facet for name, facet, _ in INPUTS} == schemas.TAG_INPUTS and len(on) == len(off) == 11
    for name in on:
        fields, plain = on[name].model_json_schema()["properties"], off[name].model_json_schema()["properties"]
        facet = schemas.TAG_INPUTS.get(name)
        assert set(fields) - set(plain) == ({facet} if facet else set()), name                   # one input, on its own tool, and no other
        assert not set(plain) & set(facets), name
        if facet:
            values = next(option["enum"] for option in fields[facet]["anyOf"] if "enum" in option)
            assert values == [*facets[facet], "untagged"] and "other" not in values             # the facet's options, and `untagged`
    assert "automated" in facets["act"]
    grouping = on["search_logs"].model_json_schema()["properties"]["group_by"]
    assert "signal" in json.dumps(grouping) and "signal" not in json.dumps(off["search_logs"].model_json_schema()["properties"]["group_by"])
    assert "signal" not in on["query_metrics"].model_fields and "signal" not in on["get_record"].model_fields


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_with_no_tag_input_a_tags_on_result_minus_its_labels_is_the_tags_off_result(checkpoint):
    off, on = golden.ctx(checkpoint, tags=False), golden.ctx(checkpoint, tags=True)
    calls = test_leak_tools.list_calls(off)                                         # the call set with no tag input in it
    assert set(calls) == set(registry.registry(tags=True)) and len(calls) == 11
    labelled = set()
    for name, variants in calls.items():
        for args in variants:
            assert not set(args) & set(profiles.facets()), (name, args)
            plain, tagged = golden.run(name, off, **args), golden.run(name, on, **args)
            what = f"{name} {json.dumps(args)}"
            assert "[tag " not in plain.text and "tag" not in json.dumps(dict(plain.meta)).replace('"tag": null', ""), what
            assert strip(tagged.text) == plain.text, what
            assert golden.handles(tagged) == golden.handles(plain) and tagged.referenced == plain.referenced and tagged.meta["tag"] is None, what
            assert dict(tagged.meta) == dict(plain.meta), what
            if LABEL.search(tagged.text):
                labelled.add(name)
    # The tools that print a label are the ones whose rows are of a kind a facet applies to.
    assert labelled == {"search_logs", "list_alerts", "list_changes", "read_chat", "itsm_query", "query_metrics"}
    handles = [h for (h,) in off.store.conn.execute("SELECT handle FROM visible WHERE kind != 'log' ORDER BY rid")]
    handles += [h for (h,) in off.store.conn.execute("SELECT handle FROM visible WHERE kind = 'log' AND rid % 23 = 0 ORDER BY rid")]
    seen = 0
    for handle in handles:
        plain, tagged = golden.run("get_record", off, handle=handle), golden.run("get_record", on, handle=handle)
        assert "[tag " not in plain.text and strip(tagged.text) == plain.text and tagged.referenced == plain.referenced, handle
        seen += bool(LABEL.search(tagged.text))
    assert len(handles) > 300 and seen > 100
    listed_off, listed_on = inventory.build(off), inventory.build(on)
    assert "tag" not in listed_off.text.lower() and strip(listed_on.text.split("Tags, a search aid")[0]).rstrip("\n") == listed_off.text


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_label_has_one_form_and_holds_only_values_of_its_facet(checkpoint):
    on = golden.ctx(checkpoint, tags=True)
    facets = profiles.facets()
    found: dict[str, set[str]] = {}
    for name, variants in test_leak_tools.list_calls(on).items():
        for args in variants:
            env = golden.run(name, on, **args)
            for facet, values in LABEL.findall(env.text):
                assert set(values.split(",")) <= set(facets[facet]) and "untagged" not in values and "other" not in values, (name, facet, values)
                assert len(values.split(",")) == len(set(values.split(","))), (name, values)
                found.setdefault(facet, set()).update(values.split(","))
            assert len(re.findall(r"\[tag (?!filter )", env.text)) == len(LABEL.findall(env.text)), name      # no other form of label exists
    assert set(found) == set(facets) and any("," in v for name, variants in test_leak_tools.list_calls(on).items() for args in variants
                                              for _, v in LABEL.findall(golden.run(name, on, **args).text))          # a row can carry two values


@pytest.mark.parametrize("name,facet,base", INPUTS, ids=[name for name, _, _ in INPUTS])
@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_tag_filter_matches_rows_that_carry_the_value_and_ends_with_what_it_left_out(checkpoint, name, facet, base):
    on = golden.ctx(checkpoint, tags=True)
    whole = golden.run(name, on, **base)
    total = whole.meta["total"]
    nonempty = empty = 0
    for value in schemas.tag_values(facet):
        env = golden.run(name, on, **base, **{facet: value})
        what = f"{name} {facet}={value}"
        assert env.status == "ok" and env.meta["tag"] == f"{facet}={value}", what
        footer = FOOTER.search(env.text)
        assert footer, what                                                         # also when nothing matched: "nothing tagged" is not "nothing there"
        assert (footer[1], footer[2], footer[7]) == (facet, value, facet) and int(footer[3]) == env.meta["total"], what
        assert int(footer[4]) == int(footer[6]) == total, what                      # counted against the same call without the filter
        untagged = int(footer[5])
        for row in env.rows:
            mine = [values.split(",") for f, values in LABEL.findall(row.text) if f == facet]
            if value == "untagged":
                assert not mine, (what, row.handle)                                 # carries no value for the facet
            else:
                assert mine and all(value in values for values in mine[:1]), (what, row.handle)
        if value == "untagged":
            assert env.meta["total"] == untagged, what                              # the untagged filter returns exactly the rows the footers count
        nonempty += bool(env.rows)
        empty += env.meta["empty"]
        if env.meta["empty"]:
            assert env.text.startswith("0 rows. ") and "the tag filter" in env.text.splitlines()[0], what
    assert nonempty >= 2
    # Every row that matches without the filter is under exactly the values it carries, or under `untagged`: none is lost.
    reached = sum(golden.run(name, on, **base, **{facet: value}).meta["total"] for value in schemas.tag_values(facet))
    assert reached >= total
    assert golden.run(name, on, **base, **{facet: "other"}).meta["error"] == "arguments"
    assert golden.run(name, golden.ctx(checkpoint, tags=False), **base, **{facet: "untagged"}).meta["error"] == "arguments"


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_a_count_that_depends_on_a_tag_mints_no_handle_and_untagged_rows_are_never_folded_away(checkpoint):
    on = golden.ctx(checkpoint, tags=True)
    by_signal = golden.run("search_logs", on, since=WIDEST, group_by="signal")
    filtered = golden.run("search_logs", on, since=WIDEST, signal="errors", group_by="minute")
    for env in (by_signal, filtered):
        assert env.rows == () and "q:" not in env.text and "[not citable: this count depends on a tag" in env.text and env.meta["tag"]
    assert golden.run("search_logs", on, since=WIDEST, group_by="minute").shown[0].startswith("q:")      # the same count with no tag is citable
    table = {ln.split("  ")[0]: int(ln.split("  ")[1]) for ln in by_signal.text.split("group  records")[1].splitlines()[1:] if not ln.startswith("[")}
    tags = [json.loads(t).get("signal", []) for (t,) in on.store.conn.execute("SELECT tags FROM visible WHERE kind = 'log'")]
    assert table["untagged"] == sum(1 for values in tags if not values) > 0 and "other" not in " ".join(table)
    assert all(table.get(value, 0) == sum(1 for values in tags if value in values) for value in profiles.facets()["signal"])
    assert sum(table.values()) >= len(tags) and f"more than the {len(tags)} records matched" in by_signal.text
    # The rows themselves stay citable however they were found.
    rows = golden.run("search_logs", on, since=WIDEST, signal="errors", limit=5)
    assert rows.shown and all(h.startswith("log:") for h in rows.shown)


def test_the_sweep_uses_no_tag_filter_and_a_run_records_whether_a_call_used_one():
    source = open(graph.__file__, encoding="utf-8").read()
    sweep = source[source.index("def sweep"):source.index("def sweep") + 2500]
    assert not re.search(r"['\"](signal|act|change_kind)['\"]\s*:", sweep) and "used_tag_input" in source      # no tag input in the fixed opening
    on = golden.ctx(tags=True)
    for name, facet, base in INPUTS:
        assert golden.run(name, on, **base).meta["tag"] is None and golden.run(name, on, **base, **{facet: "untagged"}).meta["tag"] == f"{facet}=untagged"
    assert "used_tag_input" in open(record.__file__, encoding="utf-8").read()       # a column of calls.jsonl
