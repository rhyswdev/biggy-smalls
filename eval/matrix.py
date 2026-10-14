"""The eval matrix (PLAN section 6.1; build row D6 of docs/decisions/tools-agent-build.md): arms,
checkpoints, the tags flag and repetitions, one process per run.

    python -m eval.matrix --bundle <folder> [--arms C] [--checkpoints 1452] [--reps 3] [--tags on]
                          [--profile <name>] [--label <name>] [--dry-run]

The regression set is its default: arm C at the second checkpoint, three runs (`make regress`). A run is a
process of its own, so it holds one store and one clock and two checkpoints never share either. Arm C is
`python -m smalls.agent run`; arm A is `python -m eval.baseline`. Both write the same run record, and the
table printed at the end is read back from those records, never from what a process printed.

A checkpoint is the name of a trigger file in the bundle's `triggers/` folder, and its clock time: `1452`
is 14:52 on the bundle's day. Resolving one is this module's job, so nothing under `smalls/` names that
folder.

A run that fails is a row with its status, and the matrix goes on. Results are a mean with the spread
between runs, never one run and never a claim of significance.

A live matrix costs money for every run. `--dry-run` prints the commands and starts nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARMS = {"A": "the trigger, the chat and the alerts in one model call, no tools", "C": "the agent with tools"}
REGRESSION = {"arms": "C", "checkpoints": "1452", "reps": 3}
MEASURES = (("tool calls", lambda s: s.get("tool_calls")), ("model turns", lambda s: (s.get("process") or {}).get("model_turns")),
            ("tokens", lambda s: (s.get("process") or {}).get("tokens_total")), ("cost usd", lambda s: (s.get("tokens") or {}).get("cost_usd")),
            ("wall s", lambda s: s.get("wall_s_total")), ("cited handles", lambda s: (s.get("process") or {}).get("cited_handles")))


def clock(checkpoint: str) -> str:
    """A checkpoint's clock time: `1452` is `14:52`. Anything else is passed on as the time it is."""
    return f"{checkpoint[:2]}:{checkpoint[2:]}" if len(checkpoint) == 4 and checkpoint.isdigit() else checkpoint


def cells(arms: list[str], checkpoints: list[str], tags: list[bool], reps: int) -> list[dict[str, Any]]:
    return [{"arm": arm, "checkpoint": checkpoint, "tags": flag, "rep": rep}
            for arm in arms for checkpoint in checkpoints for flag in tags for rep in range(1, reps + 1)]


def run_id(label: str, cell: dict[str, Any]) -> str:
    return f"{label}-{cell['arm']}-{cell['checkpoint']}-{'tags' if cell['tags'] else 'notags'}-r{cell['rep']}"


def command(cell: dict[str, Any], *, label: str, bundle: str, profile: str | None = None, script: str | None = None,
            trigger: str | None = None, set_name: str | None = None, roles: list[str] | None = None, effort: str | None = None) -> list[str]:
    """The one process a cell runs."""
    module = ["-m", "smalls.agent", "run", "--arm", "C", "--eval-run", "--durability", "exit", "--no-thinking"] if cell["arm"] == "C" else ["-m", "eval.baseline"]
    given = ["--trigger", trigger] if trigger else ["--trigger-file", str(Path(bundle) / "triggers" / f"{cell['checkpoint']}.md")]
    out = [sys.executable, *module, "--bundle", bundle, "--now", clock(cell["checkpoint"]), *given, "--checkpoint", cell["checkpoint"],
           "--run-id", run_id(label, cell), "--tags", "on" if cell["tags"] else "off"]
    out += ["--profile", profile] if profile else []
    out += ["--set", set_name] if set_name else []
    out += [arg for role in roles or () for arg in ("--role", role)]
    out += ["--effort", effort] if effort else []
    out += ["--script", script] if script else []
    return out


def resolved(*, set_name: str | None = None, roles: list[str] | None = None, profile: str | None = None, effort: str | None = None) -> str:
    """One line: the set every run uses and what each role resolves to in the config, as a run would resolve it.
    It reads the config's own sets and profiles, never what a test substitutes, and it names no key."""
    import tomllib

    from smalls.agent.config import PATH, _spec, resolve_roles

    raw = tomllib.loads(PATH.read_text(encoding="utf-8"))
    profiles = {n: _spec(n, p) for n, p in raw["profiles"].items()}
    name, specs = resolve_roles(raw, profiles, set_name=set_name, roles=roles, profile=profile, effort=effort,
                                model_override=os.environ.get("SMALLS_MODEL"))
    return f"set {name}: " + ", ".join(f"{role} {spec.profile}@{spec.effort} ({spec.model})" if spec else f"{role} none"
                                        for role, spec in specs.items())


def read_summary(runs_dir: Path, name: str) -> dict[str, Any]:
    """What a run's own record says about it. A run that left no summary is a row that says so."""
    folder = runs_dir / name
    try:
        summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        report = json.loads((folder / "report.json").read_text(encoding="utf-8")) if (folder / "report.json").exists() else {}
    except (OSError, ValueError):
        return {"run_id": name, "status": "no record", "stop_reason": "the run left no summary"}
    validation = (report or {}).get("validation") or {}
    return {**summary, "dropped_citations": len(validation.get("dropped") or []), "flagged_claims": len(validation.get("flagged") or [])}


