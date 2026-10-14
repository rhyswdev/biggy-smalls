"""The scorer's deterministic part (docs/PLAN.md section 5; rows S2 and S3 of docs/decisions/eval-loop.md,
whose section 14 holds the rulings it follows).

Inputs: a run record (the report, the calls with their `shown[]` and `referenced[]`, the notes, the events),
the bundle's manifest and answer key, and the alias table in `scenario/facts.yaml`. No model is called here;
the judge is `eval/judge.py`. Every check reads the report as the model and code left it, after the validator.

    score_run(<run folder>) -> dict        # what `scores.json` holds, without the judge's part

The checks, in PLAN's order:
1. citation integrity: fabricated (in neither the manifest nor this run's calls or notes), unseen (never in a
   `shown[]` of this run), future (no version known before `as_of`). Fabricated and future must be zero.
2. cause and trigger, each 0, 0.5 or 1, matched on `subject` through the alias table (G3);
3. fact recall at `as_of`: a fact counts when a cited handle that is not chat resolves to one of its refs;
4. the trap table, read with the role as the claim (11.2, ruling 4): a rejected hypothesis never falls for a trap,
   a leading one always does, an open one passes when its role or status is allowed;
5. the owner;
6. confidence: a wrong leading hypothesis above the key's maximum is a hard fail, a right one below its
   minimum a soft fail;
and the alert verdicts, the contributing factor, non-chat support per causal link, PLAN 13's agent bar, and
the process measures to the first correct leading hypothesis (S3).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from eval.key import Aliases, AnswerKey, Checkpoint, Manifest, _norm, load_aliases, load_key, load_manifest
from smalls.contracts.handles import handle_class, is_handle, parse_metric_handle

SCORER_VERSION = "1"
WRAP = "`'\"()[]<>*.,;!?"
LINKS = ("trigger", "contributing")


# -- reading a run ---------------------------------------------------------------------------------------


def _json(folder: Path, name: str, default: Any = None) -> Any:
    try:
        return json.loads((folder / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _lines(folder: Path, name: str) -> list[dict[str, Any]]:
    try:
        return [json.loads(x) for x in (folder / name).read_text(encoding="utf-8").splitlines() if x.strip()]
    except OSError:
        return []


def inline(*texts: Any) -> set[str]:
    """Handles written into prose: each word, with wrapping punctuation taken off, or each comma-separated part."""
    found: set[str] = set()
    for text in texts:
        if not isinstance(text, str):
            continue
        for token in text.split():
            whole = token.strip(WRAP).rstrip(":")
            if ":" not in whole:
                continue
            parts = [whole] if is_handle(whole) else [p.strip(WRAP).rstrip(":") for p in whole.split(",")]
            found |= {p for p in parts if is_handle(p)}
    return found


def cited(report: dict[str, Any]) -> set[str]:
    """Every handle the report cites, listed or written into its prose."""
    out: set[str] = set()
    for h in report.get("hypotheses") or []:
        out |= set(h.get("support") or []) | set(h.get("against") or []) | set(h.get("changes") or [])
        out |= {b for row in h.get("history") or [] for b in row.get("because") or [] if is_handle(b)}
        out |= inline(h.get("statement"))
    for row in report.get("timeline") or []:
        out |= set(row.get("evidence") or []) | inline(row.get("event"))
    for a in report.get("actions") or []:
        out |= set(a.get("evidence") or []) | inline(a.get("action"))
    for d in report.get("do_not") or []:
        out |= set(d.get("evidence") or []) | inline(d.get("action"), d.get("why"))
    out |= {a["handle"] for a in report.get("alerts") or []}
    out |= {p["reason"] for p in report.get("proposed_actions") or [] if p.get("reason") and is_handle(p["reason"])}
    out |= inline(report.get("summary"), *(report.get("open_questions") or []), *((report.get("stakeholder_update") or {}).values()),
                  *((report.get("impact") or {}).values()))
    return out


class RunLog:
    """What the run's tools showed and pointed to, and the notes it minted."""

    def __init__(self, folder: Path):
        calls = _lines(folder, "calls.jsonl")
        self.shown = {h for c in calls for h in c.get("shown") or []}
        self.referenced = {h for c in calls for h in c.get("referenced") or []}
        self.notes = {n.get("handle") for n in _lines(folder, "notes.jsonl") if n.get("handle")}
        self.alert_streams = [h for c in calls if c.get("tool") == "list_alerts" for h in c.get("shown") or []]

    def minted(self, handle: str) -> bool:
        return handle in self.shown or handle in self.referenced or handle in self.notes

    def seen(self, handle: str) -> bool:
        return handle in self.shown or handle in self.notes


# -- the checks -------------------------------------------------------------------------------------------


