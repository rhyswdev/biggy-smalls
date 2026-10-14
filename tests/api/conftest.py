"""The API's tests run the real app with the test client, over the agent track's fixture registry and the
scripted model: no key, no network, nothing on disk."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import parity
from smalls.api.app import create_app
from smalls.api.settings import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    runs = tmp_path / "committed"
    runs.mkdir()
    return Settings(bundle=parity.BUNDLE, checkpoint=os.environ.get("SMALLS_CHECKPOINT", "1452"), mode="scripted", runs_dir=runs,
                    registry="fixture_registry", script="fixture_script:turn")


@pytest.fixture
def client(settings: Settings):
    from fastapi.testclient import TestClient

    with TestClient(create_app(settings)) as c:
        yield c
