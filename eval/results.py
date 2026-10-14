"""The results ledger and the tuning log (docs/decisions/eval-loop.md, ruling V7 and row P2).

Every live run gets one row in `eval/results/runs.jsonl`, built from its run record by code. A row names each
role's model and its profile; the log shows profiles only, because model names live in config alone. The record itself
stays local (a git-ignored `eval/loop-runs/`), so the UI's recorded chats are not flooded; the row is what is
committed. `docs/tuning-log.md` is generated from the rows and from `eval/results/batches.yaml`, which says
what each batch was for: one table per batch, tied to its pass and commit, and a line per measure across
batches, so a measure can be followed from pass to pass.

    python -m eval.results add <run folder>... --batch <name> [--pass <n>]
    python -m eval.results report [--out docs/tuning-log.md]
    python -m eval.results rebuild                # every row again from its record, when a row gains a field
    python -m eval.results export <folder>        # runs.json, batches.json and notes.json for the published page

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
NOTES = ROOT / "eval" / "results" / "notes.yaml"
LOG = ROOT / "docs" / "tuning-log.md"
FRAMING = ROOT / "eval" / "framing"
ROLE_OF_NODE = {"agent": "investigator", "fallback": "investigator", "verifier_claims": "claims", "verifier_recheck": "claims",
                "verifier_contrarian": "contrarian"}
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


def _board_only(calls: list[dict[str, Any]]) -> int:
    """Model turns whose only calls were board writes (the mode of F-01)."""
    rounds: dict[int, set[str]] = {}
    for c in calls:
        if c.get("by") == "agent":
            rounds.setdefault(c.get("round") or 0, set()).add(c.get("tool"))
    return sum(1 for tools in rounds.values() if tools == {"update_board"})


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
        "carried_from": run.get("carried_from"),
        "status": summary.get("status"), "stop_reason": summary.get("stop_reason"),
        "wall_s": summary.get("wall_s_total"), "model_turns": process.get("model_turns"), "tool_calls": process.get("tool_calls"),
        "policy_refusals": sum(1 for d in decisions if (d.get("by") or {}).get("who") == "policy" and d.get("decision") == "rejected"),
        "board_refusals": sum(1 for d in decisions if d.get("decision") == "rejected" and BOARD_REFUSAL in str(d.get("reason") or "")),
        "malformed_retries": sum(1 for c in calls if c.get("hidden")) if e9 else None,
        "quiet_board": process.get("quiet_board"),
        "cost_usd": tokens.get("cost_usd"), "cost_by_role": {k: round(v, 6) for k, v in sorted(by_role.items())},
        "cache_share": round(tokens["cache_read"] / tokens["input_tokens"], 4) if tokens.get("input_tokens") else None,
        "leading": {"subject": lead.get("subject"), "confidence": lead.get("confidence"), "role": lead.get("role")} if lead else None,
        "hypotheses": [{k: h.get(k) for k in ("subject", "role", "status", "confidence")} for h in hypotheses],
        "board_only_turns": _board_only(calls),
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


RECORDS = (ROOT / "eval" / "loop-runs", ROOT / "eval" / "runs")


def rebuild(ledger: Path = LEDGER, batches_file: Path = BATCHES, records: tuple[Path, ...] = RECORDS) -> tuple[int, list[str]]:
    """Build every row again from its run record, keeping its batch and pass: for when a row gains a field.
    A row whose record is not on this machine is kept as it is, and named."""
    meta = {b["name"]: b for b in batches(batches_file)}
    out, missing = [], []
    for old in load(ledger):
        folder = next((d / old["run_id"] for d in records if (d / old["run_id"] / "run.json").exists()), None)
        if folder is None:
            out.append(old)
            missing.append(old["run_id"])
            continue
        new = row(folder, batch=old["batch"], pass_=old.get("pass"), phase=(meta.get(old["batch"]) or {}).get("phase", old.get("phase")))
        out.append({**new, "scores": old.get("scores") or {}})
    save(out, ledger)
    return len(out), missing


def _score_fields(r: dict[str, Any]) -> dict[str, Any]:
    """A run's scores, flat, for the page: empty when the run is not scored or left no report."""
    sc = r.get("scores") or {}
    if not sc.get("scorer") or "error" in sc:
        return {"scored": 0}
    j = sc.get("judge") or {}
    pair = lambda k, i: (sc.get(k) or [None, None])[i]
    return {"scored": 1, "cause": sc.get("cause"), "cause_non_chat": int(bool(sc.get("cause_non_chat"))), "hard_fail": int(bool(sc.get("hard_fail"))),
            "bar": int(bool(sc.get("bar"))), "facts_found": pair("facts", 0), "facts_of": pair("facts", 1),
            "traps_passed": pair("traps", 0), "traps_of": pair("traps", 1), "alerts_right": pair("alerts", 0), "alerts_of": pair("alerts", 1),
            "first_correct_calls": (sc.get("first_correct") or {}).get("tool_calls"), "judged": int(bool(j)),
            "mechanism": (j.get("mechanism") or [None])[0] if j else None, "actions": (j.get("actions") or [None])[0] if j else None,
            "judged_supported": (j.get("claims") or {}).get("supported") if j else None,
            "judged_partial": (j.get("claims") or {}).get("partial") if j else None,
            "judged_unsupported": (j.get("claims") or {}).get("unsupported") if j else None}