def integrity(handles: Iterable[str], log: RunLog, manifest: Manifest, as_of: str) -> dict[str, Any]:
    fabricated, unseen, future = [], [], []
    for h in sorted(handles):
        if not (manifest.known(h) or log.minted(h)):
            fabricated.append(h)
            continue
        if not log.seen(h):
            unseen.append(h)
        if h.startswith("metric:"):
            _, _, w1 = parse_metric_handle(h)
            if w1 and w1 > as_of[11:13] + as_of[14:16]:
                future.append(h)
        elif manifest.known(h) and _norm(manifest.first_known(h) or "") >= _norm(as_of):
            future.append(h)
    return {"cited": len(set(handles)), "fabricated": fabricated, "unseen": unseen, "future": future,
            "ok": not fabricated and not future}


def _matches(h: dict[str, Any], entity: str, aliases: Aliases) -> bool:
    return entity in aliases.entities_in(h.get("subject") or "")


def leading_of(report: dict[str, Any]) -> dict[str, Any] | None:
    """The report's leading hypothesis; with several, the most confident, then the first."""
    leads = [h for h in report.get("hypotheses") or [] if h.get("status") == "leading"]
    return max(leads, key=lambda h: h.get("confidence") or 0) if leads else None


def _names_change(h: dict[str, Any], change_any: list[str]) -> bool:
    """Whether a hypothesis names the change: through its change or support handles, or by id in its statement."""
    handles = set(h.get("changes") or []) | set(h.get("support") or [])
    text = h.get("statement") or ""
    for c in change_any:
        if f"snow:{c}" in handles or f"jk:{c}" in handles or c in text:
            return True
    return False


def cause(report: dict[str, Any], cp: Checkpoint, aliases: Aliases) -> dict[str, Any]:
    """1: the leading hypothesis blames the key's cause, claims the role `cause`, and the change is named, when the
    key gives one, by it or by another standing hypothesis on the same subject. 0.5: it blames the right subject but misses the role or the change, or the right subject is a
    `cause` still open. 0: neither."""
    lead = leading_of(report)
    entities = cp.cause_entities
    lead_right = bool(lead) and any(_matches(lead, e, aliases) for e in entities)
    change = cp.leading.change_any if cp.leading else []
    # The change may sit in its own standing hypothesis on the same subject: one link per statement.
    same = [h for h in report.get("hypotheses") or [] if h["status"] != "rejected" and lead_right
            and any(_matches(h, e, aliases) for e in entities)]
    named = bool(lead) and lead_right and (not change or any(_names_change(h, change) for h in same))
    if lead_right and lead["role"] == "cause" and named:
        score = 1.0
    elif lead_right:
        score = 0.5
    elif any(h["status"] == "open" and h["role"] == "cause" and any(_matches(h, e, aliases) for e in entities)
             for h in report.get("hypotheses") or []):
        score = 0.5
    else:
        score = 0.0
    return {"score": score, "leading": bool(lead), "leading_right": lead_right, "change_named": named if change else None,
            "leading_role": lead["role"] if lead else None, "confidence": lead.get("confidence") if lead else None}


def trigger(report: dict[str, Any], cp: Checkpoint, aliases: Aliases) -> dict[str, Any] | None:
    """1: a hypothesis that stands claims the key's trigger with the role `trigger`. 0.5: the trigger is the
    subject of a standing hypothesis with another role, or the leading hypothesis names it. None: the key has none."""
    if cp.trigger is None:
        return None
    e = cp.trigger.entity
    standing = [h for h in report.get("hypotheses") or [] if h["status"] != "rejected" and _matches(h, e, aliases)]
    lead = leading_of(report)
    if any(h["role"] == "trigger" for h in standing):
        score = 1.0
    elif standing or (lead and (e in {x for s in lead.get("entities") or [] for x in aliases.entities_in(s)}
                                or aliases.named(lead.get("statement") or "", e))):
        score = 0.5
    else:
        score = 0.0
    return {"score": score}


def non_chat(handles: Iterable[str], integ: dict[str, Any]) -> list[str]:
    bad = set(integ["fabricated"]) | set(integ["future"])
    return [h for h in handles if handle_class(h) != "chat" and h not in bad]


def facts(report_handles: set[str], key: AnswerKey, cp: Checkpoint, manifest: Manifest, as_of: str, integ: dict[str, Any]) -> dict[str, Any]:
    refs: set[str] = set()
    for h in non_chat(report_handles, integ):
        refs |= manifest.sigs_and_classes(h, as_of)

    def found(ids: list[str]) -> list[str]:
        return [f for f in ids if refs & set(key.facts[f].any_of)]

    req, sup = found(cp.required_facts), found(cp.supporting_facts)
    return {"required": len(req), "required_of": len(cp.required_facts), "missing": [f for f in cp.required_facts if f not in req],
            "supporting": len(sup), "supporting_of": len(cp.supporting_facts)}


