"""Models by role, from config (docs/decisions/eval-loop.md, V5): sets, overrides, efforts and what a run
records. Nothing here builds a live model."""

from __future__ import annotations

import tomllib

import pytest

from eval.matrix import command
from smalls.agent.__main__ import saved_roles
from smalls.agent.config import EFFORTS, PATH, ROLES, _spec, load_config, resolve_roles

RAW = tomllib.loads(PATH.read_text(encoding="utf-8"))
PROFILES = {n: _spec(n, p) for n, p in RAW["profiles"].items()}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("SMALLS_SET", "SMALLS_ROLES", "SMALLS_PROFILE", "SMALLS_EFFORT", "SMALLS_MODEL"):
        monkeypatch.delenv(name, raising=False)


def resolve(**kwargs):
    return resolve_roles(RAW, PROFILES, **kwargs)


def picked(roles):
    return {name: (spec.profile, spec.effort) if spec else None for name, spec in roles.items()}


def test_every_set_in_the_config_names_every_role_with_a_known_profile_and_effort():
    assert RAW["sets"] and RAW["default_set"] in RAW["sets"]
    for name in RAW["sets"]:
        used, roles = resolve(set_name=name)
        assert used == name and set(roles) == set(ROLES) and roles["investigator"] is not None
        assert all(spec is None or spec.effort in (None, *EFFORTS) for spec in roles.values())


def test_the_last_one_given_wins_default_then_env_then_argument_then_role_overrides(monkeypatch):
    default, roles = resolve()
    assert default == RAW["default_set"]
    monkeypatch.setenv("SMALLS_SET", "claude")
    assert resolve()[0] == "claude"
    assert resolve(set_name="sol")[0] == "sol"                                       # the argument beats the environment
    monkeypatch.setenv("SMALLS_ROLES", "claims=anthropic-small@low, contrarian=openai@high")
    _, roles = resolve(set_name="sol", roles=["contrarian=anthropic"])
    got = picked(roles)
    assert got["claims"] == ("anthropic-small", "low")                               # the environment's override
    assert got["contrarian"][0] == "anthropic"                                       # the argument's override wins over it
    _, roles = resolve(set_name="sol", profile="anthropic", effort="high")
    assert picked(roles)["investigator"] == ("anthropic", "high")                    # --profile and --effort reach the investigator


def test_an_effort_is_sent_where_its_provider_reads_it():
    _, roles = resolve(roles={"investigator": "openai@xhigh", "contrarian": "anthropic@low"})
    assert roles["investigator"].params["reasoning"]["effort"] == "xhigh" and "effort" not in roles["investigator"].params
    assert roles["contrarian"].params["effort"] == "low"
    assert PROFILES["openai"].params["reasoning"]["effort"] == "medium"               # the profile itself is not changed


@pytest.mark.parametrize("kwargs, message", [
    ({"set_name": "no-such"}, "no set named"),
    ({"roles": ["judge=openai"]}, "is not role=profile"),
    ({"roles": ["claims"]}, "is not role=profile"),
    ({"roles": ["claims=no-such"]}, "names no profile"),
    ({"roles": ["claims=openai@turbo"]}, "no effort named"),
    ({"effort": "turbo"}, "no effort named"),
])
def test_a_bad_set_or_override_is_refused_before_any_model_is_built(kwargs, message):
    with pytest.raises(SystemExit, match=message):
        resolve(**kwargs)


def test_a_set_that_misses_a_role_is_refused():
    raw = {**RAW, "sets": {"thin": {"investigator": "openai"}}, "default_set": "thin"}
    with pytest.raises(SystemExit, match="must name every role"):
        resolve_roles(raw, PROFILES)


def test_an_empty_role_cuts_that_pass_and_an_empty_investigator_is_refused():
    _, roles = resolve(roles={"contrarian": ""})
    assert roles["contrarian"] is None
    with pytest.raises(SystemExit, match="investigator role must name a profile"):
        resolve(roles={"investigator": ""})


def test_under_pytest_every_role_is_scripted_and_the_record_names_the_set_and_roles():
    cfg = load_config(set_name="claude")
    assert all(spec is None or spec.provider == "scripted" for spec in cfg.roles.values())
    public = cfg.public()
    assert public["set"] == "claude" and set(public["roles"]) == set(ROLES)


def test_a_resume_is_loaded_with_the_roles_its_record_ran_on():
    saved = {"profile": "openai", "set": "sol", "roles": {"investigator": {"profile": "openai", "effort": "high"},
                                                         "contrarian": None, "claims": {"profile": "openai", "effort": "low"}}}
    assert saved_roles(saved, None) == {"set_name": "sol", "roles": {"investigator": "openai@high", "contrarian": "", "claims": "openai@low"}}
    assert "investigator" not in saved_roles(saved, "anthropic")["roles"]           # --profile on resume wins
    assert saved_roles({"profile": "anthropic"}, None) == {"roles": {"investigator": "anthropic"}}   # a record from before sets


def test_the_matrix_passes_the_set_the_overrides_and_the_effort_to_every_run():
    cell = {"arm": "C", "checkpoint": "1452", "tags": True, "rep": 1}
    out = command(cell, label="t", bundle="b", set_name="claude", roles=["claims=openai@low"], effort="high")
    assert out[out.index("--set") + 1] == "claude" and out[out.index("--role") + 1] == "claims=openai@low"
    assert out[out.index("--effort") + 1] == "high"


def test_the_second_providers_strict_schemas_put_no_keyword_beside_a_reference():
    """Its strict mode refuses a `$ref` with a sibling keyword (found on its first call), so the adapter moves
    the reference into an anyOf and keeps the description."""
    from smalls.agent.__main__ import definitions
    from smalls.agent.models import provider_tools

    def refs(node):
        if isinstance(node, dict):
            if "$ref" in node:
                yield node
            for value in node.values():
                yield from refs(value)
        elif isinstance(node, list):
            for item in node:
                yield from refs(item)

    for tags in (True, False):
        tools = provider_tools(definitions(tags=tags), "openai", strict=True)
        assert all(set(r) == {"$ref"} for t in tools for r in refs(t["function"]["parameters"]))
