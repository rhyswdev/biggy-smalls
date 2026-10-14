"""Run the scripted solve path (build row D5 of docs/decisions/tools-agent-build.md): about fifteen tool
calls that walk the development bundle from its trigger to its cause, each checked for the words its
result must hold.

    python -m eval.solve.run --bundle <folder> [--now <time>] [--show]

Each step is printed as the `smalls tools call` command that makes the same call, so a person can make it
by hand. A step passes when the call is answered and its result holds every expected string. The path
reaches the cause when every step passes.

This folder names one bundle's hosts, people and tickets. Nothing under `smalls/` reads it, and it is
never near a prompt.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from smalls.tools import registry, store
from smalls.tools.registry import ToolContext

PATH = Path(__file__).with_name("path.yaml")


def load(path: Path = PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def context(bundle: Path | str, now: str, tags: bool = False) -> ToolContext:
    built = store.build(bundle, tags=tags)
    ms = built.set_now(now)
    return ToolContext(store=built, now=datetime.fromtimestamp(ms / 1000, timezone.utc), tags=tags)


def command(step: dict[str, Any], now: str) -> str:
    """The step as it is typed at the command line."""
    cells = [f"{name}={value}" if isinstance(value, str) else f"{name}={json.dumps(value)}" for name, value in step["args"].items()]
    return " ".join(["smalls tools call", step["tool"], "--now", now, *(f"'{cell}'" if " " in cell or "[" in cell or "{" in cell else cell for cell in cells)])


def run(ctx: ToolContext, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools = registry.registry(tags=ctx.tags)
    out = []
    for n, step in enumerate(steps, 1):
        env = registry.call(tools[step["tool"]], step["args"], ctx)
        missing = [words for words in step.get("expect") or [] if words not in env.text]
        out.append({"n": n, "tool": step["tool"], "status": env.status, "missing": missing, "ok": env.status == "ok" and not missing,
                    "tokens": len(env.text) // 4, "shown": list(env.shown), "text": env.text})
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.solve.run", description="Run the scripted solve path and check each step.")
    p.add_argument("--bundle", required=True)
    p.add_argument("--now", help="the replay clock (default: the path's own)")
    p.add_argument("--show", action="store_true", help="print each result in full")
    args = p.parse_args(argv)
    path = load()
    now = args.now or path["now"]
    results = run(context(args.bundle, now), path["steps"])
    for step, result in zip(path["steps"], results):
        print(f"{result['n']:>2}. {'ok     ' if result['ok'] else 'MISSING'} {command(step, now)}")
        print(f"    {step['why']}")
        for words in result["missing"]:
            print(f"    not in the result: {words!r}")
        if args.show:
            print("\n".join("    | " + line for line in result["text"].splitlines()))
    passed = sum(r["ok"] for r in results)
    print(f"\n{passed} of {len(results)} steps hold; {sum(r['tokens'] for r in results)} tokens of results in all")
    if passed == len(results):
        print("The path reaches: " + " ".join(path["reaches"].split()))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