def spread(values: list[float]) -> str:
    values = [v for v in values if v is not None]
    if not values:
        return "-"
    mean = sum(values) / len(values)
    form = (lambda v: f"{v:.4f}") if max(abs(v) for v in values) < 10 and any(v != int(v) for v in values) else (lambda v: f"{v:.0f}")
    return f"{form(mean)} ({form(min(values))} to {form(max(values))})"


def table(rows: list[dict[str, Any]]) -> str:
    """One line per run, then one block per cell: the mean and the spread between its runs."""
    lines = ["run  status  tool calls  model turns  tokens  cost usd  wall s  stop reason"]
    for row in rows:
        s = row["summary"]
        lines.append("  ".join(str(x) for x in (row["run_id"], s.get("status"), *(measure(s) if measure(s) is not None else "-" for _, measure in MEASURES[:5]),
                                                 s.get("stop_reason") or "-")))
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["arm"], row["checkpoint"], row["tags"]), []).append(row["summary"])
    for (arm, checkpoint, tags), runs in groups.items():
        done = sum(1 for s in runs if s.get("status") == "completed")
        lines.append(f"\narm {arm} at {checkpoint}, tags {'on' if tags else 'off'}: {len(runs)} run{'s' if len(runs) != 1 else ''}, {done} completed. "
                     f"Mean, with the lowest and highest run:")
        lines += [f"  {name}: {spread([measure(s) for s in runs])}" for name, measure in MEASURES]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.matrix", description="Run a matrix of arms, checkpoints, tags and repetitions, one process per run.")
    p.add_argument("--bundle", default=os.environ.get("SMALLS_BUNDLE"), help="bundle folder (or SMALLS_BUNDLE)")
    p.add_argument("--arms", default=REGRESSION["arms"], help="comma-separated, from: " + ", ".join(ARMS))
    p.add_argument("--checkpoints", default=REGRESSION["checkpoints"], help="comma-separated names of trigger files, each a clock time: 1452 is 14:52")
    p.add_argument("--reps", type=int, default=REGRESSION["reps"])
    p.add_argument("--tags", default="on", help="on, off, or on,off to run every cell both ways")
    p.add_argument("--profile", help="the investigator's profile from config.toml")
    p.add_argument("--set", help="the model set every run uses (default: SMALLS_SET, then the config's default_set)")
    p.add_argument("--role", action="append", metavar="ROLE=PROFILE[@EFFORT]", help="override one role's model in every run; repeatable")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help="the investigator's effort in every run")
    p.add_argument("--script", metavar="MODULE:FUNCTION", help="the script the scripted profile plays")
    p.add_argument("--trigger", help="the trigger as text, in place of the checkpoint's trigger file")
    p.add_argument("--label", help="the prefix of every run id (default: the time the matrix started)")
    p.add_argument("--runs-dir", help="where run records go (default: the configured folder)")
    p.add_argument("--dry-run", action="store_true", help="print each run's command and start nothing")
    args = p.parse_args(argv)
    if not args.bundle:
        raise SystemExit("eval.matrix: name a bundle with --bundle <folder> or SMALLS_BUNDLE")
    arms, checkpoints = [a.strip().upper() for a in args.arms.split(",") if a.strip()], [c.strip() for c in args.checkpoints.split(",") if c.strip()]
    unknown = [a for a in arms if a not in ARMS]
    if unknown or args.reps < 1:
        raise SystemExit(f"eval.matrix: arms are {', '.join(ARMS)}, and --reps is at least 1" + (f" (not {', '.join(unknown)})" if unknown else ""))
    flags = [{"on": True, "off": False}[t.strip()] for t in args.tags.split(",") if t.strip() in ("on", "off")] or [True]
    label = args.label or datetime.now(timezone.utc).strftime("m%Y%m%dT%H%M%SZ")
    plan = cells(arms, checkpoints, flags, args.reps)
    env = dict(os.environ)
    if args.runs_dir:
        env["SMALLS_RUNS_DIR"] = str(Path(args.runs_dir).resolve())
    print(f"matrix {label}: {len(plan)} run{'s' if len(plan) != 1 else ''}, one process each")
    print(resolved(set_name=args.set, roles=args.role, profile=args.profile, effort=args.effort))
    if args.dry_run:
        for cell in plan:
            print(" ".join(command(cell, label=label, bundle=args.bundle, profile=args.profile, script=args.script, trigger=args.trigger,
                                   set_name=args.set, roles=args.role, effort=args.effort)))
        return 0
    from smalls.agent.config import load_config

    runs_dir = Path(args.runs_dir).resolve() if args.runs_dir else load_config(profile=args.profile).runs_dir
    rows = []
    for n, cell in enumerate(plan, 1):
        name = run_id(label, cell)
        started = time.perf_counter()
        done = subprocess.run(command(cell, label=label, bundle=args.bundle, profile=args.profile, script=args.script, trigger=args.trigger,
                                      set_name=args.set, roles=args.role, effort=args.effort),
                              cwd=ROOT, env=env, capture_output=True, text=True)
        summary = read_summary(runs_dir, name)
        rows.append({**cell, "run_id": name, "exit": done.returncode, "summary": summary})
        print(f"  {n}/{len(plan)} {name}: {summary.get('status')} in {time.perf_counter() - started:.1f}s"
              + (f" ({summary.get('stop_reason')})" if summary.get("stop_reason") else ""))
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{label}.matrix.json").write_text(json.dumps({"label": label, "bundle": args.bundle, "profile": args.profile, "runs": rows}, indent=2),
                                                   encoding="utf-8")
    print("\n" + table(rows))
    return 0 if all(row["summary"].get("status") in ("completed", "incomplete") for row in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
