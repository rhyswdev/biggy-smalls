"""CLI: run, resume, replay, history, bench.

    uv run python -m spike run                      # scripted model, auto-approve on; gated actions ask if attended, else defer
    uv run python -m spike run --interactive        # every gated call asks in the terminal
    uv run python -m spike run --stop-at-gate       # pause at the first gate and exit; resume later
    uv run python -m spike resume <run_id> [--reject "reason"]
    uv run python -m spike replay <run_id|dir>      # re-render a run record; no model, no key
    uv run python -m spike history <run_id>         # checkpoints, for rewind or fork
    uv run python -m spike bench                    # framework overheads with the scripted model
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console

from .config import load_config
from .events import read_events
from .render import Renderer, interactive_decider
from .runner import Runner, headless

DEFAULT_Q = "The nightly report-export job failed at 02:10 UTC. What happened, and what should we do before tonight's run?"
DEFAULT_NOW = "2026-03-02T02:20:00+00:00"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="spike")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--profile")
    r.add_argument("--interactive", action="store_true", help="turn the global auto-approve off")
    r.add_argument("--stop-at-gate", action="store_true")
    r.add_argument("--headless", choices=["defer", "approve", "reject"], default=None,
                   help="answer gated calls without asking (evals, tests, CI). Default: ask if attended to a terminal, "
                        "otherwise defer")
    r.add_argument("--now", default=DEFAULT_NOW)
    r.add_argument("--question", default=DEFAULT_Q)
    r.add_argument("--run-id")
    r.add_argument("--no-thinking", action="store_true")
    r.add_argument("--bad-handle", action="store_true", help="scripted model cites a fabricated handle once")
    rs = sub.add_parser("resume")
    rs.add_argument("run_id")
    rs.add_argument("--profile")
    rs.add_argument("--reject", metavar="REASON")
    rs.add_argument("--interactive", action="store_true")
    rp = sub.add_parser("replay")
    rp.add_argument("run_id")
    hs = sub.add_parser("history")
    hs.add_argument("run_id")
    sub.add_parser("bench")
    a = ap.parse_args(argv)
    console = Console(highlight=False)

    if a.cmd == "run":
        cfg = load_config(profile=a.profile, interactive=a.interactive)
        attended = sys.stdin.isatty() and a.headless is None
        decider = interactive_decider(console) if (a.interactive or attended) else headless(a.headless or "defer")
        overrides = {"inject_bad_handle": True} if a.bad_handle and cfg.investigator.provider == "scripted" else None
        run = Runner(cfg, a.run_id, sinks=[Renderer(console, not a.no_thinking)], decider=decider,
                     stop_at_gate=a.stop_at_gate, model_overrides=overrides)
        res = run.start(a.question, datetime.fromisoformat(a.now))
        run.close()
        console.print(f"[dim]run record: {run.dir}[/]")
        return 0 if res["status"] in ("completed", "waiting_for_gate") else 1

    if a.cmd == "resume":
        cfg = load_config(profile=a.profile)
        if a.reject:
            decider = lambda calls: {c["call_id"]: {"approve": False, "reason": a.reject} for c in calls}  # noqa: E731
        elif a.interactive:
            decider = interactive_decider(console)
        else:
            decider = lambda calls: {c["call_id"]: {"approve": True} for c in calls}  # noqa: E731
        run = Runner(cfg, a.run_id, sinks=[Renderer(console)], decider=decider)
        res = run.resume()
        run.close()
        return 0 if res["status"] == "completed" else 1

    if a.cmd == "replay":
        cfg = load_config()
        ren = Renderer(console)
        d = Path(a.run_id) if Path(a.run_id).is_dir() else cfg.runs_dir / a.run_id
        for ev in read_events(d / "events.jsonl"):
            ren.emit(ev)
        return 0

    if a.cmd == "history":
        cfg = load_config()
        run = Runner(cfg, a.run_id)
        for snap in run.graph.get_state_history(run.config):
            c = snap.config["configurable"]
            console.print(f"{c['checkpoint_id']}  step {snap.metadata.get('step')}  next {list(snap.next)}  "
                          f"messages {len(snap.values.get('messages', []))}")
        run.close()
        return 0

    if a.cmd == "bench":
        from .bench import bench
        print(json.dumps(bench(), indent=1))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
