"""The incident overview (docs/v4-plan.md 5.2), written once when an incident is imported. Code builds it first,
from served data alone: the two lines from the short description and the latest work note, the configuration
items around the ticket's CI, and the contacts. Then, when a profile is set (the mid-tier one in live mode), one
model call writes the prose fields from the same inputs; its answer is validated and replaces code's prose.
Contacts are always code's, so nothing in them is invented. Not served to the agent.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from smalls.agent.config import SCRIPTED, Config
from smalls.agent.models import as_model_call_error, make_model, reasoning_text
from smalls.agent.record import cost
from smalls.contracts.handles import is_handle

PROMPT = Path(__file__).parent / "prompts" / "overview.md"
TIMEOUT_S = 180
SHORT, LINE, LONG = 120, 300, 600


class Ci(BaseModel):
    name: str = Field(max_length=SHORT)
    label: str = Field("", max_length=SHORT)
    description: str = Field("", max_length=LINE)
    status: str = Field("", max_length=SHORT)
    severity: Literal["bad", "warn", "ok", "unknown"] = "unknown"


class Theory(BaseModel):
    person: str = Field(max_length=SHORT)
    area: str = Field("", max_length=SHORT)
    claim: str = Field(max_length=LINE)
    tried: str = Field("", max_length=LINE)


class Moment(BaseModel):
    time: str = Field(pattern=r"^\d{2}:\d{2}$")
    event: str = Field(max_length=LINE)
    impact_start: bool = False


class Term(BaseModel):
    term: str = Field(max_length=60)
    meaning: str = Field(max_length=LINE)


class Written(BaseModel):
    """What the model writes. Every field is allowed to be empty."""

    whats_going_on: str = Field("", max_length=LONG)
    business_impact: str = Field("", max_length=SHORT)
    affected_cis: list[Ci] = Field(default_factory=list, max_length=12)
    not_affected: list[str] = Field(default_factory=list, max_length=8)
    working_theories: list[Theory] = Field(default_factory=list, max_length=10)
    timeline: list[Moment] = Field(default_factory=list, max_length=20)
    glossary: list[Term] = Field(default_factory=list, max_length=10)


def clean(written: Written) -> Written:
    """No handle and no markdown anywhere in what is shown; a string that carries one is dropped to empty."""
    def ok(text: str) -> bool:
        return not re.search(r"[*_`#]{2,}|^\s*[-*] ", text) and not any(is_handle(w.strip(",;:().\"'")) for w in text.split())

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            return value.strip() if ok(value) else ""
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value

    return Written.model_validate(walk(written.model_dump()))


def first_sentence(text: str) -> str:
    text = " ".join(text.split())
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return (m.group(1) if m else text)[:LINE]


def from_code(inputs: dict[str, Any]) -> Written:
    """The overview without a model: the short description and the latest work note's first sentence, and the
    configuration items from the ticket's business service down to its CI, with no status."""
    ticket = inputs["ticket"]
    f, notes = ticket["fields"], ticket["notes"]
    said = [x for x in (ticket["incident"].get("short_description"), first_sentence(notes[0]["text"]) if notes else None) if x]
    lines = " ".join(s if s.endswith((".", "!", "?")) else f"{s}." for s in said)
    rels, items = inputs["cmdb"]["relationships"], {i["name"]: i for i in inputs["cmdb"]["items"]}
    path: list[str] = []
    service, ci = f.get("business_service"), f.get("cmdb_ci")
    if service and ci:
        found = _path(rels, service, ci)
        path = found or [service, ci]
    elif service or ci:
        path = [service or ci]
    path += [r["child"] for r in rels if r.get("parent") == ci and "Depends on" in str(r.get("type")) and r["child"] not in path]
    cis = [Ci(name=n[:SHORT], label=("CI on the ticket" if n == ci else "Business service" if n == service else str(items.get(n, {}).get("sys_class_name") or ""))[:SHORT],
              description=str(items.get(n, {}).get("short_description") or "")[:LINE]) for n in path]
    return Written(whats_going_on=lines[:LONG], affected_cis=cis)


