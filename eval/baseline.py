"""Arm A, the no-tools baseline (PLAN section 6.1; build row D6 of docs/decisions/tools-agent-build.md): the
trigger, the chat transcript and the alerts, in one model call.

    python -m eval.baseline --bundle <folder> --now <time> --trigger-file <path> [--checkpoint <id>]

It is the same agent under a different context strategy, so it is the same graph, the same prompt, the
same validator and the same run record. Three things differ, and all three are settings of the run:

- the run has two read tools, `list_alerts` and `read_chat`, and only the harness calls them, through the
  same `call` every tool goes through: the opening sweep, which skips a tool the run does not have;
- the model is given one tool, `submit_report`, and one turn;
- there is no board tool and no second turn, so a hypothesis may be new in the report and the first
  submission is the one that stands. What cites a handle the two reads did not show is dropped and
  flagged by the validator, as in any run.

The verifier is off: it is part of what arm C is, and this arm is the floor it is compared with.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

from smalls.agent.__main__ import _check_placeholders, parse_now
from smalls.agent.config import load_config
from smalls.agent.runner import Runner, Toolbox, bundle_toolbox
from smalls.contracts.envelope import Envelope

ARM = "A"
READS = ("list_alerts", "read_chat")
OFFERED = ("submit_report",)
NO_INVENTORY = "none"


def toolbox(bundle: Path | str, tags: bool | None = None) -> Toolbox:
    """The bundle's toolbox, cut down to the two reads this arm is given, and with no inventory."""
    full = bundle_toolbox(bundle, tags)
    return Toolbox({name: spec for name, spec in full.read_tools.items() if name in READS}, lambda ctx: Envelope(NO_INVENTORY),
                   full.context, dict(full.describe))


def run(bundle: Path | str, now: str, trigger: str, *, checkpoint: str | None = None, profile: str | None = None, run_id: str | None = None,
        tags: bool | None = None, script: Any = None, runs_dir: Path | None = None, set_name: str | None = None,
        roles: list[str] | None = None, effort: str | None = None) -> dict[str, Any]:
    cfg = load_config(profile=profile, runs_dir=runs_dir, set_name=set_name, roles=roles, effort=effort)
    cfg = replace(cfg, call_budget=0, extra_turns=1, verifier={**cfg.verifier, "enabled": False})       # no gated call, one model turn
    _check_placeholders(cfg.investigator.provider)
    runner = Runner(cfg, toolbox(bundle, tags), run_id, eval_run=True, script=script, durability="exit", offered=OFFERED, single_call=True)
    try:
        result = runner.start(trigger, parse_now(now, str(bundle)), checkpoint=checkpoint, arm=ARM)
    finally:
        runner.close()
    return {**result, "dir": str(runner.dir)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.baseline", description="Arm A: the trigger, the chat and the alerts in one model call.")
    p.add_argument("--bundle", required=True)
    p.add_argument("--now", required=True, help="the replay clock: an ISO time, or HH:MM on the bundle's day")
    p.add_argument("--trigger")
    p.add_argument("--trigger-file", metavar="PATH")
    p.add_argument("--checkpoint")
    p.add_argument("--profile")
    p.add_argument("--set")
    p.add_argument("--role", action="append", metavar="ROLE=PROFILE[@EFFORT]")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--run-id")
    p.add_argument("--tags", choices=["on", "off"])
    p.add_argument("--script", metavar="MODULE:FUNCTION", help="the script the scripted profile plays")
    args = p.parse_args(argv)
    trigger = Path(args.trigger_file).read_text(encoding="utf-8").strip() if args.trigger_file else args.trigger
    if not trigger:
        raise SystemExit("give the trigger with --trigger <text> or --trigger-file <path>")
    result = run(args.bundle, args.now, trigger, checkpoint=args.checkpoint, profile=args.profile, run_id=args.run_id,
                 tags=None if args.tags is None else args.tags == "on", script=args.script, set_name=args.set, roles=args.role,
                 effort=args.effort)
    print(f"arm {ARM}: {result['status']}; run record: {result['dir']}")
    return 0 if result["status"] == "completed" and not result.get("error") else 1


if __name__ == "__main__":
    sys.exit(main())
