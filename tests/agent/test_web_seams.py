"""The seams the web UI needs from the agent (docs/decisions/ui-build.md section 8), each over the fixture
registry with the scripted model: S1 (decisions from the web are recorded as such) and the ones after it."""

from __future__ import annotations

from pathlib import Path

import pytest

import fixture_registry as fx
import fixture_script
from smalls.agent.config import load_config
from smalls.agent.runner import Runner, module_toolbox
from smalls.contracts.report import Report


@pytest.fixture
def cfg(tmp_path: Path):
    return load_config(runs_dir=tmp_path)


def runner(cfg, run_id: str, **kwargs) -> Runner:
    kwargs.setdefault("script", fixture_script.turn)
    return Runner(cfg, module_toolbox("fixture_registry"), run_id, **kwargs)


def approve(cards):
    return {c["call_id"]: {"approve": True} for c in cards}


# -- S1: via web -----------------------------------------------------------------------------------------------

def test_s1_a_decision_made_on_the_web_is_recorded_as_one(cfg):
    r1 = runner(cfg, "web", stop_at_gate=True, attended=True, via="web")
    assert r1.start(fx.TRIGGER, fx.NOW)["status"] == "paused"
    r1.close()
    r2 = runner(cfg, "web", attended=True, via="web")
    pending = r2.state()["pending"]["calls"]
    assert r2.resume({c["call_id"]: {"approve": False, "reason": "not tonight"} for c in pending})["status"] == "completed"
    state = r2.state()
    r2.close()
    people = [g for g in state["gate_log"] if g["by"]["who"] == "human"]
    assert people and all(g["by"]["via"] == "web" for g in people)
    report = Report.model_validate(state["report"])
    assert report.proposed_actions[0].by.via == "web" and report.proposed_actions[0].decision == "rejected"
    assert all(g["by"]["via"] == "web" for g in state["gate_log"] if g["by"]["who"] == "auto")     # the switch was the web's too


def test_s1_the_terminal_and_unattended_runs_keep_their_own_via(cfg):
    attended = runner(cfg, "cli", attended=True, decider=approve)
    attended.start(fx.TRIGGER, fx.NOW)
    state = attended.state()
    attended.close()
    assert {g["by"]["via"] for g in state["gate_log"] if g["by"]["who"] == "human"} == {"cli"}
    unattended = runner(cfg, "unattended")
    unattended.start(fx.TRIGGER, fx.NOW)
    state = unattended.state()
    unattended.close()
    assert {g["by"]["via"] for g in state["gate_log"]} == {"headless"}