def export(folder: Path, rows: list[dict[str, Any]] | None = None, described: list[dict[str, Any]] | None = None) -> list[Path]:
    """Two flat JSON files for the published tuning-log page: one row per run, and one per batch. Profiles,
    never models, as in the log."""
    rows = rows if rows is not None else load()
    described = described if described is not None else batches()
    ordered = _ordered(rows, described)
    batch_rows, run_rows = [], []
    for i, (meta, rs) in enumerate(ordered, 1):
        batch_rows.append({"batch": meta["name"], "order": i, "phase": str(meta.get("phase", "")), "pass": str(meta.get("pass", "")),
                           "finding": meta.get("finding") or "", "result": meta.get("result") or "",
                           "what": " ".join(str(meta.get("what") or "").split()), "moved": " ".join(str(meta.get("moved") or "").split()),
                           "runs": len(rs)})
        for r in rs:
            v = r.get("verifier") or {}
            lead = r.get("leading") or {}
            hyps = r.get("hypotheses") or []
            run_rows.append({
                "run_id": r["run_id"], "batch": r["batch"], "batch_order": i, "created": r.get("created"), "phase": str(r.get("phase") or ""),
                "pass": str(r.get("pass") or ""), "investigator": (r.get("profiles") or {}).get("investigator") or "", "set": r.get("set") or "",
                "checkpoint": str(r.get("checkpoint") or ""), "trigger": r.get("trigger") or "", "status": r.get("status") or "",
                "lead_subject": lead.get("subject") or "", "lead_confidence": lead.get("confidence"),
                "trigger_on_board": int(any(h.get("role") == "trigger" and h.get("status") != "rejected" for h in hyps)),
                "hypotheses": len(hyps), "rejected": len(r.get("rejected") or []),
                "tool_calls": r.get("tool_calls"), "model_turns": r.get("model_turns"), "board_only_turns": r.get("board_only_turns"),
                "cost_usd": r.get("cost_usd"), "wall_s": r.get("wall_s"), "cache_share": r.get("cache_share"),
                "claims": v.get("claims"), "partial": v.get("partial"), "unsupported": v.get("unsupported"),
                "policy_refusals": r.get("policy_refusals"), "board_refusals": r.get("board_refusals"),
                "nudges": _nudges(r), **_score_fields(r)})
    folder.mkdir(parents=True, exist_ok=True)
    paths = [folder / "runs.json", folder / "batches.json"]
    paths[0].write_text(json.dumps(run_rows, ensure_ascii=False, indent=0), encoding="utf-8")
    paths[1].write_text(json.dumps(batch_rows, ensure_ascii=False, indent=0), encoding="utf-8")
    if NOTES.exists():          # the readings of each chart, written by a person and dated
        raw = yaml.safe_load(NOTES.read_text(encoding="utf-8")) or {}
        notes = [{"chart": n["chart"], "reading": " ".join(str(n["reading"]).split()), "as_of": str(raw.get("as_of", ""))}
                 for n in raw.get("notes") or []]
        paths.append(folder / "notes.json")
        paths[2].write_text(json.dumps(notes, ensure_ascii=False, indent=0), encoding="utf-8")
    return paths


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


