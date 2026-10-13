"""The compose and CLI skeleton: the command exists, and no secret can reach an image."""
import re
from pathlib import Path

import pytest

from smalls import cli

ROOT = Path(__file__).resolve().parents[1]


def test_cli_names_its_commands(capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    assert "ingest" in out and "tag" in out


def test_cli_refuses_a_folder_that_is_not_a_bundle(tmp_path):
    with pytest.raises(SystemExit, match="not a bundle"):
        cli.main(["ingest", "--bundle", str(tmp_path)])


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
    assert "required: false" in compose and "environment:" not in compose
