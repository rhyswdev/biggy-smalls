"""The agent's entry point.

    python -m smalls.agent run --now <time> --trigger <text> [--bundle <folder>] [--interactive] [--stop-at-gate]
                               [--headless approve|defer|reject] [--profile <name>] [--run-id <id>]
    python -m smalls.agent resume <run_id> [--reject <reason>] [--interactive]
    python -m smalls.agent replay <run_id|folder>          re-render a run record; no model, no key
    python -m smalls.agent schema [--tags on|off] [--size] [--write] [--skeleton]

The agent takes the trigger as text and `now` as a time. Which trigger belongs to which checkpoint is the
caller's business (the Makefile and the eval harness).

Whether a run is attended is decided by the terminal: with one attached, a gate asks; without one, or with
`--headless`, the run answers its own gates, and defers unless it was told otherwise.

`schema` prints every tool schema the model can be shown and the report schema, as one JSON document.
`--size` prints the size of the cached prefix (the system prompt and the tool definitions) with tags on
and off. `--write` regenerates `smalls/contracts/report.schema.json`. `--skeleton` prints the descriptions
file for the current surface with every text a placeholder.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from smalls.agent import internal_tools, prompts, verifier
from smalls.contracts import report
from smalls.contracts.report import Rationale
from smalls.tools import descriptions, registry, store


def surface(*, tags: bool) -> dict[str, registry.ToolSpec]:
    """Every tool a model can be shown, built or not, in the fixed order: the investigator's, then the one
    tool a verifier pass answers through."""
    verdict = verifier.spec()
    return {**internal_tools.toolset(registry.surface(tags=tags)), verdict.name: verdict}


def skeleton() -> dict[str, Any]:
    """The descriptions file's keys for the whole surface, with every tag input present."""
    return descriptions.skeleton({name: spec.args for name, spec in surface(tags=True).items()}, gated=Rationale)


