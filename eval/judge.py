"""The judge (docs/PLAN.md section 5; row S4 of docs/decisions/eval-loop.md): one model call per report, on its
own system prompt (`eval/judge/prompt.md`), with a yes/no checklist that needs quotes and a claim-support verdict
per hypothesis (eval layer 4b).

The checklist is per-bundle data in the answer key; the prompt stays generic. Code checks every quote: a yes
whose quote is not in the report counts as a no, and a claim verdict whose quote is not in the rows it names is
marked so. The profile is pinned in `eval/scorer.toml`, never read from the agent's sets, so the grader does not
move when the agent's models do. Every live call is appended to `eval/judge/spend.jsonl`, and a call that would
take the total past the config's budget is refused before it is made.

Offline (under pytest, or with `script=`), the judge is scripted.
"""

from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from eval.key import Checkpoint

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "eval" / "scorer.toml"
PROMPT = ROOT / "eval" / "judge" / "prompt.md"
SPEND = ROOT / "eval" / "judge" / "spend.jsonl"
GROUPS = ("mechanism", "actions", "harmful_top3")
VERDICTS = ("supported", "partial", "unsupported")
JUDGE_VERSION = "2"        # 2 (2026-10-15): a conditional or requested step is not a recommended harmful step
CUT = " [cut]"
ESTIMATE_USD = 0.12        # what one call is assumed to cost when the budget is checked before it

TOOL = {
    "name": "submit_judgement",
    "description": "Your judgement: every checklist item and every claim, once each.",
    "parameters": {
        "type": "object",
        "properties": {
            "checklist": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "yes": {"type": "boolean"}, "quote": {"type": "string"}},
                "required": ["id", "yes", "quote"], "additionalProperties": False}},
            "claims": {"type": "array", "items": {"type": "object", "properties": {
                "id": {"type": "string"}, "verdict": {"type": "string", "enum": list(VERDICTS)},
                "quote": {"type": "string"}, "handle": {"type": "string"}},
                "required": ["id", "verdict", "quote", "handle"], "additionalProperties": False}},
        },
        "required": ["checklist", "claims"], "additionalProperties": False,
    },
}

Script = Callable[[str], dict[str, Any]]


def config(path: Path = CONFIG) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


# -- the packet -------------------------------------------------------------------------------------------


def report_text(report: dict[str, Any]) -> str:
    """The parts of the report the checklist reads, as plain text. Quotes are checked against this."""
    lines = ["SUMMARY", report.get("summary") or "", "", "HYPOTHESES"]
    for h in report.get("hypotheses") or []:
        lines.append(f"- {h['id']} [{h['role']}, {h['status']}, {h.get('confidence')}] {h['statement']}")
    lines += ["", "ACTIONS"]
    for a in sorted(report.get("actions") or [], key=lambda a: a.get("rank") or 99):
        lines.append(f"{a.get('rank')}. {a.get('action')} (owner: {a.get('owner')})")
    lines += ["", "DO NOT"]
    lines += [f"- {d.get('action')}: {d.get('why')}" for d in report.get("do_not") or []]
    su = report.get("stakeholder_update") or {}
    if su:
        lines += ["", "STAKEHOLDER UPDATE"] + [f"{k}: {v}" for k, v in su.items()]
    return "\n".join(lines)


