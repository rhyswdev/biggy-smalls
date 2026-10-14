"""The compose and CLI skeleton: the command exists, and no secret can reach an image."""
import re
from pathlib import Path

import pytest
import yaml

from smalls import cli

ROOT = Path(__file__).resolve().parents[1]


def test_cli_names_its_commands(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "ingest" in out and "tag" in out
    assert "run" in out and "resume" in out and "replay" in out      # the agent's commands, under the same name


def test_cli_refuses_a_folder_that_is_not_a_bundle(tmp_path):
    with pytest.raises(SystemExit, match="not a bundle"):
        cli.main(["ingest", "--bundle", str(tmp_path)])


def test_tools_call_takes_the_rationale_the_schema_lists_and_checks_it(capsys):
    # `tools schema` marks `rationale` required on every gated tool; the CLI checks it as the graph does and drops it.
    bundle = str(ROOT / "bundles/larkspur-checkout-2026-10-13")
    code = cli.main(["tools", "call", "get_oncall", 'service=Checkout - Prod', 'rationale={"tests":"x","expect":"y"}',
                     "--now", "14:52", "--bundle", bundle])
    assert code == 0 and "--- status: ok" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="rationale: an object with `tests` and `expect`"):
        cli.main(["tools", "call", "get_oncall", "service=Checkout - Prod", 'rationale={"tests":"x"}', "--now", "14:52", "--bundle", bundle])


def test_script_entry_is_declared():
    assert 'smalls = "smalls.cli:main"' in (ROOT / "pyproject.toml").read_text()


def test_no_secret_can_reach_the_image():
    ignore = (ROOT / ".dockerignore").read_text().splitlines()
    assert ".env" in ignore and ".env.*" in ignore
    # The allow rules come first, so nothing after the secret rules can re-admit them.
    assert not any(line.startswith("!") for line in ignore[ignore.index(".env"):])
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert not re.search(r"^\s*ARG\b", dockerfile, re.M)
    assert not re.search(r"(KEY|TOKEN|SECRET|PASSWORD)", "\n".join(x for x in dockerfile.splitlines() if not x.startswith("#")))
    assert not re.search(r"^\s*COPY\s+(\.\s|\.env)", dockerfile, re.M)
    compose = (ROOT / "compose.yaml").read_text()
    assert "required: false" in compose
    # The environment block passes the agent's settings through by name. It names no key, and the only
    # value it sets is the mark that says a run was started through compose.
    passed = yaml.safe_load(compose)["services"]["smalls"]["environment"]
    assert [item for item in passed if "=" in item] == ["SMALLS_RUNNER=compose"]
    assert all(item.startswith("SMALLS_") and not re.search(r"(KEY|TOKEN|SECRET|PASSWORD)", item) for item in passed)
