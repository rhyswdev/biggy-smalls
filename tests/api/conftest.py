"""The API's tests run the real app with the test client, over the agent track's fixture registry and the
scripted model: no key, no network, nothing on disk."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# The agent track's fixture registry and script live beside its tests; the API's tests run the same ones.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

import fixture_registry as fx  # noqa: E402
import parity  # noqa: E402
from smalls.api.app import create_app  # noqa: E402
from smalls.api.settings import Settings  # noqa: E402


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    runs = tmp_path / "committed"
    runs.mkdir()
    return Settings(bundle=parity.BUNDLE, checkpoint=os.environ.get("SMALLS_CHECKPOINT", "1452"), mode="scripted", runs_dir=runs,
                    registry="fixture_registry", script="fixture_script:turn", clock=fx.NOW.isoformat())


@pytest.fixture
def client(settings: Settings):
    from fastapi.testclient import TestClient

    with TestClient(create_app(settings)) as c:
        yield c