def definitions(*, tags: bool, texts: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [registry.definition(spec, texts) for spec in surface(tags=tags).values()]


def schema_document(*, tags: bool, texts: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"tags": "on" if tags else "off", "tools": definitions(tags=tags, texts=texts), "report": report.report_schema()}


def prefix_size(*, tags: bool) -> dict[str, int]:
    """The cached prefix, in characters, and in tokens counted as characters over 4."""
    system = len(prompts.load("investigator"))
    tools = len(json.dumps(definitions(tags=tags), ensure_ascii=False))
    return {"system_prompt_chars": system, "tool_definitions_chars": tools, "prefix_chars": system + tools,
            "prefix_tokens": (system + tools) // 4}


def cmd_schema(args: argparse.Namespace) -> int:
    if args.write:
        report.SCHEMA_PATH.write_text(report.schema_text(), encoding="utf-8")
        print(f"wrote {report.SCHEMA_PATH}")
        return 0
    if args.skeleton:
        print(yaml.safe_dump(skeleton(), sort_keys=False, allow_unicode=True, width=110), end="")
        return 0
    if args.size:
        print(json.dumps({"tags_on": prefix_size(tags=True), "tags_off": prefix_size(tags=False)}, indent=2))
        return 0
    tags = store.tags_enabled() if args.tags is None else args.tags == "on"
    print(json.dumps(schema_document(tags=tags), indent=2, ensure_ascii=False))
    return 0


def parse_now(text: str, bundle: str | None) -> datetime:
    """`now` as an aware time: an ISO time, or a clock time such as 14:52 on the bundle's day."""
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return when if when.tzinfo else when.replace(tzinfo=timezone.utc)
    except ValueError:
        if not bundle:
            raise SystemExit(f"--now {text}: give a full ISO time, or name a bundle so a clock time has a day")
        ms = store.Store(None, store.Bundle(bundle)).resolve_time(text)
        return datetime.fromtimestamp(ms / 1000, timezone.utc)


def _toolbox(args: argparse.Namespace, saved: dict[str, Any] | None = None):
    from smalls.agent.runner import bundle_toolbox, module_toolbox

    saved = saved or {}
    module = getattr(args, "registry", None) or (saved.get("registry") if saved.get("registry") != "bundle" else None)
    if module:
        return module_toolbox(module)
    bundle = getattr(args, "bundle", None) or os.environ.get("SMALLS_BUNDLE")
    if not bundle:
        raise SystemExit("smalls: name a bundle with --bundle <folder> or SMALLS_BUNDLE")
    return bundle_toolbox(bundle, saved.get("tags"))


def _check_placeholders(profile_provider: str) -> None:
    """A live run refuses to start while any prompt or description is still a placeholder."""
    if profile_provider == "scripted":
        return
    left = [*prompts.placeholders(), *(["descriptions.yaml"] if descriptions.placeholders() else [])]
    if left:
        raise SystemExit("refusing a live run: these files still hold placeholder text: " + ", ".join(left))


def cmd_run(args: argparse.Namespace) -> int:
    from rich.console import Console

    from smalls.agent.config import load_config
    from smalls.agent.gate import headless
    from smalls.agent.render import Renderer, interactive_decider
    from smalls.agent.runner import Runner

    cfg = load_config(profile=args.profile, interactive=args.interactive)
    _check_placeholders(cfg.investigator.provider)
    trigger = Path(args.trigger_file).read_text(encoding="utf-8").strip() if args.trigger_file else args.trigger
    if not trigger:
        raise SystemExit("give the trigger with --trigger <text> or --trigger-file <path>")
    console = Console(highlight=False)
    attended = sys.stdin.isatty() and args.headless is None
    decider = interactive_decider(console) if attended else (headless(args.headless) if args.headless else None)
    toolbox = _toolbox(args)
    run = Runner(cfg, toolbox, args.run_id, sinks=[Renderer(console, not args.no_thinking)], decider=decider,
                 stop_at_gate=args.stop_at_gate, attended=attended, eval_run=args.eval_run, script=args.script)
    result = run.start(trigger, parse_now(args.now, toolbox.describe.get("bundle") and args.bundle), checkpoint=args.checkpoint,
                       arm=args.arm, channel=args.channel)
    run.close()
    console.print(f"[dim]run record: {run.dir}[/]")
    return 0 if result["status"] in ("completed", "paused") else 1


def cmd_resume(args: argparse.Namespace) -> int:
    from rich.console import Console

    from smalls.agent.config import load_config
    from smalls.agent.record import RunRecord
    from smalls.agent.render import Renderer, interactive_decider
    from smalls.agent.runner import Runner

    cfg = load_config(profile=args.profile)
    saved = RunRecord(cfg.runs_dir, args.run_id).read_json("run.json")
    cfg = load_config(profile=args.profile or saved["profile"], interactive=not saved["context"]["auto_approve"])
    console = Console(highlight=False)
    attended = sys.stdin.isatty() and not args.reject and not args.approve
    if args.reject:
        decider = lambda cards: {c["call_id"]: {"approve": False, "reason": args.reject} for c in cards}  # noqa: E731
    elif args.approve:
        decider = lambda cards: {c["call_id"]: {"approve": True} for c in cards}  # noqa: E731
    elif attended:
        decider = interactive_decider(console)
    else:
        decider = None
    run = Runner(cfg, _toolbox(args, saved), args.run_id, sinks=[Renderer(console)], decider=decider, attended=attended,
                 eval_run=saved.get("eval_run", False), script=saved.get("script"))
    result = run.resume()
    run.close()
    return 0 if result["status"] in ("completed", "paused") else 1


def cmd_replay(args: argparse.Namespace) -> int:
    from rich.console import Console

    from smalls.agent.config import load_config
    from smalls.agent.events import read_events
    from smalls.agent.render import Renderer

    folder = Path(args.run_id) if Path(args.run_id).is_dir() else load_config().runs_dir / args.run_id
    log = folder / "events.jsonl"
    if not log.is_file():
        raise SystemExit(f"no run record at {folder}")
    renderer = Renderer(Console(highlight=False), not args.no_thinking)
    for event in read_events(log):
        renderer.emit(event)
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m smalls.agent", description="The Smalls investigator.")
    sub = p.add_subparsers(dest="command", required=True)
    add_run_commands(sub)
    schema = sub.add_parser("schema", help="print every tool schema and the report schema")
    schema.add_argument("--tags", choices=["on", "off"], help="with or without the tag inputs (default: SMALLS_TAGS)")
    schema.add_argument("--size", action="store_true", help="print the size of the cached prefix, tags on and off")
    schema.add_argument("--write", action="store_true", help="regenerate the committed report schema file")
    schema.add_argument("--skeleton", action="store_true", help="print the descriptions file with every text a placeholder")
    schema.set_defaults(run=cmd_schema)
    return p


def add_run_commands(sub) -> None:
    """`run`, `resume` and `replay`. The `smalls` command adds the same three, so there is one definition."""
    run = sub.add_parser("run", help="investigate: one run at one `now`")
    run.add_argument("--now", required=True, help="the replay clock: an ISO time, or HH:MM on the bundle's day")
    run.add_argument("--trigger", help="the incident commander's question, as text")
    run.add_argument("--trigger-file", metavar="PATH", help="a file holding the trigger")
    run.add_argument("--bundle", default=os.environ.get("SMALLS_BUNDLE"), help="bundle folder (or SMALLS_BUNDLE)")
    run.add_argument("--checkpoint", help="a label for this `now`, kept in the run record")
    run.add_argument("--arm", default="C", help="the eval arm, kept in the run record")
    run.add_argument("--channel", help="the incident channel the opening sweep reads; all channels when omitted")
    run.add_argument("--profile", help="a model profile from config.toml (default: SMALLS_PROFILE, then the file's default)")
    run.add_argument("--interactive", action="store_true", help="ask before every read too, not only before actions")
    run.add_argument("--stop-at-gate", action="store_true", help="pause at the first gate and exit; resume later")
    run.add_argument("--headless", choices=["approve", "defer", "reject"],
                     help="answer gates without asking. Default: ask when a terminal is attached, otherwise defer")
    run.add_argument("--eval-run", action="store_true", help="an eval run: unattended gates approve and record")
    run.add_argument("--run-id")
    run.add_argument("--no-thinking", action="store_true")
    run.add_argument("--registry", metavar="MODULE", help="a module exposing registry() and inventory(), in place of the bundle's tools")
    run.add_argument("--script", metavar="MODULE:FUNCTION", help="the script the scripted profile plays")
    run.set_defaults(run=cmd_run)

    resume = sub.add_parser("resume", help="resume a run paused at a gate")
    resume.add_argument("run_id")
    resume.add_argument("--bundle", default=os.environ.get("SMALLS_BUNDLE"))
    resume.add_argument("--profile")
    resume.add_argument("--reject", metavar="REASON", help="reject the pending calls with this reason")
    resume.add_argument("--approve", action="store_true", help="approve the pending calls")
    resume.set_defaults(run=cmd_resume)

    replay = sub.add_parser("replay", help="re-render a run record; no model, no key")
    replay.add_argument("run_id", help="a run id, or the folder of a run record")
    replay.add_argument("--no-thinking", action="store_true")
    replay.set_defaults(run=cmd_replay)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.run(args) or 0


if __name__ == "__main__":
    sys.exit(main())
