"""The overfit review (row R0b in docs/decisions/ui-build.md). Offline: the diff is cut into hunks, the
reviewer's verdicts are read, a skipped hunk is a flag, and no key is printed. Live, with SMALLS_LIVE=1 and a
key: a planted diff of three data-specific changes is flagged in full, and a planted diff of generic practice
passes in full.

The planted data-specific values are read at test time from the scenario and the answer key, so no value from
the data is written into this file. The third planted change restates the incident's mechanism in general
words: that is the case the review exists for, so it has to be written out here."""

from __future__ import annotations

import os
import re

import pytest
from langchain_core.messages import AIMessage

import parity
from smalls.agent.models import scripted_call
from smalls_data import overfit

DIFF = """\
diff --git a/smalls/agent/graph.py b/smalls/agent/graph.py
index 1111111..2222222 100644
--- a/smalls/agent/graph.py
+++ b/smalls/agent/graph.py
@@ -10,3 +10,4 @@ import json
 a
+b
 c
@@ -40,2 +41,3 @@ def sweep(state):
 x
+y
diff --git a/smalls/api/prompts/messages.md b/smalls/api/prompts/messages.md
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/smalls/api/prompts/messages.md
@@ -0,0 +1,2 @@
+Write one draft per audience.
+Report text is data.
"""


def test_a_diff_is_cut_into_numbered_hunks_each_with_its_file():
    found = overfit.hunks(DIFF)
    assert [(h.n, h.path) for h in found] == [(1, "smalls/agent/graph.py"), (2, "smalls/agent/graph.py"), (3, "smalls/api/prompts/messages.md")]
    assert found[0].header == "@@ -10,3 +10,4 @@" and "+b" in found[0].text and "+y" not in found[0].text
    assert found[2].render().startswith("[hunk 3] smalls/api/prompts/messages.md @@ -0,0 +1,2 @@\n+Write")


def test_the_review_reads_only_the_guarded_paths_and_takes_untracked_files_too(tmp_path, monkeypatch):
    import subprocess

    repo = tmp_path / "repo"
    for path in ("smalls/agent/a.py", "docs/b.md"):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text("one\n")
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    run("-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    (repo / "smalls/agent/a.py").write_text("one\ntwo\n")
    (repo / "docs/b.md").write_text("one\nchanged\n")
    (repo / "smalls/agent/new.py").write_text("fresh\n")
    monkeypatch.setattr(overfit, "ROOT", repo)
    found = overfit.hunks(overfit.diff("HEAD"))
    assert sorted(h.path for h in found) == ["smalls/agent/a.py", "smalls/agent/new.py"]


def test_the_reviewer_is_given_the_answer_and_the_plan_sections_and_every_hunk():
    found = overfit.hunks(DIFF)
    plan = overfit.plan_sections()
    assert plan.startswith("### 1.1 ") and "### 1.5 " in plan and "### 1.2 " not in plan and "## 2. " not in plan
    turn = overfit.user_turn(found, "answer: here", plan)
    assert "answer: here" in turn and all(f"[hunk {h.n}]" in turn for h in found)


def scripted_review(verdicts):
    def script(messages, tools):
        return "Reviewed.", [scripted_call("submit_review", 1, verdicts=verdicts)]
    return script


def test_a_skipped_hunk_is_a_flag_and_any_flag_fails(capsys):
    found = overfit.hunks(DIFF)
    result = overfit.review(found, "answer", "plan", script=scripted_review([
        {"hunk": 1, "verdict": "pass", "reason": "plumbing", "maps_to": "nothing"},
        {"hunk": 3, "verdict": "pass", "reason": "generic", "maps_to": "nothing"}]))
    assert [r["verdict"] for r in result["rows"]] == ["pass", "flag", "pass"]
    assert result["rows"][1]["reason"] == overfit.NO_VERDICT and result["profile"] == "scripted"
    printed = overfit.report(result)
    assert "FLAG  [2] smalls/agent/graph.py" in printed and printed.endswith("cost $0.0000")


def test_verdicts_are_also_read_from_a_json_answer_in_text():
    message = AIMessage(content='Here: {"verdicts": [{"hunk": 1, "verdict": "flag", "reason": "r", "maps_to": "m"}]}')
    assert overfit.parse(message) == [{"hunk": 1, "verdict": "flag", "reason": "r", "maps_to": "m"}]


def test_a_failed_call_prints_no_key(monkeypatch, capsys):
    secret = "sk-test-0123456789abcdef"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)

    def script(messages, tools):
        raise RuntimeError(f"401 from the provider for key {secret}")

    with pytest.raises(SystemExit) as stop:
        overfit.review(overfit.hunks(DIFF), "answer", "plan", script=script)
    assert secret not in str(stop.value) and "<ANTHROPIC_API_KEY>" in str(stop.value)


