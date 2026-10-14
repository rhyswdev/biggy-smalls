"""The report: one object the UI renders, the scorer reads and the next checkpoint starts from
(docs/PLAN.md section 3.1; docs/decisions/tools-agent-build.md section 5, with ruling G3).

Who writes what. The model writes its judgement through `submit_report`: the summary, the impact, the
hypotheses, the timeline, the actions, what not to do, a verdict per alert stream, the open questions and
the stakeholder update. Code writes the rest: `as_of`, each hypothesis's `source` and `history` (read from
the board), `unassessed` alert verdicts, `proposed_actions` (from the gate ledger) and `validation`.

There is no top-level trigger: it is the hypothesis with that role, and it may be absent. A report with
no `leading` hypothesis is inconclusive, which is a valid outcome. `impact` and `stakeholder_update` are
null in a partial report, which code renders from the board when the agent did not submit one.

`report.schema.json` beside this file is generated from `Report` and committed. It carries no description
text, so wording changes to the tool descriptions never change the contract the scorer reads. Regenerate
it with `python -m smalls.agent schema --write`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_PATH = Path(__file__).with_name("report.schema.json")

Role = Literal["cause", "trigger", "contributing", "symptom", "unrelated"]
Status = Literal["leading", "open", "rejected"]
Source = Literal["stated", "derived"]
Verdict = Literal["this_incident", "other_incident", "noise"]
Decision = Literal["approved", "rejected", "deferred"]


class Strict(BaseModel):
    """No unknown keys: a strict tool schema needs every object closed, and a misspelt field should fail."""

    model_config = ConfigDict(extra="forbid")


class Rationale(Strict):
    tests: str      # the hypothesis the call tests
    expect: str     # what the model expects to see


class Priority(Strict):
    suggested: str
    impact: str
    urgency: str


class Impact(Strict):
    business_service: str
    cis: list[str]
    priority: Priority
    assignment_group: str
    oncall: str
    customer: str


class HistoryRow(Strict):
    as_of: str
    status: Status
    confidence: float = Field(ge=0, le=1)
    because: list[str] = Field(default_factory=list)


class Hypothesis(Strict):
    id: str                     # stable; reused across updates and checkpoints
    statement: str
    role: Role
    status: Status
    subject: str                # the one entity it blames
    entities: list[str]         # everything it touches
    changes: list[str]          # handles of the change records it implicates
    confidence: float = Field(ge=0, le=1)
    support: list[str]
    against: list[str]
    source: Source              # code, from the board: stated in the trigger or the chat, or derived
    history: list[HistoryRow]   # code, from the board: one row per checkpoint


class TimelineRow(Strict):
    t_utc: str
    event: str
    evidence: list[str]


class Action(Strict):
    rank: int = Field(ge=1)
    action: str
    owner: str
    evidence: list[str]


class DoNot(Strict):
    action: str
    why: str
    evidence: list[str]


class AlertVerdict(Strict):
    """The model's verdict on one alert stream."""

    handle: str
    verdict: Verdict


class ReportAlert(Strict):
    handle: str
    verdict: Literal["this_incident", "other_incident", "noise", "unassessed"]   # code adds `unassessed`


class StakeholderUpdate(Strict):
    status: str
    customer_impact: str
    scope: str
    changed_since_last: str
    next_steps: str
    next_update_at: str


class GateBy(Strict):
    who: Literal["policy", "auto", "human"]
    via: Literal["cli", "web", "headless"]


class ProposedAction(Strict):
    """One gated action call with its gate decision. Written by code from the ledger, never by the model."""

    call_id: str
    tool: str
    args: dict[str, Any]
    rationale: Rationale | None
    decision: Decision
    by: GateBy
    reason: str | None          # an `ic:note/` handle when a person gave a reason; a policy's own words otherwise
    decided_at: str | None


class Validation(Strict):
    """What the validator did to the report, so a reader sees a degraded report as one."""

    ok: bool
    attempts: int
    dropped: list[str] = Field(default_factory=list)        # handles removed because no tool showed them
    flagged: list[str] = Field(default_factory=list)        # claim ids left with no handle
    continuity: Literal["passed", "failed"] | None = None   # the carry-forward check; None when nothing was carried
    verifier: Literal["pass", "revised", "unverified"] | None = None    # what the verifier did; None when it did not run
    disputed: list[str] = Field(default_factory=list)       # claim ids the verifier left short of supported


class Report(Strict):
    as_of: str
    summary: str
    impact: Impact | None
    hypotheses: list[Hypothesis]
    timeline: list[TimelineRow]
    actions: list[Action]
    do_not: list[DoNot]
    alerts: list[ReportAlert]
    open_questions: list[str]
    stakeholder_update: StakeholderUpdate | None
    proposed_actions: list[ProposedAction]
    validation: Validation | None = None


def strip_keys(node: Any, keys: tuple[str, ...]) -> Any:
    """A copy of a JSON Schema without the annotation keys named. A property that happens to be called
    `title` or `description` is kept: only a schema object's own annotations are dropped."""
    if isinstance(node, list):
        return [strip_keys(item, keys) for item in node]
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if key in keys:
            continue
        if key in ("properties", "$defs"):      # here the keys are names, not annotations
            out[key] = {name: strip_keys(sub, keys) for name, sub in value.items()}
        else:
            out[key] = strip_keys(value, keys)
    return out


def report_schema() -> dict[str, Any]:
    """The JSON Schema of `Report`, with no description text."""
    schema = strip_keys(Report.model_json_schema(), ("description",))
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}


def schema_text() -> str:
    return json.dumps(report_schema(), indent=2) + "\n"
