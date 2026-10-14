"""Score runs, or a hand-written report (row S6 of docs/decisions/eval-loop.md).

    python -m eval.score <run id or folder>... [--no-judge]       # writes scores.json into each run's folder
    python -m eval.score --report <file.json> [--checkpoint 1452]  # prints a hand-written report's scores
    python -m eval.score --ledger                                  # each ledger row's `scores`, from its scores.json

A run id is looked for in `eval/loop-runs/` and then `eval/runs/`. `scores.json` holds the deterministic checks
(`eval/scorer.py`) and the judge's part (`eval/judge.py`), with the versions of both, so a score can be traced
to the grader that gave it. The judge spends money: it stops at the budget in `eval/scorer.toml`.

A run whose `scores.json` already holds this judge's verdict keeps it, so re-scoring after a change to the
deterministic checks costs nothing; `--rejudge` calls the judge again.

A hand-written report has no run behind it, so every handle the bundle holds at its `as_of` counts as shown,
and the judge reads its checklist only: there are no rows the run saw to judge its claims against.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from eval import judge as judging
from eval.key import DEFAULT_BUNDLE, checkpoint_time, load_key, load_manifest
from eval.scorer import RunLog, score_report, score_run

ROOT = Path(__file__).resolve().parents[1]
RUN_DIRS = (ROOT / "eval" / "loop-runs", ROOT / "eval" / "runs")


def find(run: str) -> Path:
    p = Path(run)
    if p.is_dir():
        return p
    for d in RUN_DIRS:
        if (d / run).is_dir():
            return d / run
    raise SystemExit(f"no run record named {run!r} in {', '.join(str(d.relative_to(ROOT)) for d in RUN_DIRS)}")


class HandLog(RunLog):
    """A hand-written report's stand-in for a run log: every handle visible at `as_of` was shown."""

    def __init__(self, bundle: str, as_of: str):
        m = load_manifest(bundle)
        self.shown = {h for h in m.rows if m.visible(h, as_of)}
        self.referenced, self.notes, self.alert_streams = set(), set(), []

    def seen(self, handle: str) -> bool:
        return handle in self.shown or handle.startswith(("q:", "metric:"))

    def minted(self, handle: str) -> bool:
        return self.seen(handle)


def score_folder(folder: Path, *, with_judge: bool = True, rejudge: bool = False, script: judging.Script | None = None) -> dict[str, Any]:
    scores = score_run(folder)
    run = json.loads((folder / "run.json").read_text(encoding="utf-8")) if (folder / "run.json").exists() else {}
    report = json.loads((folder / "report.json").read_text(encoding="utf-8")) if (folder / "report.json").exists() else None
    cp = load_key(run.get("bundle") or DEFAULT_BUNDLE).checkpoint(str(run.get("checkpoint") or ""))
    earlier = json.loads((folder / "scores.json").read_text(encoding="utf-8")).get("judge") if (folder / "scores.json").exists() else None
    if with_judge and report and cp is not None and not rejudge and earlier and earlier.get("judge") == judging.JUDGE_VERSION:
        scores["judge"] = earlier          # the same report, judged by the same judge: no second call
    elif with_judge and report and cp is not None:
        scores["judge"] = judging.judge(report, cp, judging.evidence_texts(folder), label=scores["run_id"], script=script)
    else:
        scores["judge"] = None
    (folder / "scores.json").write_text(json.dumps(scores, indent=1) + "\n", encoding="utf-8")
    return scores


def score_hand(path: Path, checkpoint: str, *, with_judge: bool = True, bundle: str = DEFAULT_BUNDLE,
               script: judging.Script | None = None) -> dict[str, Any]:
    report = json.loads(path.read_text(encoding="utf-8"))
    as_of = report.get("as_of") or checkpoint_time(bundle, checkpoint)
    scores = {"run_id": path.stem, "arm": "hand", "has_report": True,
              **score_report(report, checkpoint=checkpoint, as_of=as_of, log=HandLog(bundle, as_of), bundle=bundle)}
    cp = load_key(bundle).checkpoint(checkpoint)
    scores["judge"] = judging.judge(report, cp, {}, label=f"hand:{path.stem}", script=script) if with_judge and cp else None
    return scores


