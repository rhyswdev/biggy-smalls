"""The tool surface as the model sees it (docs/decisions/tools-agent-build.md sections 5 and 6.5, K1; build
row A1): the eleven argument models, the registry's order and caps, the tag inputs built from the tagging
profiles, and the descriptions file covering every tool, parameter and object field."""
import json

import pytest
from pydantic import ValidationError

import parity
from smalls.agent import __main__ as cli
from smalls.agent import actions, internal_tools
from smalls.contracts.report import Rationale
from smalls.tools import descriptions, registry, schemas, store
from smalls.tools.ingest.tagging import cache, measure, profiles

ELEVEN = ["list_alerts", "get_record", "search_logs", "query_metrics", "list_changes", "itsm_query", "search_knowledge",
          "get_topology", "get_oncall", "read_chat", "check_status_page"]
WINDOWED = ["list_alerts", "search_logs", "query_metrics", "list_changes", "read_chat"]


def objects(node, path="$"):
    """Every object schema in a JSON Schema, with where it is."""
    if isinstance(node, dict):
        if node.get("type") == "object":
            yield path, node
        for key, value in node.items():
            yield from objects(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from objects(item, f"{path}[{i}]")


def property_names(node):
    if isinstance(node, dict):
        yield from node.get("properties", {})
        for value in node.values():
            yield from property_names(value)
    elif isinstance(node, list):
        for item in node:
            yield from property_names(item)


@pytest.fixture(scope="module", params=[True, False], ids=["tags-on", "tags-off"])
def defs(request):
    return {d["name"]: d for d in cli.definitions(tags=request.param)}


def test_the_surface_is_the_eleven_tools_in_a_fixed_order_with_plans_caps():
    for tags in (True, False):
        surface = registry.surface(tags=tags)
        assert list(surface) == ELEVEN
        assert all(spec.kind == "read" and not spec.mock for spec in surface.values())
        assert {name: (spec.cap_tokens, spec.cap_rows) for name, spec in surface.items()} == registry.CAPS
    assert registry.CAPS["get_record"] == (3000, None) and registry.CAPS["search_logs"][0] == 4000
    assert registry.CAPS["list_alerts"] == (6000, 40) and registry.CAPS["read_chat"] == (6000, 60)


def test_a_tool_that_is_not_built_is_absent_from_the_registry():
    for tags in (True, False):
        built = registry.registry(tags=tags)
        assert list(built) == [name for name in ELEVEN if name in registry.BUILT]


def test_the_toolset_adds_the_action_tool_and_the_internal_tools_last():
    full = internal_tools.toolset(registry.surface(tags=True))
    assert list(full) == ELEVEN + ["propose_action", "update_board", "submit_report"]
    assert full["propose_action"].kind == "action" and full["propose_action"].mock is True
    assert {full[n].kind for n in ("update_board", "submit_report")} == {"internal"}


def test_every_object_is_closed_and_none_has_open_keys(defs):
    for name, d in defs.items():
        for where, node in objects(d["parameters"]):
            assert node.get("additionalProperties") is False, (name, where)
            assert "properties" in node, (name, where)       # a free-form input is a list of pairs, never a map
            assert "patternProperties" not in node


def test_now_is_never_a_parameter(defs):
    for name, d in defs.items():
        assert "now" not in set(property_names(d["parameters"])), name


def test_every_gated_tool_requires_a_rationale_and_internal_tools_carry_none(defs):
    for name, d in defs.items():
        props, required = d["parameters"]["properties"], d["parameters"]["required"]
        if name in ("update_board", "submit_report", "submit_verdict"):
            assert "rationale" not in props
            continue
        assert next(iter(props)) == "rationale" and required[0] == "rationale"
        assert d["parameters"]["$defs"]["Rationale"]["required"] == ["tests", "expect"]
    assert set(Rationale.model_fields) == {"tests", "expect"}


def test_the_rationale_is_not_one_of_a_tools_own_arguments():
    for spec in internal_tools.toolset(registry.surface(tags=True)).values():
        assert "rationale" not in spec.args.model_fields


def test_optional_inputs_are_left_out_not_sent_as_null(defs):
    for name, d in defs.items():
        text = json.dumps(d["parameters"])
        assert '"null"' not in text and '"anyOf"' not in text, name


def test_the_argument_conventions_of_k1(defs):
    for name in WINDOWED:
        params = defs[name]["parameters"]
        assert "since" in params["required"] and "until" not in params["required"], name
    p = {name: d["parameters"]["properties"] for name, d in defs.items()}
    assert p["search_logs"]["terms"]["maxItems"] == 8 and p["search_logs"]["bucket"] == {"default": "1m", "enum": ["1m", "5m"], "type": "string", "description": "PLACEHOLDER"}
    assert (p["search_logs"]["limit"]["minimum"], p["search_logs"]["limit"]["maximum"], p["search_logs"]["limit"]["default"]) == (1, 50, 30)
    assert p["search_logs"]["group_by"]["enum"][:4] == ["minute", "host", "level", "template"]
    assert p["list_alerts"]["state"]["enum"] == ["active", "all"] and p["list_alerts"]["state"]["default"] == "all"
    assert p["itsm_query"]["table"]["enum"] == ["incident", "journal", "user", "group"]
    assert p["itsm_query"]["filters"]["maxItems"] == 6 and p["itsm_query"]["fields"]["maxItems"] == 12
    assert (p["itsm_query"]["limit"]["maximum"], p["itsm_query"]["limit"]["default"]) == (20, 20)
    assert (p["search_knowledge"]["query"]["minLength"], p["search_knowledge"]["query"]["maxLength"]) == (1, 300)
    assert (p["search_knowledge"]["k"]["maximum"], p["search_knowledge"]["k"]["default"]) == (5, 5)
    assert p["get_topology"]["hops"]["enum"] == [1, 2] and p["get_topology"]["hops"]["default"] == 1
    assert defs["get_oncall"]["parameters"]["required"] == ["rationale"]
    assert p["get_record"]["part"]["minimum"] == 1 and p["get_record"]["part"]["default"] == 1
    assert p["query_metrics"]["rollup"]["enum"] == ["avg", "max", "min", "sum"]
    assert set(defs["query_metrics"]["parameters"]["required"]) == {"rationale", "metric", "scope", "since"}
    assert defs["check_status_page"]["parameters"]["required"] == ["rationale"]
    # A value that comes from the bundle is a free string, never an enum.
    for tool, names in {"search_logs": ["sourcetype", "host"], "read_chat": ["channel"], "check_status_page": ["provider"],
                        "get_oncall": ["service", "policy"], "get_topology": ["entity"], "query_metrics": ["metric", "scope"],
                        "list_alerts": ["source"]}.items():
        for n in names:
            assert "enum" not in p[tool][n] and p[tool][n]["type"] == "string", (tool, n)


def test_a_free_form_input_is_a_list_of_name_and_value_pairs(defs):
    for tool, param in (("itsm_query", "filters"), ("propose_action", "params")):
        node = defs[tool]["parameters"]
        assert node["properties"][param]["items"] == {"$ref": "#/$defs/Pair"}
        assert node["$defs"]["Pair"]["required"] == ["name", "value"]
        assert {v["type"] for v in node["$defs"]["Pair"]["properties"].values()} == {"string"}


@pytest.mark.parametrize("model,bad", [
    (schemas.SearchLogsArgs, {"since": "-1h", "terms": ["a"] * 9}),
    (schemas.SearchLogsArgs, {"since": "-1h", "limit": 51}),
    (schemas.SearchLogsArgs, {"since": "-1h", "limit": 0}),
    (schemas.SearchLogsArgs, {"since": "-1h", "group_by": "signal"}),       # tags off: the grouping does not exist
    (schemas.SearchLogsArgs, {"terms": ["a"]}),                             # since is required
    (schemas.SearchLogsArgs, {"since": "-1h", "now": "2026-01-01T00:00:00Z"}),
    (schemas.SearchLogsArgs, {"since": "-1h", "signal": "errors"}),
    (schemas.SearchKnowledgeArgs, {"query": ""}),
    (schemas.SearchKnowledgeArgs, {"query": "x" * 301}),
    (schemas.SearchKnowledgeArgs, {"query": "x", "k": 6}),
    (schemas.ItsmQueryArgs, {"table": "change"}),
    (schemas.ItsmQueryArgs, {"table": "incident", "filters": [{"name": "a", "value": "b"}] * 7}),
    (schemas.ItsmQueryArgs, {"table": "incident", "filters": {"state": "New"}}),
    (schemas.ItsmQueryArgs, {"table": "incident", "limit": 21}),
    (schemas.GetTopologyArgs, {"entity": "x", "hops": 3}),
    (schemas.GetRecordArgs, {"handle": "conf:X", "part": 0}),
    (schemas.ListAlertsArgs, {"since": "-1h", "state": "open"}),
    (schemas.QueryMetricsArgs, {"metric": "m", "scope": "", "since": "-1h", "rollup": "p95"}),
])
def test_arguments_outside_the_conventions_are_refused(model, bad):
    with pytest.raises(ValidationError):
        model.model_validate(bad)


def test_defaults_are_filled():
    logs = schemas.SearchLogsArgs.model_validate({"since": "-30m"})
    assert (logs.terms, logs.until, logs.group_by, logs.bucket, logs.limit) == ([], None, None, "1m", 30)
    assert schemas.ListAlertsArgs.model_validate({"since": "-2h"}).state == "all"
    assert schemas.GetTopologyArgs.model_validate({"entity": "x"}).hops == 1
    assert schemas.GetRecordArgs.model_validate({"handle": "conf:X"}).part == 1
    oncall = schemas.GetOncallArgs.model_validate({})
    assert oncall.service is None and oncall.policy is None


# -- tags ------------------------------------------------------------------------------------------------


def test_tag_inputs_exist_only_with_tags_on_and_take_their_values_from_the_profiles():
    on, off = schemas.args_models(tags=True), schemas.args_models(tags=False)
    assert list(on) == list(off) == ELEVEN
    facets = profiles.facets()
    for tool, facet in schemas.TAG_INPUTS.items():
        assert facet not in off[tool].model_fields
        values = on[tool].model_fields[facet].annotation.__args__[0].__args__
        assert values == (*facets[facet], "untagged")
        assert "other" not in values
        assert on[tool].model_validate({**_minimal(tool), facet: "untagged"})
        with pytest.raises(ValidationError):
            on[tool].model_validate({**_minimal(tool), facet: "other"})
    assert "automated" in schemas.tag_values("act") and "automated" not in schemas.tag_values("signal")
    for tool in set(ELEVEN) - set(schemas.TAG_INPUTS):
        assert on[tool] is off[tool], tool           # no tag input: `query_metrics`, `get_record` and the rest
    assert on["search_logs"].model_validate({"since": "-1h", "group_by": "signal"}).group_by == "signal"
    assert on["search_logs"].__name__ == off["search_logs"].__name__


def _minimal(tool: str) -> dict:
    return {"table": "incident"} if tool == "itsm_query" else {"since": "-1h"}


def test_a_dropped_facet_takes_its_input_its_grouping_and_its_values_with_it(monkeypatch):
    monkeypatch.setattr(schemas, "NOT_OFFERED", frozenset({"signal"}))
    on = schemas.args_models(tags=True)
    assert "signal" not in on["search_logs"].model_fields and "signal" not in on["list_alerts"].model_fields
    assert on["search_logs"] is schemas.SearchLogsArgs
    assert "act" in on["read_chat"].model_fields and "change_kind" in on["list_changes"].model_fields
    assert "signal" not in schemas.offered_facets()


def test_the_facets_not_offered_are_the_ones_the_labelled_sample_fails():
    """A facet that cannot meet the precision bar at any floor is stored but not offered. Today none fails."""
    _, records, points, _ = store.read(parity.BUNDLE)
    units = measure.units_of(records, points)
    rows = measure.outcomes(measure.load_labels(), cache.read(cache.DEFAULT_CACHE), measure.load_repeats(), units)
    _, hopeless = measure.final_floor(rows)
    assert set(hopeless) == set(schemas.NOT_OFFERED) == set()


# -- descriptions ----------------------------------------------------------------------------------------


def test_the_descriptions_file_covers_the_surface_exactly():
    assert descriptions.problems(descriptions.load(), cli.skeleton()) == []


def test_a_descriptions_file_with_a_gap_or_a_stray_text_is_caught():
    want = cli.skeleton()
    texts = json.loads(json.dumps(want))
    del texts["tools"]["get_record"]["params"]["part"]
    texts["objects"]["Pair"]["colour"] = "x"
    texts["tools"]["read_chat"]["description"] = "  "
    texts["action_kinds"] = {"Roll Back": "x"}
    found = descriptions.problems(texts, want)
    assert "missing: tools.get_record.params.part" in found and "unknown: objects.Pair.colour" in found
    assert "empty: tools.read_chat.description" in found and any(f.startswith("action_kinds:") for f in found)


def test_every_text_reaches_its_own_place_in_the_schema():
    """Fill the file with each text's own path, then find every path on the property it describes."""
    texts = cli.skeleton()

    def fill(node, prefix=""):
        for key in node:
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(node[key], dict):
                fill(node[key], path)
            else:
                node[key] = path
    fill(texts)
    for d in cli.definitions(tags=True, texts=texts):
        tool = d["name"]
        assert d["description"] == f"tools.{tool}.description"
        for name, prop in d["parameters"]["properties"].items():
            assert prop["description"] == ("rationale" if name == "rationale" else f"tools.{tool}.params.{name}")
        for cls, node in d["parameters"].get("$defs", {}).items():
            for field, prop in node["properties"].items():
                assert prop["description"].startswith("objects.") and prop["description"].endswith(f".{field}"), (tool, cls, field)
    # A field a model inherits is described once, under the model that declares it.
    board = next(d for d in cli.definitions(tags=True, texts=texts) if d["name"] == "update_board")
    hyp = board["parameters"]["$defs"]["HypothesisUpdate"]["properties"]
    assert hyp["statement"]["description"] == "objects.HypothesisFields.statement"
    assert hyp["because"]["description"] == "objects.HypothesisUpdate.because"


def test_every_property_the_model_sees_has_a_description(defs):
    def undescribed(node, path):
        for name, prop in node.get("properties", {}).items():
            if not prop.get("description"):
                yield f"{path}.{name}"
    for name, d in defs.items():
        assert d["description"]
        assert not list(undescribed(d["parameters"], name))
        for cls, node in d["parameters"].get("$defs", {}).items():
            assert not list(undescribed(node, f"{name}.{cls}"))


def test_until_the_blind_pass_every_text_is_a_placeholder():
    """This flips when the blind pass lands: then no placeholder may remain."""
    left = descriptions.placeholders()
    total = len(list(descriptions.paths(descriptions.load())))
    assert len(left) in (0, total)


def test_action_kinds_come_from_the_descriptions_file():
    kinds = descriptions.action_kinds()
    assert actions.args_model().model_fields["kind"].annotation.__args__ == kinds
    custom = actions.args_model(("restart", "rollback"))
    assert custom.model_validate({"kind": "rollback", "target": "x", "evidence": []}).params == []
    with pytest.raises(ValidationError):
        custom.model_validate({"kind": "reboot", "target": "x", "evidence": []})
