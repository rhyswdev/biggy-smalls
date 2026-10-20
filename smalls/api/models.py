"""Request bodies (docs/decisions/ui-build.md section 6.4). Every one is closed and every text has a length
limit, so the API never takes more than a person would type."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TEXT = 8000         # a trigger or a pasted note
NOTE = 4000
REASON = 2000
MESSAGE = 4000      # one stakeholder message

AUDIENCES = ("exec", "support", "status", "eng")
Audience = Literal["exec", "support", "status", "eng"]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NewChat(Closed):
    origin: Literal["chat", "headless", "blank"]
    trigger: str | None = Field(None, max_length=TEXT)
    auto_approve: bool = False
    title: str | None = Field(None, max_length=200)
    incident: str | None = Field(None, max_length=40)       # a ServiceNow incident the chat is linked to (v4 plan 6.7)


class ImportIn(Closed):
    """An incident number to import, or a link that carries one."""

    number: str = Field(min_length=1, max_length=300)


class Answer(Closed):
    """One call's answer: approve (optionally with edited arguments), skip (defer) or reject with a reason."""

    approve: bool = False
    defer: bool = False
    reason: str | None = Field(None, max_length=REASON)
    args: dict[str, Any] | None = None

    @model_validator(mode="after")
    def one_answer(self) -> "Answer":
        if self.approve and self.defer:
            raise ValueError("an answer approves or defers, not both")
        if not self.approve and not self.defer and not (self.reason or "").strip():
            raise ValueError("a rejection needs a reason")
        if self.args is not None and not self.approve:
            raise ValueError("edited arguments come with an approval")
        return self

    def for_runner(self) -> dict[str, Any]:
        if self.approve:
            return {"approve": True, **({"args": self.args} if self.args is not None else {})}
        if self.defer:
            return {"defer": True, **({"reason": self.reason} if self.reason else {})}
        return {"approve": False, "reason": (self.reason or "").strip()}


class GateAnswer(Closed):
    interrupt_id: str = Field(min_length=1, max_length=200)
    answers: dict[str, Answer] = Field(min_length=1, max_length=50)
    note: str | None = Field(None, max_length=NOTE)


class NoteIn(Closed):
    text: str = Field(min_length=1, max_length=NOTE)


class Switch(Closed):
    on: bool


class MoreRun(Closed):
    resume: bool = False


RESOLUTION_CODES = ("Solved (Work Around)", "Solved (Permanently)", "Solved Remotely (Work Around)", "Not Solved (Not Reproducible)")
ResolutionCode = Literal["Solved (Work Around)", "Solved (Permanently)", "Solved Remotely (Work Around)", "Not Solved (Not Reproducible)"]


class Resolution(Closed):
    path: Literal["agent", "own"]
    code: ResolutionCode = "Solved (Work Around)"       # ServiceNow's close codes (v4 plan 5.4)
    note: str = Field(min_length=1, max_length=NOTE)
    resolved_at: str = Field(pattern=r"^\d{1,2}:\d{2}$")
    resolved_by: str = Field(min_length=1, max_length=200)


class WriteupEdit(Closed):
    kept: list[str] = Field(max_length=500)
    note: str | None = Field(None, max_length=NOTE)


class WriteupSave(Closed):
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=40000)
    terms: list[str] = Field(max_length=100)


class AudienceSpec(Closed):
    """One audience as the web app defines it (U8): who, where, what they need, the checks its draft must pass,
    with their values filled from the report, and the code-rendered draft kept when no model drafts it."""

    key: Audience
    name: str = Field(min_length=1, max_length=80)
    channel: str = Field("", max_length=120)
    guide: str = Field("", max_length=300)
    checks: list[Annotated[str, Field(max_length=400)]] = Field(default_factory=list, max_length=12)
    fallback: str = Field(max_length=MESSAGE)


class MessagesDraft(Closed):
    audiences: list[AudienceSpec] = Field(min_length=1, max_length=len(AUDIENCES))

    @model_validator(mode="after")
    def once_each(self) -> "MessagesDraft":
        keys = [a.key for a in self.audiences]
        if len(set(keys)) != len(keys):
            raise ValueError("each audience is drafted once per request")
        return self


class MessageText(Closed):
    text: str = Field(max_length=MESSAGE)