def _scores(rs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r["scores"] for r in rs if (r.get("scores") or {}).get("scorer") and "error" not in r["scores"]]


def _scored(rs: list[dict[str, Any]], test: Callable[[dict[str, Any]], Any]) -> str:
    """A count over the scored runs of a batch (eval/scorer.py), or a dash when none is scored."""
    ss = _scores(rs)
    return f"{sum(1 for s in ss if test(s))} of {len(ss)}" if ss else "-"


def _scored_spread(rs: list[dict[str, Any]], key: str) -> str:
    """Found of asked, summed over the batch's scored runs, since the two checkpoints ask for different numbers."""
    ss = _scores(rs)
    return f"{sum(s[key][0] for s in ss)} of {sum(s[key][1] for s in ss)}" if ss else "-"


def _scored_sum(rs: list[dict[str, Any]], key: str) -> str:
    ss = _scores(rs)
    return f"{sum(s[key][0] for s in ss)} of {sum(s[key][1] for s in ss)}" if ss else "-"


def _judged_claims(rs: list[dict[str, Any]]) -> str:
    js = [s["judge"] for s in _scores(rs) if s.get("judge")]
    return ", ".join(str(sum(j["claims"][k] for j in js)) for k in ("supported", "partial", "unsupported")) if js else "-"


def _score_cell(r: dict[str, Any]) -> str:
    s = r.get("scores") or {}
    if not s.get("scorer"):
        return "-"
    if "error" in s:
        return "no report"
    head = "HARD FAIL" if s.get("hard_fail") else f"cause {s['cause']:g}"
    return f"{head}, traps {s['traps'][0]}/{s['traps'][1]}, facts {s['facts'][0]}/{s['facts'][1]}{', bar' if s.get('bar') else ''}"


MEASURES: list[tuple[str, Callable[[list[dict[str, Any]]], str]]] = [
    ("Runs", lambda rs: str(len(rs))),
    ("Completed", lambda rs: f"{sum(1 for r in rs if r.get('status') == 'completed')} of {len(rs)}"),
    ("Leading hypothesis", _leads),
    ("Tool calls", lambda rs: _spread([r.get("tool_calls") for r in rs])),
    ("Model turns", lambda rs: _spread([r.get("model_turns") for r in rs])),
    ("Board-only turns", lambda rs: _spread([r.get("board_only_turns") for r in rs])),
    ("Cost per run, USD", lambda rs: _spread([r.get("cost_usd") for r in rs], lambda v: f"{v:.2f}")),
    ("Wall time, s", lambda rs: _spread([r.get("wall_s") for r in rs], lambda v: f"{v:.0f}")),
    ("Cache share", lambda rs: _spread([r.get("cache_share") for r in rs], lambda v: f"{v:.2f}")),
    ("Trigger on the board", lambda rs: f"{sum(1 for r in rs if any(h.get('role') == 'trigger' and h.get('status') != 'rejected' for h in r.get('hypotheses') or []))} of {len(rs)}"),
    ("Partial claims", _partial),
    ("Unsupported claims per run", lambda rs: _spread([(r.get("verifier") or {}).get("unsupported") for r in rs])),
    ("Policy refusals per run", lambda rs: _spread([r.get("policy_refusals") for r in rs])),
    ("Board refusals per run", lambda rs: _spread([r.get("board_refusals") for r in rs])),
    ("Quiet-board nudges per run", lambda rs: _spread([_nudges(r) for r in rs])),
    ("Malformed calls retried per run", lambda rs: _spread([r.get("malformed_retries") for r in rs])),
    ("Scored: reached the cause with non-chat support", lambda rs: _scored(rs, lambda s: s.get("cause_non_chat"))),
    ("Scored: hard fails", lambda rs: _scored(rs, lambda s: s.get("hard_fail"))),
    ("Scored: required facts found", lambda rs: _scored_spread(rs, "facts")),
    ("Scored: traps passed", lambda rs: _scored_spread(rs, "traps")),
    ("Scored: alert streams placed rightly", lambda rs: _scored_sum(rs, "alerts")),
    ("Scored: agent bar met", lambda rs: _scored(rs, lambda s: s.get("bar"))),
    ("Judged: claims supported, partial, unsupported", _judged_claims),
    ("Judged: mechanism items stated per run", lambda rs: _spread([((r.get("scores") or {}).get("judge") or {}).get("mechanism", [None])[0] for r in rs])),
]