def traps(report: dict[str, Any], cp: Checkpoint, aliases: Aliases) -> dict[str, Any]:
    out: dict[str, dict[str, Any]] = {}
    lead = leading_of(report)
    for name, trap in cp.traps.items():
        hits = [h for h in report.get("hypotheses") or [] if _matches(h, trap.match.entity, aliases)]
        if not hits:
            placed = bool(lead) and trap.match.entity in {x for s in lead.get("entities") or [] for x in aliases.entities_in(s)}
            out[name] = {"pass": trap.absent_ok or placed, "why": "in_leading" if placed else "absent"}
            continue
        fell = [h["id"] for h in hits if h["status"] == "leading" or
                (h["status"] == "open" and h["role"] not in trap.allowed.role and "open" not in trap.allowed.status)]
        out[name] = {"pass": not fell, "why": "fell" if fell else "held", "by": fell}
    return {"passed": sum(t["pass"] for t in out.values()), "of": len(out), "traps": out}


def owner(report: dict[str, Any], cp: Checkpoint) -> dict[str, Any] | None:
    """Pass when the on-call owner is named in the impact's on-call or group or as the first action's owner, and
    no name the key rules out is given as a group or an action's owner. `impact.oncall` is prose that may say who is
    not on call, so a ruled-out name there does not fail."""
    if cp.owner is None:
        return None
    impact = report.get("impact") or {}
    actions = sorted(report.get("actions") or [], key=lambda a: a.get("rank") or 99)
    named_in = [impact.get("oncall") or "", impact.get("assignment_group") or "", actions[0]["owner"] if actions else ""]
    owners = [impact.get("assignment_group") or "", *(a.get("owner") or "" for a in actions)]
    right = any(n.lower() in t.lower() for n in cp.owner.any for t in named_in)
    wrong = any(n.lower() in t.lower() for n in cp.owner.never for t in owners)
    return {"pass": right and not wrong, "named": right, "ruled_out_named": wrong}


def confidence(c: dict[str, Any], cp: Checkpoint) -> dict[str, Any]:
    conf = c["confidence"]
    hard = c["leading"] and not c["leading_right"] and conf is not None and conf > cp.confidence.wrong_leading_max
    soft = (c["leading_right"] and cp.confidence.right_leading_min is not None and conf is not None
            and conf < cp.confidence.right_leading_min)
    return {"hard_fail": bool(hard), "soft_fail": bool(soft)}


def contributing(report: dict[str, Any], cp: Checkpoint, aliases: Aliases) -> dict[str, Any] | None:
    if not cp.contributing_any:
        return None
    got = [e for e in cp.contributing_any if any(h["status"] != "rejected" and h["role"] == "contributing" and _matches(h, e, aliases)
                                                  for h in report.get("hypotheses") or [])]
    return {"found": len(got), "of": len(cp.contributing_any)}


def causal_links(report: dict[str, Any], integ: dict[str, Any]) -> dict[str, Any]:
    """Each link of the chain the report stands on (the leading hypothesis, and each open `trigger` or
    `contributing` one) and whether it cites support that is not chat. An open `cause` is a rival, not a link."""
    links = [h for h in report.get("hypotheses") or [] if h["status"] == "leading" or (h["status"] == "open" and h["role"] in LINKS)]
    bare = [h["id"] for h in links if not non_chat(h.get("support") or [], integ)]
    return {"links": len(links), "with_non_chat": len(links) - len(bare), "without": bare}


def alerts(report: dict[str, Any], key: AnswerKey, manifest: Manifest, log: RunLog) -> dict[str, Any]:
    given = [a for a in report.get("alerts") or [] if a.get("verdict") != "unassessed"]
    side_right = exact_right = 0
    wrong: list[dict[str, str]] = []
    for a in given:
        rows = manifest.get(a["handle"])
        sigs = {r["sig_id"] for r in rows if r.get("sig_id")}
        classes = {r["class"] for r in rows if r.get("class")}
        entry = next((s for s in key.alerts.streams if s.sig and s.sig in sigs), None) or \
            next((s for s in key.alerts.streams if s.class_ and s.class_ in classes), None)
        accept = entry.accept if entry else key.alerts.default
        exact = a["verdict"] in accept
        side = (a["verdict"] == "this_incident") == ("this_incident" in accept)
        exact_right += exact
        side_right += side
        if not side:
            wrong.append({"handle": a["handle"], "verdict": a["verdict"], "expected": accept[0]})
    named = {a["handle"] for a in report.get("alerts") or []}
    owed = list(dict.fromkeys(log.alert_streams))
    return {"given": len(given), "side_right": side_right, "exact_right": exact_right, "wrong": wrong,
            "unassessed": sum(1 for a in report.get("alerts") or [] if a.get("verdict") == "unassessed"),
            "owed": len(owed), "missing": [h for h in owed if h not in named]}


