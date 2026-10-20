"""The script the scripted profile plays when the API runs in scripted mode (docs/decisions/ui-build.md section
6.2): for developing and testing the UI without a key. It knows nothing about any data. It opens with what is
alerting, what changed and what people are saying, reads the recent logs and counts them, writes one open
hypothesis citing rows it was shown, proposes one action, and submits an inconclusive report with a verdict for
every alert stream its listing showed. A follow-up is answered in text. It proves the plumbing, never the agent.

`concluded` is the same script reaching a probable cause (docs/v4-plan.md 6.8, `SMALLS_API_SCRIPT=concluded`): its
one hypothesis is leading at 0.75 and its report recommends one generic action, so the answer card, the
probable-cause banner and the resolve flow can be developed and tested without a key. It still knows nothing
about the data.

`outcomes` is the scripted summariser's script (5.1): a generic summary and one generic clause per check, marked
as scripted.

`draft` is the scripted drafter's script (U8): it writes a message from the packet's suggested stakeholder
update and the resolution, the same for every audience but for its name.

Like every script, it chooses each turn from the conversation so far, so it survives a pause and a resume.
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from smalls.agent.models import scripted_call as call
from smalls.contracts.handles import is_handle

UNKNOWN = "unknown"


def shown(messages: list[BaseMessage], needle: str = "") -> list[str]:
    """The handles at the start of result rows, in the order they were shown, optionally only rows holding `needle`."""
    found: list[str] = []
    for m in messages:
        if isinstance(m, (ToolMessage, HumanMessage)):
            for line in str(m.content).splitlines():
                first = line.split("  ", 1)[0].strip()
                if is_handle(first) and needle in line and first not in found:
                    found.append(first)
    return found


def swept_alerts(messages: list[BaseMessage]) -> list[str]:
    """The alert streams the run's `list_alerts` calls showed: the row handles."""
    found: list[str] = []
    for m in messages:
        if isinstance(m, ToolMessage) and m.name == "list_alerts":
            found += [h for h in (line.split("  ", 1)[0].strip() for line in str(m.content).splitlines()) if is_handle(h) and h not in found]
    return found


def first_kind(tools: list[dict[str, Any]]) -> str | None:
    for tool in tools:
        spec = tool.get("function", tool)
        if spec.get("name") == "propose_action":
            kind = (spec.get("parameters") or spec.get("input_schema"))["properties"]["kind"]
            return kind["enum"][0] if "enum" in kind else kind.get("const")
    return None


def turn(messages: list[BaseMessage], tools: list[dict[str, Any]], concluded: bool = False) -> tuple[Any, ...]:
    last_human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
    if last_human is not None and "follow-up from the incident commander" in str(last_human.content):
        return ("A scripted answer.", [], {"text": "This is the scripted development profile: it answers every follow-up the same way. "
                                                   "Run the API in live mode for a real answer."})
    k = sum(isinstance(m, AIMessage) for m in messages)
    if k == 0:      # its own first look: the harness reads nothing before the first turn
        return "Start with what is alerting, what changed and what people are saying.", [
            call("list_alerts", 101, ("h-dev", "what is alerting"), since="-2h"),
            call("list_changes", 102, ("h-dev", "what changed"), since="-24h"),
            call("read_chat", 103, ("h-dev", "what people are saying"), since="-2h")]
    k -= 1
    if k == 0:
        return "Read the most recent log records and count them by level.", [
            call("search_logs", 1, ("h-dev", "recent records"), since="-30m", limit=5),
            call("search_logs", 2, ("h-dev", "a count per level"), since="-30m", group_by="level")]
    rows = [h for h in shown(messages) if not h.startswith("q:")]
    cited = rows[:2] or shown(messages)[:1]
    if k == 1:
        return "Record a first, open hypothesis on what the reads showed.", [call("update_board", 3, hypotheses=[
            {"id": "h-dev", "statement": "Something changed in the last half hour (scripted).", "role": "cause",
             "status": "leading" if concluded else "open", "subject": UNKNOWN, "confidence": 0.75 if concluded else 0.3, "source": "derived",
             "support": cited, "because": cited[:1]}])]
    kind = first_kind(tools)
    proposed = any(isinstance(m, ToolMessage) and m.name == "propose_action" for m in messages)
    if k == 2 and kind and not proposed:
        return "Propose one action for the commander to decide.", [call(
            "propose_action", 4, ("h-dev", "the commander decides"), kind=kind, target=UNKNOWN, params=[], evidence=cited[:1])]
    summary = (f"Something changed in the last half hour (scripted development run). The recent logs were read ({', '.join(cited[:1]) or 'none'})."
               if concluded else f"Inconclusive (scripted development run). The recent logs were read ({', '.join(cited[:1]) or 'none'}).")
    actions = [{"rank": 1, "action": "Undo the most recent change (scripted).", "owner": UNKNOWN, "evidence": cited[:1]}] if concluded else []
    do_not = [{"action": "Do not restart everything at once (scripted).", "why": "It would hide what changed.", "evidence": cited[:1]}] if concluded else []
    return ("Enough to say what changed: submit it." if concluded else "Nothing conclusive: say so."), [call(
        "submit_report", 5 + k, summary=summary,
        impact={"business_service": UNKNOWN, "cis": [], "priority": {"suggested": UNKNOWN, "impact": UNKNOWN, "urgency": UNKNOWN},
                "assignment_group": UNKNOWN, "oncall": UNKNOWN, "customer": UNKNOWN},
        hypotheses=[{"id": "h-dev"}], timeline=[], actions=actions, do_not=do_not,
        alerts=[{"handle": h, "verdict": "noise"} for h in swept_alerts(messages)], open_questions=["What changed? (scripted)"],
        stakeholder_update={"status": "Investigating (scripted).", "customer_impact": UNKNOWN, "scope": UNKNOWN,
                            "changed_since_last": "First update.", "next_steps": "Run the API in live mode.", "next_update_at": UNKNOWN})]


