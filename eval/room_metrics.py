"""A summary of scores that is safe for the iteration room (row S7 of docs/decisions/eval-loop.md): what the
room's `METRICS.md` holds.

    python -m eval.room_metrics <run id or folder>... [--batch <name>] --out <file>

It is aggregate counts per eval layer and nothing else: no trap or fact name, no handle, no subject, no time,
no run id. Every line is a fixed sentence with numbers in it; no text is copied from a score. The output is then
checked as a prompt would be (the bundle-string lint, plus a check for handles and clock times), and is refused
if anything is found, so a room never receives a summary that carries the incident.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
from pathlib import Path
from typing import Any

from eval.score import find
from eval.table import _j, batch_runs, load
from smalls.contracts.handles import is_handle

TIME = re.compile(r"\b\d{1,2}:\d{2}\b")


def _med(values: list[int | float]) -> str:
    if not values:
        return "none"
    m, lo, hi = statistics.median(values), min(values), max(values)
    return f"{m:g}" if lo == hi else f"{m:g} (range {lo:g} to {hi:g})"


def _n(x: Any) -> int:
    """A count, whatever came in: only numbers leave this module."""
    return int(x) if isinstance(x, (int, float, bool)) else len(x) if isinstance(x, (list, dict, set)) else 0


def summary(runs: list[dict[str, Any]]) -> str:
    n = len(runs)
    judged = [_j(r) for r in runs if _j(r)]
    fc = [r["first_correct"] for r in runs if r.get("first_correct")]
    lines = [
        "# Scores over recent runs",
        "",
        f"Aggregate counts over {n} runs, {len(judged)} of them judged. No run, entity, record or time is named.",
        "",
        "## Outcome",
        f"- Reached the cause with support that is not chat: {sum(_n(r['bar']['cause_with_non_chat']) for r in runs)} of {n} runs.",
        f"- Cause score per run (0, 0.5 or 1): median {_med([float(r['cause']['score']) for r in runs])}.",
        f"- Trigger score per run: median {_med([float(r['trigger']['score']) for r in runs if r.get('trigger')])}.",
        f"- Hard fails (a wrong leading hypothesis above the confidence limit): {sum(_n(r['confidence']['hard_fail']) for r in runs)} of {n}.",
        f"- Soft fails (the right leading hypothesis below the confidence floor): {sum(_n(r['confidence']['soft_fail']) for r in runs)} of {n}.",
        f"- Required facts cited with support that is not chat, per run: median {_med([_n(r['facts']['required']) for r in runs])} "
        f"of {max((_n(r['facts']['required_of']) for r in runs), default=0)}.",
        f"- Traps passed per run: median {_med([_n(r['traps']['passed']) for r in runs])} of {max((_n(r['traps']['of']) for r in runs), default=0)}; "
        f"runs that fell for at least one: {sum(1 for r in runs if _n(r['traps']['passed']) < _n(r['traps']['of']))} of {n}.",
        f"- Owner named from on-call: {sum(1 for r in runs if r.get('owner') and r['owner'].get('pass') is True)} of "
        f"{sum(1 for r in runs if r.get('owner') is not None)} runs where it was asked.",
        "",
        "## Grounding",
        f"- Runs with no fabricated or future citation: {sum(_n(r['integrity']['ok']) for r in runs)} of {n}; fabricated "
        f"{sum(_n(r['integrity']['fabricated']) for r in runs)}, future {sum(_n(r['integrity']['future']) for r in runs)}, "
        f"cited but never shown {sum(_n(r['integrity']['unseen']) for r in runs)}, in total.",
        f"- Links of the stated chain with support that is not chat: {sum(_n(r['causal_links']['with_non_chat']) for r in runs)} of "
        f"{sum(_n(r['causal_links']['links']) for r in runs)}.",
        f"- Claims judged supported, partial, unsupported: {sum(_n(j['claims']['supported']) for j in judged)}, "
        f"{sum(_n(j['claims']['partial']) for j in judged)}, {sum(_n(j['claims']['unsupported']) for j in judged)}.",
        f"- Alert streams placed in or out of the incident rightly: {sum(_n(r['alerts']['side_right']) for r in runs)} of "
        f"{sum(_n(r['alerts']['given']) for r in runs)}; streams owed a verdict and missing: {sum(_n(r['alerts']['missing']) for r in runs)}.",
        "",
        "## Recommendations, as the judge read them",
        f"- Parts of the mechanism stated, per run: median {_med([_n(j['mechanism']['yes']) for j in judged])} "
        f"of {max((_n(j['mechanism']['of']) for j in judged), default=0)}.",
        f"- Required actions recommended, per run: median {_med([_n(j['actions']['yes']) for j in judged])} "
        f"of {max((_n(j['actions']['of']) for j in judged), default=0)}.",
        f"- Runs recommending a harmful step among the top three: {sum(1 for j in judged if _n(j['harmful_top3']['yes']) > 0)} of {len(judged)}.",
        "",
        "## Process",
        f"- Tool calls before the right cause first led: median {_med([_n(f['tool_calls']) for f in fc])}; it never led in {n - len(fc)} runs.",
        f"- Model turns before it first led: median {_med([_n(f['model_turns']) for f in fc])}.",
        f"- The agent bar of the plan met in full: {sum(_n(r['bar']['met']) for r in runs)} of {n}.",
    ]
    return "\n".join(lines) + "\n"


def leaks(text: str) -> list[str]:
    """What in the text a room must not see: a bundle string, a handle or a clock time."""
    from smalls_data.prompt_lint import find as bundle_hits

    found = bundle_hits([("METRICS.md", text)])
    found += [f"handle {t!r}" for t in (w.strip(".,;:()") for w in text.split()) if ":" in t and is_handle(t)]
    found += [f"time {m!r}" for m in TIME.findall(text)]
    return found


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.room_metrics", description="Aggregate scores for the iteration room.")
    p.add_argument("runs", nargs="*")
    p.add_argument("--batch", action="append", default=[])
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    ids = list(args.runs) + [r for b in args.batch for r in batch_runs(b)]
    if not ids:
        p.error("name runs or a --batch")
    text = summary([load(find(r)) for r in ids])
    bad = leaks(text)
    if bad:
        raise SystemExit("room_metrics: refused, the summary carries " + "; ".join(bad))
    args.out.write_text(text, encoding="utf-8")
    print(f"room_metrics: {len(ids)} runs summarised into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
