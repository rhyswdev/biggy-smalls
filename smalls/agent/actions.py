"""The action tool. One tool, `propose_action`, kind `action`, and a mock: it touches nothing and records
the proposal for the incident commander (docs/decisions/tools-agent-build.md section 5, open point P2).

An action is always gated, whatever the approval policy says about reads: logs, chat and tickets are
untrusted input, so the gate is the boundary that stops text inside them from triggering anything without
a person. A real executor would plug in behind the gate, after approval.

The closed list of action kinds is prompt text, so it lives in `descriptions.yaml` and is written blind.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, create_model

from smalls.contracts.envelope import Envelope
from smalls.contracts.report import Strict
from smalls.tools import descriptions
from smalls.tools.registry import ToolContext, ToolSpec
from smalls.tools.schemas import Pair

NAME = "propose_action"
RECORDED = "MOCK: the proposal was recorded for the incident commander. Nothing was executed."


def args_model(kinds: tuple[str, ...] | None = None) -> type[BaseModel]:
    kinds = kinds or descriptions.action_kinds()
    return create_model("ProposeActionArgs", __base__=Strict, kind=(Literal[kinds], ...), target=(str, ...),
                        params=(list[Pair], Field(default_factory=list)), evidence=(list[str], ...))


def propose_action(args: BaseModel, ctx: ToolContext) -> Envelope:
    # The proposal is durable because the tools node writes it to the ledger in state, not here.
    return Envelope(RECORDED)


def spec(kinds: tuple[str, ...] | None = None) -> ToolSpec:
    return ToolSpec(NAME, "action", args_model(kinds), propose_action, cap_tokens=500, mock=True)
