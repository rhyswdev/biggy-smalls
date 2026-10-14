"""The scores of a set of runs as one table (row S6 of docs/decisions/eval-loop.md).

    python -m eval.table <run id or folder>...
    python -m eval.table --batch <name>          # the runs of a batch in eval/results/runs.jsonl

Each measure is a count over the runs ("4 of 5"), or the median of a per-run value with its range. PLAN 12's
wording rules apply: counts, never percentages, and no claim that one number beats another. A run with no
`scores.json` is scored here without the judge, and the table says so.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Callable

from eval.score import find
from eval.scorer import score_run

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "eval" / "results" / "runs.jsonl"


def load(folder: Path) -> dict[str, Any]:
    path = folder / "scores.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {**score_run(folder), "judge": None}


def _median(values: list[float]) -> str:
    if not values:
        return "none"
    m, lo, hi = statistics.median(values), min(values), max(values)
    fmt = (lambda v: f"{v:g}")
    return fmt(m) if lo == hi else f"{fmt(m)} ({fmt(lo)} to {fmt(hi)})"


def _count(runs: list[dict[str, Any]], test: Callable[[dict[str, Any]], bool | None]) -> str:
    asked = [r for r in runs if test(r) is not None]
    return f"{sum(1 for r in asked if test(r))} of {len(asked)}" if asked else "not asked"


def _j(r: dict[str, Any]) -> dict[str, Any] | None:
    j = r.get("judge")
    return j if j and "mechanism" in j else None


ROWS: list[tuple[str, Callable[[list[dict[str, Any]]], str]]] = [
    ("Runs scored", lambda rs: f"{len(rs)}, {sum(1 for r in rs if _j(r))} judged"),
    ("Reached the cause with non-chat support", lambda rs: _count(rs, lambda r: r["bar"]["cause_with_non_chat"])),
    ("Cause score", lambda rs: _median([r["cause"]["score"] for r in rs])),
    ("Trigger score", lambda rs: _median([r["trigger"]["score"] for r in rs if r.get("trigger")])),
    ("Hard fails (wrong cause leading above the key's maximum)", lambda rs: _count(rs, lambda r: r["confidence"]["hard_fail"])),
    ("Soft fails (right cause below the key's minimum)", lambda rs: _count(rs, lambda r: r["confidence"]["soft_fail"])),
    ("Citation integrity held (no fabricated or future handle)", lambda rs: _count(rs, lambda r: r["integrity"]["ok"])),
    ("Fabricated, future, unseen handles (totals)", lambda rs: ", ".join(str(sum(len(r["integrity"][k]) for r in rs))
                                                                          for k in ("fabricated", "future", "unseen"))),
    ("Required facts found, per run", lambda rs: _median([r["facts"]["required"] for r in rs]) + f" of {rs[0]['facts']['required_of']}"),
    ("Traps passed, per run", lambda rs: _median([r["traps"]["passed"] for r in rs]) + f" of {rs[0]['traps']['of']}"),
    ("Owner from on-call", lambda rs: _count(rs, lambda r: None if r["owner"] is None else r["owner"]["pass"])),
    ("Contributing factor held", lambda rs: _count(rs, lambda r: None if r["contributing"] is None else r["contributing"]["found"] > 0)),
    ("Causal links with non-chat support (totals)", lambda rs: f"{sum(r['causal_links']['with_non_chat'] for r in rs)} of "
                                                            f"{sum(r['causal_links']['links'] for r in rs)}"),
    ("Alert streams placed in or out of the incident rightly (totals)", lambda rs: f"{sum(r['alerts']['side_right'] for r in rs)} of "
                                                                               f"{sum(r['alerts']['given'] for r in rs)}"),
    ("Alert streams owed and missing (totals)", lambda rs: str(sum(len(r["alerts"]["missing"]) for r in rs))),
    ("PLAN 13 agent bar met", lambda rs: _count(rs, lambda r: r["bar"]["met"])),
    ("Tool calls to the first correct leading cause", lambda rs: _median([r["first_correct"]["tool_calls"] for r in rs if r.get("first_correct")])
     + f"; never in {sum(1 for r in rs if not r.get('first_correct'))}"),
    ("Model turns to the first correct leading cause", lambda rs: _median([r["first_correct"]["model_turns"] for r in rs if r.get("first_correct")])),
    ("Judge: mechanism items stated, per run", lambda rs: _median([_j(r)["mechanism"]["yes"] for r in rs if _j(r)])),
    ("Judge: required actions recommended, per run", lambda rs: _median([_j(r)["actions"]["yes"] for r in rs if _j(r)])),
    ("Judge: runs recommending a harmful step in the top three", lambda rs: _count(rs, lambda r: None if not _j(r) else _j(r)["harmful_top3"]["yes"] > 0)),
    ("Judge: claims supported, partial, unsupported (totals)", lambda rs: ", ".join(
        str(sum(_j(r)["claims"][k] for r in rs if _j(r))) for k in ("supported", "partial", "unsupported"))),
]


def table(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "no runs"
    out = ["| Measure | Value |", "|---|---|"]
    for name, measure in ROWS:
        try:
            value = measure(runs)
        except (KeyError, TypeError):
            value = "not scored"
        out.append(f"| {name} | {value} |")
    return "\n".join(out)


def batch_runs(name: str, ledger: Path = LEDGER) -> list[str]:
    rows = [json.loads(x) for x in ledger.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [r["run_id"] for r in rows if r.get("batch") == name]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.table", description="The scores of a set of runs, as counts.")
    p.add_argument("runs", nargs="*")
    p.add_argument("--batch", action="append", default=[], help="every run of a ledger batch; may repeat")
    args = p.parse_args(argv)
    ids = list(args.runs) + [r for b in args.batch for r in batch_runs(b)]
    if not ids:
        p.error("name runs or a --batch")
    print(table([load(find(r)) for r in ids]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