def _path(rels: list[dict[str, Any]], start: str, end: str) -> list[str]:
    """The shortest chain of relationships from one item to another, following parent to child."""
    frontier, seen = [[start]], {start}
    while frontier:
        nxt = []
        for chain in frontier:
            for r in rels:
                if r.get("parent") == chain[-1] and r.get("child") not in seen:
                    if r["child"] == end:
                        return chain + [end]
                    seen.add(r["child"])
                    nxt.append(chain + [r["child"]])
        frontier = nxt
    return []


def contacts(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Who to talk to, built by code only from the ticket, the on-call schedule, the CMDB and the user records:
    the Major Incident Manager (the ticket's assignee), who is on call for its groups, the CI's support group, and
    the people the work notes name. A phone link only when the user record has a number."""
    f = inputs["ticket"]["fields"]
    people = {p["name"]: p for p in inputs["people"]}
    out: list[dict[str, Any]] = []

    def add(name: str | None, role: str, kind: str = "person") -> None:
        if not name or any(c["name"] == name for c in out):
            return
        p = people.get(name, {})
        out.append({"name": name, "role": role, "kind": kind, "phone": p.get("phone")})

    group = f.get("assignment_group")
    add(f.get("assigned_to"), f"Major Incident Manager{f' · {group}' if group else ''}")
    for o in inputs["oncall"]:
        add(o["name"], f"{o['policy']} · on call, level {o['level']}")
    for p in inputs["people"]:
        add(p["name"], " · ".join(x for x in (p.get("title"), p.get("department")) if x) or "Named in the work notes")
    if inputs.get("support_group"):
        add(inputs["support_group"], f"Support group for {f.get('cmdb_ci')}", kind="group")
    return out


def view(inputs: dict[str, Any], written: Written, by: str) -> dict[str, Any]:
    """The stored overview: what was written, by whom, the contacts and the channels."""
    return {**written.model_dump(), "written_by": by, "contacts": contacts(inputs), "channels": inputs["channels"]}


class Writer:
    """One profile's overview writer. `write` never raises: a failure comes back as an error and no overview."""

    def __init__(self, cfg: Config, profile: str, script: Any = None):
        if profile not in cfg.profiles:
            raise SystemExit(f"SMALLS_API_OVERVIEW_PROFILE names no profile in the agent's config: {profile!r}")
        spec = cfg.profiles[profile]
        self.spec = spec.with_params(script=script) if spec.provider == SCRIPTED else spec
        self.cfg = cfg
        self.prompt = PROMPT.read_text(encoding="utf-8")

    @property
    def profile(self) -> str:
        return self.spec.profile

    def write(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        started = time.monotonic()
        given = {"ticket": {k: v for k, v in inputs["ticket"].items() if k != "zone"}, "changes": inputs["changes"], "cmdb": inputs["cmdb"],
                 "oncall": inputs["oncall"]}
        turn = f"INPUTS:\n```json\n{json.dumps(given, ensure_ascii=False, indent=1)}\n```\n"
        try:
            model, bind = make_model(self.spec, timeout_s=min(self.cfg.timeout_s, TIMEOUT_S))
            message = (model.bind(**bind) if bind else model).invoke([SystemMessage(self.prompt), HumanMessage(turn)])
        except Exception as error:      # described by kind, never with a key value it may quote
            return {"written": None, "error": str(as_model_call_error(error))}
        name = message.response_metadata.get("model_name") or self.spec.model
        usage = dict(message.usage_metadata or {})
        out = {"usage": usage, "model": name, "cost_usd": cost({"usage": usage}, self.cfg.price(name)), "latency_ms": round((time.monotonic() - started) * 1000, 1)}
        text = reasoning_text(message)[1]
        found = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
        if not found:
            return {**out, "written": None, "error": "no overview came back"}
        try:
            written = clean(Written.model_validate(json.loads(found.group(1))))
        except (ValueError, ValidationError) as error:
            return {**out, "written": None, "error": f"the overview did not validate ({type(error).__name__})"}
        if not written.whats_going_on:
            return {**out, "written": None, "error": "the overview said nothing about what is going on"}
        return {**out, "written": written, "error": None}