def bar(report: dict[str, Any], run: dict[str, Any], summary: dict[str, Any], c: dict[str, Any], integ: dict[str, Any],
        links: dict[str, Any], own: dict[str, Any] | None) -> dict[str, Any]:
    """PLAN 13's agent bar, criterion by criterion, as this run meets it."""
    validation = report.get("validation") or {}
    process = (summary.get("process") or {}).get("budget") or {}
    lead = leading_of(report)
    rejected = [h for h in report.get("hypotheses") or [] if h["status"] == "rejected"]
    criteria = {
        "valid_report": bool(validation.get("ok")) and summary.get("status") in ("completed", None),
        "no_fabricated_or_future": integ["ok"],
        "cause_with_non_chat": c["score"] == 1.0 and bool(lead) and bool(non_chat(lead.get("support") or [], integ)),
        "non_chat_per_link": links["links"] > 0 and not links["without"],
        "rejected_have_against": all(h.get("against") for h in rejected),
        "owner_from_oncall": None if own is None else own["pass"],
        "lists_do_not": bool(report.get("do_not")),
        "stops_within_budget": not process.get("turn_cap_hit") and summary.get("stop_reason") in (None, ""),
        "continuity": validation.get("continuity") != "failed",
    }
    return {**criteria, "met": all(v for v in criteria.values() if v is not None)}     # None: the key asks nothing here


# -- process to the first correct leading hypothesis (S3) ---------------------------------------------------


def first_correct(events: list[dict[str, Any]], cp: Checkpoint, aliases: Aliases) -> dict[str, Any] | None:
    """Model turns, tool calls and tokens until a hypothesis on the key's cause first leads with the role `cause`,
    read from the run's `hypothesis_update` events. None when that never happens."""
    turns = calls = tokens = 0
    for e in events:
        kind, data = e.get("type"), e.get("data") or {}
        if kind == "model_call" and data.get("node") in ("agent", "fallback"):
            turns += 1
            tokens += int((data.get("usage") or {}).get("total_tokens") or 0)
        elif kind == "tool_result" and data.get("kind") in ("read", "action"):
            calls += 1
        elif kind == "hypothesis_update" and data.get("status") == "leading" and data.get("role") == "cause" \
                and any(_matches(data, x, aliases) for x in cp.cause_entities):
            return {"model_turns": turns, "tool_calls": calls, "tokens": tokens}
    return None


# -- one run ----------------------------------------------------------------------------------------------


def score_report(report: dict[str, Any], *, checkpoint: str, as_of: str, log: RunLog, bundle: str, run: dict[str, Any] | None = None,
                 summary: dict[str, Any] | None = None, events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    key, manifest, aliases = load_key(bundle), load_manifest(bundle), load_aliases()
    cp = key.checkpoint(checkpoint)
    if cp is None:
        return {"scorer": SCORER_VERSION, "checkpoint": checkpoint, "error": f"the answer key has no checkpoint {checkpoint}"}
    handles = cited(report)
    integ = integrity(handles, log, manifest, as_of)
    c = cause(report, cp, aliases)
    links = causal_links(report, integ)
    own = owner(report, cp)
    out = {
        "scorer": SCORER_VERSION, "checkpoint": checkpoint, "as_of": as_of,
        "integrity": integ, "cause": c, "trigger": trigger(report, cp, aliases),
        "facts": facts(handles, key, cp, manifest, as_of, integ), "traps": traps(report, cp, aliases),
        "owner": own, "confidence": confidence(c, cp), "contributing": contributing(report, cp, aliases),
        "causal_links": links, "alerts": alerts(report, key, manifest, log),
        "bar": bar(report, run or {}, summary or {}, c, integ, links, own),
        "first_correct": first_correct(events or [], cp, aliases),
    }
    return out


def score_run(folder: Path | str) -> dict[str, Any]:
    """The deterministic scores of one run record. A run with no report scores as one with nothing in it."""
    folder = Path(folder)
    run = _json(folder, "run.json", {})
    summary = _json(folder, "summary.json", {})
    report = _json(folder, "report.json") or {}
    checkpoint = str(run.get("checkpoint") or "")
    as_of = report.get("as_of") or run.get("now") or ""
    out = score_report(report, checkpoint=checkpoint, as_of=as_of, log=RunLog(folder), bundle=run.get("bundle") or "larkspur-checkout-2026-10-13",
                       run=run, summary=summary, events=_lines(folder, "events.jsonl"))
    return {"run_id": run.get("run_id") or folder.name, "arm": run.get("arm"), "has_report": bool(report), **out}