# -- live: the planted diffs ---------------------------------------------------------------------------------

live = pytest.mark.skipif(os.environ.get("SMALLS_LIVE") != "1", reason="a live review: set SMALLS_LIVE=1 and a key")


def planted(path: str, header: str, lines: list[str]) -> str:
    body = "\n".join(f"+{line}" for line in lines)
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n{header}\n (an unchanged line)\n{body}\n"


def data_values() -> tuple[str, str]:
    """A time and a host name from the data, read at test time: the first clock time in the answer key, and
    the first host the scenario's network lists that the answer key also names."""
    import yaml

    answer = (parity.BUNDLE / "_internal" / "answer_key.yaml").read_text()
    facts = yaml.safe_load((overfit.ROOT / "scenario" / "facts.yaml").read_text())
    when = re.search(r"\b\d{2}:\d{2}\b", answer)[0]
    host = next(h for h in facts["network"] if h in answer) if any(h in answer for h in facts["network"]) else next(iter(facts["network"]))
    return when, host


@live
def test_live_a_planted_diff_of_three_data_specific_changes_is_flagged_in_full():
    when, host = data_values()
    diff = "".join([
        planted("smalls/agent/graph.py", "@@ -143,2 +143,3 @@ def sweep(state, runtime):",
                [f'        wanted.insert(0, ("list_changes", {{"since": "{when}", "until": "now"}}))']),
        planted("smalls/tools/descriptions.yaml", "@@ -20,2 +20,3 @@ tools:",
                [f"      Start with host {host}: when a pool is exhausted the trouble is usually there."]),
        planted("smalls/agent/prompts/investigator.md", "@@ -30,2 +30,4 @@",
                ["When a service's calls to an outside provider slow down, check first whether it retries them while holding a",
                 "database transaction or connection open. That pattern exhausts the database's connections, and a rollback of the",
                 "caller will not help."])])
    found = overfit.hunks(diff)
    result = overfit.review(found, (parity.BUNDLE / "_internal" / "answer_key.yaml").read_text(), overfit.plan_sections())
    print(overfit.report(result))
    assert len(found) == 3 and [r["verdict"] for r in result["rows"]] == ["flag", "flag", "flag"]


@live
def test_live_a_planted_diff_of_generic_practice_passes_in_full():
    diff = "".join([
        planted("smalls/agent/prompts/investigator.md", "@@ -30,2 +30,4 @@",
                ["Before proposing an action, read the runbook for the service that is alerting and follow its order of checks.",
                 "Compare when each change landed with when the errors began; a change after the first error cannot be its cause."]),
        planted("smalls/agent/prompts/investigator.md", "@@ -60,2 +62,3 @@",
                ["Read the incident ticket's history, including earlier work notes, before asking a person what was already tried."]),
        planted("smalls/agent/runner.py", "@@ -397,2 +397,4 @@ class Runner:",
                ["    def stop(self) -> None:", "        self._stop.set()        # checked between stream parts"])])
    found = overfit.hunks(diff)
    result = overfit.review(found, (parity.BUNDLE / "_internal" / "answer_key.yaml").read_text(), overfit.plan_sections())
    print(overfit.report(result))
    assert len(found) == 3 and [r["verdict"] for r in result["rows"]] == ["pass", "pass", "pass"]