def claims(report: dict[str, Any], evidence: dict[str, str], *, limit: int, chars: int) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The hypotheses to judge, leading first, then the other standing ones, then the rejected, each with the
    handles it cites (support, or against for a rejected one); and the text of those rows, each once. A hypothesis
    whose rows the run never showed has nothing to be judged against and is left out."""
    order = {"leading": 0, "open": 1, "rejected": 2}
    picked, rows = [], {}
    for h in sorted(report.get("hypotheses") or [], key=lambda h: (order.get(h["status"], 3), -(h.get("confidence") or 0))):
        cited = [x for x in (h.get("against") if h["status"] == "rejected" else h.get("support")) or [] if x in evidence]
        if cited and len(picked) < limit:
            picked.append({"id": h["id"], "status": h["status"], "statement": h["statement"], "handles": cited})
            rows.update({x: _cut(evidence[x], chars) for x in cited})
    return picked, rows


def _cut(text: str, chars: int) -> str:
    """A long row keeps its first two thirds and its last third, so a stack trace keeps its deepest frames."""
    if len(text) <= chars:
        return text
    head = chars * 2 // 3
    return text[:head] + CUT + "\n" + text[-(chars - head):]


def user_turn(report: dict[str, Any], cp: Checkpoint, packet: list[dict[str, Any]], rows: dict[str, str]) -> str:
    assert cp.judge is not None
    parts = ["THE REPORT", "", report_text(report), "", "THE CHECKLIST"]
    for group in GROUPS:
        parts.append(f"\n{group}:")
        parts += [f"- {item}: {text}" for item, text in getattr(cp.judge, group).items()]
    parts += ["", "THE ROWS", ""]
    parts += [f"[{h}] {t}\n" for h, t in rows.items()]
    parts += ["THE CLAIMS"]
    for c in packet:
        parts.append(f"\nclaim {c['id']} ({c['status']}): {c['statement']}")
        parts.append(("rows cited as against: " if c["status"] == "rejected" else "rows cited as support: ") + " ".join(c["handles"]))
    return "\n".join(parts)


def evidence_texts(folder: Path) -> dict[str, str]:
    """Each handle's text as the run saw it, the longest showing when it was shown more than once (a search
    snippet first, the whole record later)."""
    out: dict[str, str] = {}
    try:
        lines = (folder / "evidence.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        if line.strip():
            row = json.loads(line)
            if len(row.get("text") or "") > len(out.get(row["handle"], "")):
                out[row["handle"]] = row["text"]
    return out


# -- checking the answer ------------------------------------------------------------------------------------


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def check(answer: dict[str, Any], report: dict[str, Any], cp: Checkpoint, packet: list[dict[str, Any]], rows: dict[str, str]) -> dict[str, Any]:
    """The judgement as counted: each checklist item yes only with a quote found in the report; each claim's
    verdict with whether its quote was found in the rows it names."""
    assert cp.judge is not None
    text = _norm(report_text(report))
    given = {str(c.get("id")): c for c in answer.get("checklist") or []}
    out: dict[str, Any] = {}
    for group in GROUPS:
        items = {}
        for item in getattr(cp.judge, group):
            c = given.get(item) or {}
            quote = str(c.get("quote") or "")
            quoted = bool(quote.strip()) and _norm(quote.replace(CUT, "")) in text
            items[item] = {"yes": bool(c.get("yes")) and quoted, "said_yes": bool(c.get("yes")), "quote_found": quoted,
                           "answered": item in given}
        out[group] = {"yes": sum(i["yes"] for i in items.values()), "of": len(items), "items": items}
    by_id = {c["id"]: c for c in packet}
    verdicts = {}
    for c in answer.get("claims") or []:
        cid = str(c.get("id"))
        if cid not in by_id or cid in verdicts:
            continue
        v = c.get("verdict") if c.get("verdict") in VERDICTS else "unsupported"
        quote, handle = _norm(str(c.get("quote") or "").replace(CUT, "")), str(c.get("handle") or "")
        found = bool(quote) and handle in by_id[cid]["handles"] and quote in _norm(rows.get(handle, ""))
        verdicts[cid] = {"verdict": v, "quote_found": found if v != "unsupported" else None}
    for cid in by_id:
        verdicts.setdefault(cid, {"verdict": "unjudged", "quote_found": None})
    counts = {k: sum(1 for v in verdicts.values() if v["verdict"] == k) for k in (*VERDICTS, "unjudged")}
    out["claims"] = {**counts, "of": len(by_id), "quote_missing": sum(1 for v in verdicts.values() if v["quote_found"] is False),
                     "by_id": verdicts}
    return out


# -- the call -----------------------------------------------------------------------------------------------


def spent(path: Path = SPEND) -> float:
    try:
        return sum(float(json.loads(x).get("cost_usd") or 0) for x in path.read_text(encoding="utf-8").splitlines() if x.strip())
    except OSError:
        return 0.0


def _model(cfg: dict[str, Any]):
    from smalls.agent.config import ModelSpec
    from smalls.agent.models import make_model

    raw = dict(cfg["judge"])
    timeout = float(raw.pop("timeout_s", 600))
    spec = ModelSpec(profile="judge", provider=raw.pop("provider"), model=raw.pop("model"), params=raw)
    return make_model(spec, timeout_s=timeout)[0], spec


def _parse(message: Any) -> dict[str, Any]:
    for call in getattr(message, "tool_calls", None) or []:
        if call.get("name") == TOOL["name"]:
            return dict(call.get("args") or {})
    content = message.content if isinstance(message.content, str) else "".join(
        b.get("text", "") for b in message.content if isinstance(b, dict) and b.get("type") == "text")
    m = re.search(r"\{.*\}", content, re.S)
    try:
        return json.loads(m[0]) if m else {}
    except ValueError:
        return {}


def judge(report: dict[str, Any], cp: Checkpoint, evidence: dict[str, str], *, label: str, script: Script | None = None,
          cfg: dict[str, Any] | None = None, spend: Path = SPEND) -> dict[str, Any]:
    """Judge one report. `script` answers in place of the model (offline); otherwise one live call."""
    cfg = cfg or config()
    if cp.judge is None:
        return {"judge": JUDGE_VERSION, "skipped": "the key has no checklist at this checkpoint"}
    packet, rows = claims(report, evidence, limit=int(cfg.get("max_claims", 12)), chars=int(cfg.get("claim_chars", 2400)))
    turn = user_turn(report, cp, packet, rows)
    if script is not None:
        answer, meta = script(turn), {"model": "scripted", "cost_usd": 0.0, "usage": {}}
    else:
        budget = float(cfg.get("budget_usd", 5.0))
        if spent(spend) + ESTIMATE_USD > budget:
            raise SystemExit(f"judge: ${spent(spend):.2f} of the ${budget:.2f} budget is spent; ask before spending more")
        answer, meta = _live(turn, cfg)
        with spend.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "label": label,
                                "cost_usd": round(meta["cost_usd"], 6), "usage": meta["usage"]}) + "\n")
    return {"judge": JUDGE_VERSION, "model": meta["model"], "cost_usd": meta["cost_usd"], **check(answer, report, cp, packet, rows)}


def _live(turn: str, cfg: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    from langchain_core.messages import HumanMessage, SystemMessage

    from smalls.agent.models import as_model_call_error, provider_tools
    from smalls.agent.record import cost

    model, spec = _model(cfg)
    tools = provider_tools([TOOL], spec.provider, strict=True)
    bound = model.bind_tools(tools, tool_choice=TOOL["name"])
    try:
        message = bound.invoke([SystemMessage(PROMPT.read_text(encoding="utf-8")), HumanMessage(turn)])
    except Exception as error:      # recorded without any key value it may quote
        raise SystemExit(f"judge: the model call failed: {as_model_call_error(error)}") from None
    name = message.response_metadata.get("model_name") or spec.model
    price = (cfg.get("pricing") or {}).get(spec.model) or {}
    usage = dict(message.usage_metadata or {})
    return _parse(message), {"model": name, "cost_usd": cost({"usage": usage}, price), "usage": usage}
