"""Score runs, or a hand-written report (row S6 of docs/decisions/eval-loop.md).

    python -m eval.score <run id or folder>... [--no-judge]       # writes scores.json into each run's folder
    python -m eval.score --report <file.json> [--checkpoint 1452]  # prints a hand-written report's scores

A run id is looked for in `eval/loop-runs/` and then `eval/runs/`. `scores.json` holds the deterministic checks
(`eval/scorer.py`) and the judge's part (`eval/judge.py`), with the versions of both, so a score can be traced
to the grader that gave it. The judge spends money: it stops at the budget in `eval/scorer.toml`.

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


def score_folder(folder: Path, *, with_judge: bool = True, script: judging.Script | None = None) -> dict[str, Any]:
    scores = score_run(folder)
    run = json.loads((folder / "run.json").read_text(encoding="utf-8")) if (folder / "run.json").exists() else {}
    report = json.loads((folder / "report.json").read_text(encoding="utf-8")) if (folder / "report.json").exists() else None
    cp = load_key(run.get("bundle") or DEFAULT_BUNDLE).checkpoint(str(run.get("checkpoint") or ""))
    if with_judge and report and cp is not None:
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
    args = p.parse_args(argv)
    if args.report:
        print(line(score_hand(args.report, args.checkpoint, with_judge=not args.no_judge)))
        return 0
    if not args.runs:
        p.error("name at least one run, or --report")
    for run in args.runs:
        folder = find(run)
        print(line(score_folder(folder, with_judge=not args.no_judge)))
    if not args.no_judge:
        print(f"judge spend so far: ${judging.spent():.2f} of ${float(judging.config().get('budget_usd', 5)):.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