def compact(s: dict[str, Any]) -> dict[str, Any]:
    """What a ledger row keeps of a run's scores: counts and flags only, no handle, subject or trap name."""
    if "error" in s or not s.get("has_report"):
        return {"scorer": s.get("scorer"), "error": s.get("error") or "no report"}
    out: dict[str, Any] = {
        "scorer": s["scorer"], "cause": s["cause"]["score"], "trigger": (s.get("trigger") or {}).get("score"),
        "cause_non_chat": s["bar"]["cause_with_non_chat"], "hard_fail": s["confidence"]["hard_fail"], "soft_fail": s["confidence"]["soft_fail"],
        "integrity_ok": s["integrity"]["ok"], "fabricated": len(s["integrity"]["fabricated"]), "future": len(s["integrity"]["future"]),
        "unseen": len(s["integrity"]["unseen"]), "facts": [s["facts"]["required"], s["facts"]["required_of"]],
        "traps": [s["traps"]["passed"], s["traps"]["of"]], "owner": None if s["owner"] is None else s["owner"]["pass"],
        "contributing": None if s["contributing"] is None else s["contributing"]["found"] > 0,
        "links": [s["causal_links"]["with_non_chat"], s["causal_links"]["links"]],
        "alerts": [s["alerts"]["side_right"], s["alerts"]["given"], len(s["alerts"]["missing"])],
        "bar": s["bar"]["met"], "first_correct": s.get("first_correct"),
    }
    j = s.get("judge")
    if j and "mechanism" in j:
        out["judge"] = {"version": j["judge"], "mechanism": [j["mechanism"]["yes"], j["mechanism"]["of"]],
                        "actions": [j["actions"]["yes"], j["actions"]["of"]], "harmful_top3": j["harmful_top3"]["yes"],
                        "claims": {k: j["claims"][k] for k in ("supported", "partial", "unsupported", "of")}}
    return out


def fill_ledger(ledger: Path | None = None) -> tuple[int, list[str]]:
    """Write each row's `scores` from its run's `scores.json`. Rows whose record has none are left as they are."""
    from eval import results

    ledger = ledger or results.LEDGER
    rows = results.load(ledger)
    filled, missing = 0, []
    for row in rows:
        try:
            path = find(row["run_id"]) / "scores.json"
        except SystemExit:
            path = None
        if path is None or not path.exists():
            missing.append(row["run_id"])
            continue
        row["scores"] = compact(json.loads(path.read_text(encoding="utf-8")))
        filled += 1
    results.save(rows, ledger)
    return filled, missing


def line(s: dict[str, Any]) -> str:
    """One run's scores in a line, as counts."""
    if "error" in s:
        return f"{s['run_id']}: {s['error']}"
    t, f, a, c = s["traps"], s["facts"], s["alerts"], s["cause"]
    out = (f"{s['run_id']}: cause {c['score']:g}, trigger {'-' if s['trigger'] is None else format(s['trigger']['score'], 'g')}, "
           f"facts {f['required']} of {f['required_of']}, traps {t['passed']} of {t['of']}, "
           f"owner {'-' if s['owner'] is None else ('yes' if s['owner']['pass'] else 'no')}, "
           f"{'HARD FAIL, ' if s['confidence']['hard_fail'] else ''}"
           f"integrity {'ok' if s['integrity']['ok'] else 'FAILED'} ({len(s['integrity']['fabricated'])} fabricated, "
           f"{len(s['integrity']['future'])} future, {len(s['integrity']['unseen'])} unseen), "
           f"alerts {a['side_right']} of {a['given']} placed right, bar {'met' if s['bar']['met'] else 'not met'}")
    j = s.get("judge")
    if j and "mechanism" in j:
        cl = j["claims"]
        out += (f"; judge: mechanism {j['mechanism']['yes']} of {j['mechanism']['of']}, actions {j['actions']['yes']} of {j['actions']['of']}, "
                f"harmful in top 3 {j['harmful_top3']['yes']}, claims {cl['supported']} supported, {cl['partial']} partial, "
                f"{cl['unsupported']} unsupported of {cl['of']}")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.score", description="Score run records, or a hand-written report.")
    p.add_argument("runs", nargs="*", help="run ids or folders")
    p.add_argument("--report", type=Path, help="a hand-written report (JSON) to score instead of a run")
    p.add_argument("--checkpoint", default="1452")
    p.add_argument("--no-judge", action="store_true", help="the deterministic checks only; no model call")
    p.add_argument("--rejudge", action="store_true", help="call the judge again even where scores.json holds this judge's verdict")
    p.add_argument("--ledger", action="store_true", help="fill every ledger row's `scores` from its run's scores.json")
    args = p.parse_args(argv)
    if args.ledger:
        filled, missing = fill_ledger()
        print(f"ledger: {filled} rows scored" + (f"; no scores.json for {', '.join(missing)}" if missing else ""))
        return 0
    if args.report:
        print(line(score_hand(args.report, args.checkpoint, with_judge=not args.no_judge)))
        return 0
    if not args.runs:
        p.error("name at least one run, or --report")
    for run in args.runs:
        folder = find(run)
        print(line(score_folder(folder, with_judge=not args.no_judge, rejudge=args.rejudge)))
    if not args.no_judge:
        print(f"judge spend so far: ${judging.spent():.2f} of ${float(judging.config().get('budget_usd', 5)):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