COLUMNS: list[tuple[str, Callable[[dict[str, Any]], str]]] = [
    ("Run", lambda r: f"`{r['run_id']}`"),
    ("At", lambda r: str(r.get("checkpoint"))),
    ("Trigger", lambda r: str(r.get("trigger"))),
    ("Investigator", lambda r: str((r.get("profiles") or {}).get("investigator"))),
    ("Status", lambda r: str(r.get("status"))),
    ("Leading", _lead),
    ("Score", _score_cell),
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


# The words the log and the published page use, grouped. The page's key is built from this list.
KEY: list[tuple[str, list[tuple[str, str]]]] = [
    ("The runs", [
        ("Arm A", "The no-tools baseline: one model call over the commander's message, the alerts and the chat, with no tools and no second turn. It shows what a model does with only the obvious inputs."),
        ("Arm C", "The agent, Smalls: it chooses its own reads with eleven tools, keeps a hypothesis board, cites the records it read, and has its report checked before it stands. Every run but arm A's is arm C."),
        ("Checkpoint", "The moment a run investigates as of: 14:40 or 14:52 on the incident's day. The tools show nothing later."),
        ("Batch", "A set of live runs made for one purpose; a column on every chart, in the order they ran."),
        ("Batch names", "The labels along each chart's bottom. p01 to p08 are pass 1 to pass 8's runs, and p07-guard pass 7's wrong-cause triggers. "
                        "p3-sol, p3-luna, p4-sol and p4-luna are phase 2's plan rows P3 (the shakedown) and P4 (the baseline), one batch per model, "
                        "not passes 3 and 4. sol-shake and sol-base are phase 1's shakedown and baseline; eff-low and p02-low probe low effort; "
                        "frame and frame-db-at-time are phase 1's wrong-cause and factual-question triggers, and final two more runs on pass 3's files. "
                        "c3 is the first live run and cli-claude two runs by hand, both on the first provider. armA is arm A; "
                        "e4 batches are the carry-forward and e5 batches the tags ablation."),
        ("Phase", "Phase 1 and phase 2 are the eval loop's two rounds of tuning; build and between are runs made outside them."),
        ("Pass", "One rewrite of the prompt files to fix one finding, by a fresh author who has never seen the incident, kept or reverted on the runs that follow. Pass 0 is the first blind author's files."),
        ("Finding", "A failure mode seen across runs, written in terms that hold on any incident (F-01, F-02 and on), which a pass sets out to fix."),
        ("Kept, reverted", "A pass is kept when its runs still reach the cause, its target moves and nothing else gets worse; otherwise its files are reverted."),
        ("Set", "The models behind the agent's roles: sol, the second provider's middle tier; luna, its cheaper tier; claude, the first provider. The investigator's column names its profile and effort, such as openai@medium."),
        ("Effort", "How long the model reasons before each answer: low, medium or high."),
        ("Trigger kind", "Which message a run was given: standard, the checkpoint's own; framing, a commander asserting a wrong cause, to test whether it steers the agent; or custom, typed by hand."),
        ("Guard set", "The two framing runs a pass must come through, unsteered, before it is kept."),
    ]),
    ("The agent", [
        ("Hypothesis board", "The agent's running list of explanations, each with a role, a status (leading, open, rejected), a confidence and the records for and against it."),
        ("Leading hypothesis", "The explanation the report puts first: what the agent says is causing the incident."),
        ("Roles", "Cause, what is broken and why; trigger, the event that set the cause off; contributing, what made it worse; symptom, an effect, not the origin."),
        ("Handle", "The id at the start of every record a tool prints, such as log:pay-01/ebfd5293. Reports cite handles, and each opens its native record."),
        ("Non-chat support", "Evidence from a system's own record (a log, metric, change or ticket), not only what people said in chat."),
        ("Board-only turn", "A model turn whose only call is a board write, with no reads beside it. Each costs time and tokens."),
        ("Quiet-board nudge", "A line the model sees after three rounds with no board change, asking it to record what it has found or report."),
        ("Verifier", "Two checks on each report before it stands: claim support, where a cheaper pass judges each claim against the rows it cites as supported, partial or unsupported; and a contrarian pass that looks for a better explanation."),
        ("Tags", "Labels a classifier put on records to help searches. A search aid, never evidence."),
        ("Carry-forward", "A 14:52 run that starts from the 14:40 run's board and must use what changed in between."),
    ]),
    ("Scoring", [
        ("Answer key", "What really happened, written with the data. Only the scorer reads it, never the agent."),
        ("Scorer", "Code that grades each run against the answer key, plus a judge: a model given a yes-or-no checklist with quotes, at 14:52 only."),
        ("Reached the cause", "The leading hypothesis blames the real cause, in the cause role, with non-chat support."),
        ("Hard fail", "A wrong leading hypothesis held with high confidence: the worst outcome, since a commander would act on it."),
        ("Required facts", "Facts the answer key says a good report must establish at its checkpoint, each counted only when cited from a record a tool showed."),
        ("Traps", "The misleading theories built into the incident, such as the visible deploy or the full database. A run passes one by rejecting it or placing it inside the real chain."),
        ("Agent bar", "PLAN 13's conditions for a good run: a valid report, no invented citations, the cause with non-chat support, rivals cleared with evidence, the owner from on-call, a list of what not to do, and a stop within budget."),
        ("Mechanism items", "The judge's four-item checklist for how the fault works; the report earns one for each it states with support."),
    ]),
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
           "Each batch is a set of live runs made for one purpose, on one version of the prompt files (its pass). Every run is",
           "scored against the answer key by `eval/scorer.py` (docs/decisions/eval-loop.md section 14); the judge's checklist",
           "covers the second checkpoint only. Counts, never percentages. A spread is the median, then the lowest and highest run.",
           "",
           f"{len(rows)} runs in {len(ordered)} batches; ${sum(r.get('cost_usd') or 0 for r in rows):.2f} in all.",
           "",
           "## Key", ""]
    for group, terms in KEY:
        out += [f"**{group}.**", ""] + [f"- _{term}_: {text}" for term, text in terms] + [""]
    out += ["## What moved", ""]
    out += [f"- **{meta['name']}** (pass {meta.get('pass', '?')}{', ' + str(meta['result']) if meta.get('result') else ''}). {meta['moved']}"
            for meta, _ in ordered if meta.get("moved")]
    out += ["",
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
        if meta.get("moved"):
            out += [f"What moved: {str(meta['moved']).strip()}", ""]
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
    sub.add_parser("rebuild", help="build every row again from its run record, keeping its batch and pass")
    e = sub.add_parser("export", help="write the published page's two JSON files")
    e.add_argument("folder")
    args = p.parse_args(argv)
    if args.command == "rebuild":
        n, missing = rebuild()
        print(f"rebuilt {n - len(missing)} of {n} rows" + (f"; kept as they were, with no record here: {', '.join(missing)}" if missing else ""))
        return 0
    if args.command == "export":
        print("\n".join(str(path) for path in export(Path(args.folder))))
        return 0
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