def concluded(messages: list[BaseMessage], tools: list[dict[str, Any]]) -> tuple[Any, ...]:
    return turn(messages, tools, concluded=True)


def outcomes(messages: list[BaseMessage], tools: list[dict[str, Any]]) -> tuple[Any, ...]:
    text = str(next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""))
    found = re.search(r"```json\n(.*?)\n```", text, re.S)
    items = json.loads(found.group(1)) if found else []
    clauses = {c["id"]: f"looked at what {str(c.get('tool', 'the tool')).replace('_', ' ')} returned (scripted)" for c in items}
    summary = f"Smalls ran {len(items)} {'check' if len(items) == 1 else 'checks'} and compared what each returned (scripted)."
    return "A summary and one clause per check.", [], {"text": json.dumps({"summary": summary, "checks": clauses})}


def overview(messages: list[BaseMessage], tools: list[dict[str, Any]]) -> tuple[Any, ...]:
    """A generic overview from the inputs' own fields, marked as scripted: no model and no knowledge of the data."""
    text = str(next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""))
    found = re.search(r"```json\n(.*?)\n```", text, re.S)
    given = json.loads(found.group(1)) if found else {}
    ticket = given.get("ticket") or {}
    notes = ticket.get("notes") or []
    items = (given.get("cmdb") or {}).get("items") or []
    body = {
        "whats_going_on": f"{(ticket.get('incident') or {}).get('short_description') or 'An incident'} (scripted). {len(notes)} work notes so far.",
        "business_impact": "",
        "affected_cis": [{"name": i["name"], "label": i.get("sys_class_name") or "", "description": "", "status": "", "severity": "unknown"} for i in items[:4]],
        "not_affected": [],
        "working_theories": [{"person": n["author"], "area": "", "claim": "Wrote a work note (scripted).", "tried": ""} for n in notes[:2] if n.get("author")],
        "timeline": [{"time": n["utc"][11:16], "event": "Work note added (scripted).", "impact_start": False} for n in reversed(notes)],
        "glossary": [],
    }
    return "Write it from the inputs.", [], {"text": json.dumps(body)}


def draft(messages: list[BaseMessage], tools: list[dict[str, Any]]) -> tuple[Any, ...]:
    turn = str(next((m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""))
    found = re.search(r"```json\n(.*?)\n```", turn, re.S)
    pk = json.loads(found.group(1)) if found else {}
    name = (re.search(r"^Name: (.+)$", turn, re.M) or [None, "everyone"])[1]
    update = pk.get("stakeholder_update") or {}
    said = [update.get("customer_impact"), update.get("status")]
    if pk.get("resolution"):
        said.append(f"Resolved at {pk['resolution']['resolved_at']} UTC.")
    said.append(f"Next update: {update.get('next_update_at') or UNKNOWN}.")
    text = " ".join(str(s).strip() for s in said if s and s != UNKNOWN) + f" (Scripted draft for {name}.)"
    return "Write it from the packet's stakeholder update.", [], {"text": text}
