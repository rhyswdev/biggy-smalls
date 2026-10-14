"""The terminal (build row D10 of docs/decisions/tools-agent-build.md). Acceptance: a run paused in one
process resumes in another, and what the two print equals what a replay prints; every tool string is
rendered as text."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest
from rich.console import Console

import fixture_registry as fx
import fixture_script
import parity
from smalls.agent import __main__ as cli
from smalls.agent.config import load_config
from smalls.agent.events import Event
from smalls.agent.render import Renderer, citations, show
from smalls.agent.runner import Runner, module_toolbox

HERE = os.path.dirname(os.path.abspath(__file__))
COMMON = ["--registry", "fixture_registry", "--script", "fixture_script:turn", "--profile", "scripted"]


def process(args: list[str], runs_dir: Path) -> str:
    """One `python -m smalls.agent` process, with nobody at its terminal. Returns what it printed."""
    env = {**os.environ, "SMALLS_RUNS_DIR": str(runs_dir), "PYTHONPATH": os.pathsep.join([HERE, os.path.dirname(HERE), os.environ.get("PYTHONPATH", "")]),
           "COLUMNS": "100", "NO_COLOR": "1"}
    done = subprocess.run([sys.executable, "-m", "smalls.agent", *args], cwd=parity.ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-800:]
    return done.stdout


def printed(text: str) -> list[str]:
    """What a process printed about the run: everything before its last line, which says where the record is."""
    return [line.rstrip() for line in text.split("run record: ")[0].splitlines()]


def test_a_run_paused_in_one_process_resumes_in_another_and_prints_what_a_replay_prints(tmp_path):
    first = process(["run", "--now", "2026-03-02T02:20:00Z", "--trigger", fx.TRIGGER, "--run-id", "two-procs", "--stop-at-gate", *COMMON], tmp_path)
    assert "paused at a gate" in first and "resume with:" in first
    second = process(["resume", "two-procs", "--approve"], tmp_path)
    assert "report as of 2026-03-02T02:20:00Z" in second and "completed" in second
    replayed = process(["replay", "two-procs"], tmp_path)
    assert printed(first) + printed(second) == printed(replayed)                   # the two processes together, line for line
    assert len(printed(replayed)) > 60 and json.loads((tmp_path / "two-procs" / "report.json").read_text())["proposed_actions"][0]["decision"] == "approved"
    # The bar that closes each round is drawn from the round's own event, so the second process draws it right without the first one's memory.
    bars = [line for line in printed(replayed) if " · calls " in line and "round " in line]
    assert len(bars) >= 5 and "round 0 · no leading hypothesis · 0 open, 0 rejected · calls 0/30" in bars[0]
    assert any("round 2 · leading h-full 0.60 · 1 open, 0 rejected · calls 4/30" in bar for bar in bars)
    assert [bar for bar in printed(second) if "leading h-release 0.65 · 1 open, 1 rejected" in bar]


def events_of(run_dir: Path) -> list[dict]:
    return [json.loads(line) for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]


def test_a_report_numbers_its_citations_and_show_prints_the_row_behind_one(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SMALLS_RUNS_DIR", str(tmp_path))
    monkeypatch.syspath_prepend(HERE)
    assert cli.main(["run", "--now", "2026-03-02T02:20:00Z", "--trigger", fx.TRIGGER, "--run-id", "cited", "--eval-run", "--no-thinking", *COMMON]) == 0
    out = capsys.readouterr().out
    report = json.loads((tmp_path / "cited" / "report.json").read_text())
    numbered = citations(report)
    assert numbered[0] in report["summary"] and len(numbered) == len(set(numbered)) >= 8          # in the order the report first cites them
    assert set(numbered) == {h for hyp in report["hypotheses"] for h in hyp["support"] + hyp["against"]} | {
        h for name in ("timeline", "actions", "do_not") for row in report[name] for h in row["evidence"]} | {a["handle"] for a in report["alerts"]}
    assert "citations (show <run> <number> prints the row):" in out and all(f"[{n}] {h}" in out for n, h in enumerate(numbered, 1))
    lead = next(h for h in report["hypotheses"] if h["status"] == "leading")
    lines = out.split("report as of")[1].splitlines()
    top = next(n for n, line in enumerate(lines) if "statement" in line and "support" in line)
    table = "\n".join(lines[top:next(n for n, line in enumerate(lines) if n > top and line.startswith("└"))])
    assert lead["id"] in table and "[1]" in table and not any(h in table for h in numbered)       # the table cites by number, never by handle
    evidence = {row["handle"]: row for row in map(json.loads, (tmp_path / "cited" / "evidence.jsonl").read_text().splitlines())}
    assert cli.main(["show", "cited", "1"]) == 0
    shown = capsys.readouterr().out
    assert f"[1] {numbered[0]}" in shown and "shown by call_" in shown and evidence[numbered[0]]["text"].split("  ")[-1][:30] in shown
    assert cli.main(["show", str(tmp_path / "cited"), numbered[3]]) == 0 and f"[4] {numbered[3]}" in capsys.readouterr().out      # by handle, and by folder
    for wanted in ("999", "log:worker-01/00000000"):
        with pytest.raises(SystemExit, match="no tool result in this run showed that"):
            cli.main(["show", "cited", wanted])
    with pytest.raises(SystemExit, match="no run record"):
        cli.main(["show", "no-such-run", "1"])


def test_every_tool_string_is_rendered_as_text(tmp_path):
    markup = "[bold red]boom[/] [link=file:///etc/passwd]click[/link] [/]"
    console = Console(record=True, width=400, force_terminal=False, highlight=False)
    r = Renderer(console)
    report = {"as_of": markup, "summary": f"S {markup}", "hypotheses": [{"id": markup, "role": markup, "status": markup, "confidence": 0.5, "statement": markup,
                                                                           "support": ["log:a/00000001"], "against": []}],
              "timeline": [{"t_utc": markup, "event": markup, "evidence": []}], "actions": [{"rank": 1, "action": markup, "owner": markup, "evidence": []}],
              "do_not": [{"action": markup, "why": markup, "evidence": []}], "alerts": [], "open_questions": [markup],
              "proposed_actions": [{"tool": markup, "args": {"x": markup}, "decision": "approved", "by": {"who": "human", "via": "cli"}, "reason": markup}],
              "validation": {"verifier": markup, "disputed": [markup]}}
    by = {"who": "human", "via": "cli"}
    for kind, data in [
        ("run_started", {"now": markup, "trigger": markup, "profile": markup}), ("phase", {"name": markup}), ("thinking_delta", {"text": markup}),
        ("text_delta", {"text": markup}), ("model_call", {"latency_ms": 1, "usage": {}, "stop_reason": markup}),
        ("tool_call_proposed", {"tool": markup, "kind": markup, "args": {"terms": [markup]}, "rationale": {"tests": markup, "expect": markup}}),
        ("gate_decision", {"tool": markup, "decision": "rejected", "by": by, "reason": markup, "reason_text": markup}),
        ("ic_note", {"handle": markup, "text": markup}),
        ("tool_result", {"tool": markup, "status": "ok", "shown": [], "referenced": [], "chars": 1, "latency_ms": 1, "native_query": markup}),
        ("hypothesis_update", {"id": markup, "role": markup, "status": "open", "confidence": 0.1, "statement": markup}),
        ("board_status", {"line": markup}), ("budget", {"used": 1, "limit": 2, "turns_left": 3, "round": 1, "board": {"leading": {"id": markup, "confidence": 0.5},
                                                                                                                     "open": 1, "rejected": 0}}),
        ("verifier_verdict", {"status": markup, "decision": {}, "disputed": [markup]}), ("restart", {"reason": markup, "thread": markup}),
        ("validation", {"ok": False, "flagged": [markup]}), ("report", {"report": report, "status": markup}),
        ("run_paused", {"reason": markup, "resume": markup}), ("error", {"where": markup, "message": markup}), ("run_finished", {"status": markup})]:
        r.emit(Event(type=kind, data=data, run_id=markup))
    text = console.export_text()
    assert text.count("[bold red]boom[/]") >= 30 and "[link=file:///etc/passwd]click[/link]" in text      # printed as written, never interpreted
    assert "\x1b]8;" not in console.export_text(styles=True) and "boom" not in console.export_html().split("<body>")[1].replace("[bold red]boom[/]", "")
    (tmp_path / "evidence.jsonl").write_text(json.dumps({"call_id": "c1", "handle": "log:a/00000001", "t_known": None, "text": f"log:a/00000001  {markup}"}) + "\n")
    (tmp_path / "report.json").write_text(json.dumps(report))
    shown = Console(record=True, width=400, force_terminal=False, highlight=False)
    assert show(tmp_path, "1", shown) == 0 and f"log:a/00000001  {markup}" in shown.export_text()


def slow_first_turn(messages, tools):
    if not any(m.type == "ai" for m in messages):
        time.sleep(0.9)
    return fixture_script.turn(messages, tools)


def test_a_heartbeat_says_a_silent_run_is_still_working_and_never_while_a_person_is_asked(tmp_path, capsys):
    cfg = replace(load_config(runs_dir=tmp_path), heartbeat_s=0.25)

    def slow_person(cards):
        time.sleep(0.9)
        return {card["call_id"]: {"approve": True} for card in cards}

    r = Runner(cfg, module_toolbox("fixture_registry"), "beat", script=slow_first_turn, decider=slow_person)
    r.start(fx.TRIGGER, fx.NOW)
    r.close()
    events = events_of(r.dir)
    kinds = [e["type"] for e in events]
    beats = [e for e in events if e["type"] == "heartbeat"]
    first_call = kinds.index("model_call")
    assert 2 <= len([k for k in kinds[:first_call] if k == "heartbeat"]) <= 4 and all(e["data"]["seconds"] >= 0 for e in beats)      # while the model was silent
    asked, answered = kinds.index("gate_pending"), kinds.index("gate_decision", kinds.index("gate_pending"))
    assert "heartbeat" not in kinds[asked:answered] and len(beats) == kinds[:first_call].count("heartbeat")         # the silence was the person's
    assert cli.main(["replay", str(r.dir), "--no-thinking"]) == 0
    assert capsys.readouterr().out.count("… still working: nothing new for") == len(beats)                           # and a replay says it the same number of times
    quiet = Runner(replace(cfg, heartbeat_s=0), module_toolbox("fixture_registry"), "quiet", script=slow_first_turn, eval_run=True)
    quiet.start(fx.TRIGGER, fx.NOW)
    quiet.close()
    assert "heartbeat" not in [e["type"] for e in events_of(quiet.dir)]              # zero turns it off
