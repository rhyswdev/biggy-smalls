"""The results ledger and the tuning log (docs/decisions/eval-loop.md, ruling V7 and row P2).

Every live run gets one row in `eval/results/runs.jsonl`, built from its run record by code. A row names each
role's model and its profile; the log shows profiles only, because model names live in config alone. The record itself
stays local (a git-ignored `eval/loop-runs/`), so the UI's recorded chats are not flooded; the row is what is
committed. `docs/tuning-log.md` is generated from the rows and from `eval/results/batches.yaml`, which says
what each batch was for: one table per batch, tied to its pass and commit, and a line per measure across
batches, so a measure can be followed from pass to pass.

    python -m eval.results add <run folder>... --batch <name> [--pass <n>]
    python -m eval.results report [--out docs/tuning-log.md]

A row holds no judgement of right or wrong. Until the scorer lands, `scores` is empty; when it lands it fills
`scores` for every row, and the log shows them beside the rest.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable

import yaml

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "eval" / "results" / "runs.jsonl"
BATCHES = ROOT / "eval" / "results" / "batches.yaml"
LOG = ROOT / "docs" / "tuning-log.md"
FRAMING = ROOT / "eval" / "framing"
ROLE_OF_NODE = {"agent": "investigator", "fallback": "investigator", "verifier_claims": "claims", "verifier_contrarian": "contrarian"}
BOARD_REFUSAL = "the board has not changed"


def _read(folder: Path, name: str, default: Any = None) -> Any:
    try:
        return json.loads((folder / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _lines(folder: Path, name: str) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in (folder / name).read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def _roles(run: dict[str, Any], key: str) -> dict[str, str | None]:
    """Each role as `<model or profile>@effort`, by `key`. A record made before roles existed names the
    investigator, and the two verifier passes by profile only."""
    config = run.get("config") or {}
    roles = config.get("roles")
    if roles:
        return {name: (f"{r[key]}@{r['effort']}" if r.get("effort") else r[key]) if r else None for name, r in roles.items()}
    params = config.get("params") or {}
    effort = params.get("effort") or (params.get("reasoning") or {}).get("effort")
    out: dict[str, str | None] = {"investigator": f"{run.get(key)}@{effort}" if effort else run.get(key)}
    for role, profile in (run.get("verifier") or {}).items():
        out[role] = profile if key == "profile" else None       # the record kept no model for these
    return out


def trigger_kind(run: dict[str, Any], bundles: Path = ROOT / "bundles") -> str:
    """`standard` when the run was given its checkpoint's own trigger file, `framing:<name>` for a trigger from
    `eval/framing/`, else `custom` (a trigger typed by hand)."""
    given = (run.get("trigger") or "").strip()
    standard = bundles / str(run.get("bundle") or "") / "triggers" / f"{run.get('checkpoint')}.md"
    if standard.is_file() and standard.read_text(encoding="utf-8").strip() == given:
        return "standard"
    for path in sorted(FRAMING.glob("*.md")):
        if path.read_text(encoding="utf-8").strip() == given:
            return f"framing:{path.stem}"
    return "custom"


def row(folder: Path | str, *, batch: str, pass_: str | int | None, phase: str | int | None = None,
        bundles: Path = ROOT / "bundles") -> dict[str, Any]:
    """One ledger row from a run record, by code alone."""
    folder = Path(folder)
    run = _read(folder, "run.json")
    if not run:
        raise SystemExit(f"{folder}: no run.json, so not a run record")
    summary = _read(folder, "summary.json", {}) or {}
    report = _read(folder, "report.json", {}) or {}
    verdict = (_read(folder, "verdict.json", {}) or {}).get("verdict") or {}
    usage = _read(folder, "usage.json", {}) or {}
    process = summary.get("process") or {}
    tokens = summary.get("tokens") or {}
    events = _lines(folder, "events.jsonl")
    calls = _lines(folder, "calls.jsonl")

    by_role: Counter[str] = Counter()
    for call in (usage.get("calls") if isinstance(usage, dict) else usage) or []:
        by_role[ROLE_OF_NODE.get(call.get("node"), call.get("node") or "unknown")] += call.get("cost_usd") or 0.0
    hypotheses = report.get("hypotheses") or []
    lead = next((h for h in hypotheses if h.get("status") == "leading"), None)
    decisions = [e.get("data") or {} for e in events if e.get("type") == "gate_decision"]
    claims = [c.get("verdict") for c in ((verdict.get("claims") or {}).get("claims") or [])]
    e9 = "malformed_shown" in (run.get("context") or {})        # calls answered out of sight exist from E9 on
    return {
        "run_id": run["run_id"], "date": str(run.get("created", ""))[:10], "created": run.get("created"),
        "batch": batch, "pass": None if pass_ is None else str(pass_), "phase": None if phase is None else str(phase),
        "commit": run.get("commit"), "prompt_sha": run.get("prompt_sha"), "tools_sha": run.get("tools_sha"),
        "set": (run.get("config") or {}).get("set"), "roles": _roles(run, "model"), "profiles": _roles(run, "profile"),
        "arm": run.get("arm"), "checkpoint": run.get("checkpoint"), "tags": run.get("tags"), "trigger": trigger_kind(run, bundles),
        "status": summary.get("status"), "stop_reason": summary.get("stop_reason"),
        "wall_s": summary.get("wall_s_total"), "model_turns": process.get("model_turns"), "tool_calls": process.get("tool_calls"),
        "policy_refusals": sum(1 for d in decisions if (d.get("by") or {}).get("who") == "policy" and d.get("decision") == "rejected"),
        "board_refusals": sum(1 for d in decisions if d.get("decision") == "rejected" and BOARD_REFUSAL in str(d.get("reason") or "")),
        "malformed_retries": sum(1 for c in calls if c.get("hidden")) if e9 else None,
        "quiet_board": process.get("quiet_board"),
        "cost_usd": tokens.get("cost_usd"), "cost_by_role": {k: round(v, 6) for k, v in sorted(by_role.items())},
        "cache_share": round(tokens["cache_read"] / tokens["input_tokens"], 4) if tokens.get("input_tokens") else None,
        "leading": {"subject": lead.get("subject"), "confidence": lead.get("confidence"), "role": lead.get("role")} if lead else None,
        "rejected": [h.get("subject") for h in hypotheses if h.get("status") == "rejected"],
        "open": [h.get("subject") for h in hypotheses if h.get("status") == "open"],
        "verifier": {"status": verdict.get("status"), "claims": len(claims), "supported": claims.count("supported"),
                     "partial": claims.count("partial"), "unsupported": claims.count("unsupported"),
                     "disputed": len((summary.get("validation") or {}).get("disputed") or []), "revisions": summary.get("revisions")}
                    if verdict else None,
        "validation_ok": (report.get("validation") or {}).get("ok"),
        "scores": {},
    }


def load(path: Path = LEDGER) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def save(rows: list[dict[str, Any]], path: Path = LEDGER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda r: (str(r.get("created") or ""), r["run_id"]))
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def batches(path: Path = BATCHES) -> list[dict[str, Any]]:
    return list((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("batches") or []) if path.exists() else []


def add(folders: list[str], *, batch: str, pass_: str | None = None, ledger: Path = LEDGER, batches_file: Path = BATCHES,
        bundles: Path = ROOT / "bundles") -> list[dict[str, Any]]:
    """Add or replace the rows for these run records. The batch must be described in `batches.yaml` first."""
    known = {b["name"]: b for b in batches(batches_file)}
    if batch not in known:
        raise SystemExit(f"no batch named {batch!r} in {batches_file}; describe it there first")
    meta = known[batch]
    new = [row(Path(f), batch=batch, pass_=pass_ if pass_ is not None else meta.get("pass"), phase=meta.get("phase"), bundles=bundles)
           for f in folders]
    ids = {r["run_id"] for r in new}
    save([r for r in load(ledger) if r["run_id"] not in ids] + new, ledger)
    return new


# -- the tuning log ----------------------------------------------------------------------------------------------

def _median(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _n(v: float) -> str:
    """A count, or the median of counts: whole numbers without a decimal point."""
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def _spread(values: list[Any], fmt: Callable[[Any], str] = _n) -> str:
    values = [v for v in values if v is not None]
    if not values:
        return "-"
    lo, mid, hi = min(values), statistics.median(values), max(values)
    return fmt(mid) if lo == hi else f"{fmt(mid)} ({fmt(lo)} to {fmt(hi)})"


def _partial(rows: list[dict[str, Any]]) -> str:
    claims = sum((r.get("verifier") or {}).get("claims") or 0 for r in rows)
    partial = sum((r.get("verifier") or {}).get("partial") or 0 for r in rows)
    return f"{partial} of {claims}" if claims else "-"


def _nudges(r: dict[str, Any]) -> int | None:
    q = r.get("quiet_board")
    return len(q.get("nudged_rounds") or []) if q else None


def _lead(r: dict[str, Any]) -> str:
    lead = r.get("leading")
    return f"{lead['subject']} {lead['confidence']:.2f}" if lead and lead.get("confidence") is not None else "none"


def _same(text: str) -> str:
    """A subject as the log groups it: the model writes one name in several cases and separators."""
    return " ".join(text.lower().replace("-", " ").replace("_", " ").split())


def _leads(rows: list[dict[str, Any]]) -> str:
    """Leading hypotheses by subject and confidence, each group under the first spelling seen."""
    counts: Counter[str] = Counter()
    shown: dict[str, str] = {}
    for r in rows:
        key = _same(_lead(r))
        shown.setdefault(key, _lead(r))
        counts[key] += 1
    return "; ".join(f"{shown[k]} ×{n}" for k, n in counts.most_common())


MEASURES: list[tuple[str, Callable[[list[dict[str, Any]]], str]]] = [
    ("Runs", lambda rs: str(len(rs))),
    ("Completed", lambda rs: f"{sum(1 for r in rs if r.get('status') == 'completed')} of {len(rs)}"),
    ("Leading hypothesis", _leads),
    ("Tool calls", lambda rs: _spread([r.get("tool_calls") for r in rs])),
    ("Model turns", lambda rs: _spread([r.get("model_turns") for r in rs])),
    ("Cost per run, USD", lambda rs: _spread([r.get("cost_usd") for r in rs], lambda v: f"{v:.2f}")),
    ("Wall time, s", lambda rs: _spread([r.get("wall_s") for r in rs], lambda v: f"{v:.0f}")),
    ("Cache share", lambda rs: _spread([r.get("cache_share") for r in rs], lambda v: f"{v:.2f}")),
    ("Partial claims", _partial),
    ("Unsupported claims per run", lambda rs: _spread([(r.get("verifier") or {}).get("unsupported") for r in rs])),
    ("Policy refusals per run", lambda rs: _spread([r.get("policy_refusals") for r in rs])),
    ("Board refusals per run", lambda rs: _spread([r.get("board_refusals") for r in rs])),
    ("Quiet-board nudges per run", lambda rs: _spread([_nudges(r) for r in rs])),
    ("Malformed calls retried per run", lambda rs: _spread([r.get("malformed_retries") for r in rs])),
]

COLUMNS: list[tuple[str, Callable[[dict[str, Any]], str]]] = [
    ("Run", lambda r: f"`{r['run_id']}`"),
    ("At", lambda r: str(r.get("checkpoint"))),
    ("Trigger", lambda r: str(r.get("trigger"))),
    ("Investigator", lambda r: str((r.get("profiles") or {}).get("investigator"))),
    ("Status", lambda r: str(r.get("status"))),
    ("Leading", _lead),
    ("Rejected", lambda r: str(len(r.get("rejected") or []))),
    ("Calls", lambda r: str(r.get("tool_calls"))),
    ("Turns", lambda r: str(r.get("model_turns"))),
    ("Cost", lambda r: f"${r['cost_usd']:.2f}" if r.get("cost_usd") is not None else "-"),
    ("Wall s", lambda r: f"{r['wall_s']:.0f}" if r.get("wall_s") is not None else "-"),
    ("Cache", lambda r: f"{r['cache_share']:.2f}" if r.get("cache_share") is not None else "-"),
    ("Claims s/p/u", lambda r: "/".join(str((r.get("verifier") or {}).get(k, "-")) for k in ("supported", "partial", "unsupported")) if r.get("verifier") else "-"),
    ("Refused", lambda r: str(r.get("policy_refusals"))),
    ("Nudged", lambda r: "-" if _nudges(r) is None else f"{_nudges(r)} ({(r.get('quiet_board') or {}).get('moved_next_round', 0)} moved)"),
]


def _ordered(rows: list[dict[str, Any]], described: list[dict[str, Any]]) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Batches in the order of their first run, each with its description and rows."""
    by_batch: dict[str, list[dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: (str(r.get("created") or ""), r["run_id"])):
        by_batch.setdefault(r["batch"], []).append(r)
    meta = {b["name"]: b for b in described}
    return [(meta.get(name, {"name": name}), rs) for name, rs in by_batch.items()]


def render(rows: list[dict[str, Any]], described: list[dict[str, Any]]) -> str:
    ordered = _ordered(rows, described)
    out = ["# Tuning log", "",
           "Generated by `python -m eval.results report` from the results ledger (`eval/results/runs.jsonl`) and the batch",
           "descriptions (`eval/results/batches.yaml`). Do not edit it by hand: add rows with `python -m eval.results add`.",
           "",
           "Each batch is a set of live runs made for one purpose, on one version of the prompt files (its pass). Until the",
           "scorer lands, nothing here says whether a run was right: the leading hypothesis is shown as the run left it, and",
           "PLAN 1.1 and 1.5 are what a reader judges it against. Counts, never percentages. A spread is the median, then the",
           "lowest and highest run.",
           "",
           f"{len(rows)} runs in {len(ordered)} batches; ${sum(r.get('cost_usd') or 0 for r in rows):.2f} in all.",
           "",
           "## Across batches", "",
           "One line per measure, batches in the order they ran.", ""]
    for name, measure in MEASURES:
        out.append(f"- **{name}.** " + " · ".join(f"{meta['name']}: {measure(rs)}" for meta, rs in ordered))
    out += ["", "## Batches", ""]
    for meta, rs in ordered:
        commits = ", ".join(dict.fromkeys(f"`{r['commit']}`" for r in rs if r.get("commit")))
        prompts = ", ".join(dict.fromkeys(f"`{r['prompt_sha']}`" for r in rs if r.get("prompt_sha")))
        sets = ", ".join(dict.fromkeys(str(r.get("set") or "none, from before sets") for r in rs))
        out += [f"### {meta['name']}", ""]
        if meta.get("what"):
            out += [str(meta["what"]).strip(), ""]
        facts = [f"phase {meta.get('phase', '?')}", f"pass {meta.get('pass', '?')}"]
        if meta.get("finding"):
            facts.append(f"finding {meta['finding']}")
        if meta.get("result"):
            facts.append(f"result: {meta['result']}")
        out += ["; ".join(facts) + f". Commit {commits}; prompt files {prompts}; set {sets}.", ""]
        out.append("| " + " | ".join(name for name, _ in COLUMNS) + " |")
        out.append("|" + "---|" * len(COLUMNS))
        out += ["| " + " | ".join(cell(r) for _, cell in COLUMNS) + " |" for r in rs]
        out += ["", "; ".join(f"{name}: {measure(rs)}" for name, measure in MEASURES[3:]) + ".", ""]
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.results", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("add", help="add or replace the ledger rows for run records")
    a.add_argument("folders", nargs="+")
    a.add_argument("--batch", required=True)
    a.add_argument("--pass", dest="pass_", help="the pass whose prompt files the runs used (default: the batch's)")
    r = sub.add_parser("report", help="write the tuning log from the ledger")
    r.add_argument("--out", default=str(LOG))
    args = p.parse_args(argv)
    if args.command == "add":
        for new in add(args.folders, batch=args.batch, pass_=args.pass_):
            print(f"{new['run_id']}: {new['status']}, {new['tool_calls']} calls, ${new.get('cost_usd') or 0:.2f}, leading {_lead(new)}")
        return 0
    text = render(load(), batches())
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"tuning log: {len(load())} runs written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
