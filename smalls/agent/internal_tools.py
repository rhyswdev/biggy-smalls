"""The internal tools: `update_board` and `submit_report`. Kind `internal`: they write the agent's own state,
are never gated, and every write is validated (docs/decisions/tools-agent-build.md section 5).

Their behaviour lives in the tools node, which owns state; this module holds what the model fills in.

A hypothesis update carries only the fields the model sets, so an update that changes one field cannot
erase another. A list that is set replaces the list on the board: it is the list as it should now stand.
`source` is set when a hypothesis is created and cannot change (J1).

`submit_report` carries the model-written report fields. Its hypotheses are applied to the board as a last
update, under the same validation and caps, and the report's hypotheses are then read from the board, so
the two cannot disagree. It must be the only call in its turn.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from smalls.agent import actions
from smalls.contracts.envelope import Envelope
from smalls.contracts.report import (Action, AlertVerdict, DoNot, Impact, Role, Source, StakeholderUpdate, Status, Strict,
                                     TimelineRow)
from smalls.tools.registry import ToolContext, ToolSpec

UPDATE_BOARD = "update_board"
SUBMIT_REPORT = "submit_report"


class HypothesisFields(Strict):
    """The fields of a hypothesis the model writes. Only `id` is required: an update names what changes."""

    id: str
    statement: str | None = None
    role: Role | None = None
    status: Status | None = None
    subject: str | None = None
    entities: list[str] | None = None
    changes: list[str] | None = None
    confidence: float | None = Field(None, ge=0, le=1)
    support: list[str] | None = None
    against: list[str] | None = None


class HypothesisUpdate(HypothesisFields):
    source: Source | None = None
    because: list[str] | None = None            # the handles that moved it this time
    discriminating: list[str] | None = None     # handles that would exist only if the hypothesis were true (J2)


class UpdateBoardArgs(Strict):
    hypotheses: list[HypothesisUpdate] | None = None
    timeline: list[TimelineRow] | None = None
    open_questions: list[str] | None = None


class SubmitReportArgs(Strict):
    summary: str
    impact: Impact
    hypotheses: list[HypothesisFields]
    timeline: list[TimelineRow]
    actions: list[Action]
    do_not: list[DoNot]
    alerts: list[AlertVerdict]
    open_questions: list[str]
    stakeholder_update: StakeholderUpdate


def _in_the_tools_node(args: BaseModel, ctx: ToolContext) -> Envelope:
    raise NotImplementedError("an internal tool is handled by the tools node, which owns state")


def specs() -> dict[str, ToolSpec]:
    return {UPDATE_BOARD: ToolSpec(UPDATE_BOARD, "internal", UpdateBoardArgs, _in_the_tools_node, cap_tokens=500),
            SUBMIT_REPORT: ToolSpec(SUBMIT_REPORT, "internal", SubmitReportArgs, _in_the_tools_node, cap_tokens=500)}


def toolset(read_tools: dict[str, ToolSpec]) -> dict[str, ToolSpec]:
    """Everything the investigator can call, in the order the model sees it: the read tools it was given,
    the action tool, then the internal tools. The order is fixed so the cached prefix is."""
    action = actions.spec()
    return {**read_tools, action.name: action, **specs()}
