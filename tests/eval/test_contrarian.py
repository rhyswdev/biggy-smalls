"""The contrarian control, arm K (row Z5 of docs/decisions/eval-loop.md section 15): the frozen investigator
prompt with the rules after it, in this process only; every other prompt as it is."""
import sys

import pytest

from eval import contrarian, matrix
from smalls.agent import prompts


def test_the_rules_follow_the_frozen_investigator_prompt_and_nothing_else_changes(monkeypatch):
    frozen = {name: prompts.load(name) for name in prompts.NAMES}
    monkeypatch.setattr(prompts, "load", prompts.load)       # undone after the test
    contrarian.install()
    rules = contrarian.RULES.read_text(encoding="utf-8")
    assert prompts.load("investigator") == frozen["investigator"] + rules
    assert all(prompts.load(n) == frozen[n] for n in prompts.NAMES if n != "investigator")


def test_the_matrix_runs_arm_k_through_the_control_and_the_arm_is_not_given_twice():
    cell = {"arm": "K", "checkpoint": "1452", "tags": True, "rep": 1}
    cmd = matrix.command(cell, label="z5", bundle="b")
    assert cmd[1:4] == ["-m", "eval.contrarian", "run"] and "--arm" not in cmd and cmd[cmd.index("--run-id") + 1] == "z5-K-1452-tags-r1"
    with pytest.raises(SystemExit, match="the arm is K"):
        contrarian.main(["run", "--arm", "C"])
    assert cmd[0] == sys.executable
