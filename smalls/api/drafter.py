"""The stakeholder-message drafter (docs/decisions/ui-build.md ruling U8): one model call per audience, through
the agent's model adapter, with the profile the settings name. Its input is a packet built from the report,
the commander's resolution and the evidence they kept; the audience, its guide and the checks its draft must
pass come from the web app, which runs the same checks on whatever comes back.

Without a drafter (`code`, which is scripted mode's default), or when a call fails or returns nothing, the
web app's code-rendered draft, sent with the request, is kept, and the row says code wrote it and why.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from smalls.agent.config import SCRIPTED, Config
from smalls.agent.models import as_model_call_error, make_model, reasoning_text
from smalls.agent.record import cost

PROMPT = Path(__file__).parent / "prompts" / "messages.md"
TIMEOUT_S = 120                     # a short message; the server action that asked is waiting
KEPT_ROWS = 40                      # the packet's evidence lines: enough for a message, bounded for the call


def packet(incident: str | None, report: dict[str, Any], resolution: dict[str, Any] | None, kept: list[str]) -> dict[str, Any]:
    """What the drafter is given. The report's handle lists (support, against, evidence, changes) are left out: a
    message carries no handle, and the kept evidence goes in as the lines the run was shown, without their handles."""
    return {
        "incident": incident,
        "as_of": report.get("as_of"),
        "summary": report.get("summary"),
        "hypotheses": [{k: h.get(k) for k in ("statement", "role", "status", "confidence", "subject", "entities")}
                       for h in report.get("hypotheses") or []],
        "impact": report.get("impact"),
        "actions": [{k: a.get(k) for k in ("rank", "action", "owner")} for a in sorted(report.get("actions") or [], key=lambda a: a.get("rank", 0))],
        "do_not": [{k: d.get(k) for k in ("action", "why")} for d in report.get("do_not") or []],
        "timeline": [{k: t.get(k) for k in ("t_utc", "event")} for t in report.get("timeline") or []],
        "open_questions": report.get("open_questions") or [],
        "stakeholder_update": report.get("stakeholder_update"),
        "resolution": resolution,
        "evidence_kept": [line[:300] for line in kept[:KEPT_ROWS]],
    }


def user_turn(pk: dict[str, Any], audience: dict[str, Any]) -> str:
    checks = "\n".join(f"- {c}" for c in audience.get("checks") or []) or "- (none)"
    return (f"PACKET:\n```json\n{json.dumps(pk, ensure_ascii=False, indent=1)}\n```\n\n"
            f"AUDIENCE:\nName: {audience['name']}\nChannel: {audience.get('channel') or 'not given'}\n"
            f"Guide: {audience.get('guide') or 'not given'}\nChecks:\n{checks}\n")


class Drafter:
    """One profile's drafter. `draft` never raises: a failure comes back as an error for the caller to explain."""

    def __init__(self, cfg: Config, profile: str, script: str | None = None):
        if profile not in cfg.profiles:
            raise SystemExit(f"SMALLS_API_DRAFTER names no profile in the agent's config: {profile!r}")
        spec = cfg.profiles[profile]
        self.spec = spec.with_params(script=script) if spec.provider == SCRIPTED else spec
        self.cfg = cfg
        self.prompt = PROMPT.read_text(encoding="utf-8")

    @property
    def profile(self) -> str:
        return self.spec.profile

    def draft(self, pk: dict[str, Any], audience: dict[str, Any]) -> dict[str, Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        started = time.monotonic()
        try:
            model, bind = make_model(self.spec, timeout_s=min(self.cfg.timeout_s, TIMEOUT_S))
            message = (model.bind(**bind) if bind else model).invoke([SystemMessage(self.prompt), HumanMessage(user_turn(pk, audience))])
        except Exception as error:      # described by kind, never with a key value it may quote
            return {"text": None, "error": str(as_model_call_error(error))}
        name = message.response_metadata.get("model_name") or self.spec.model
        usage = dict(message.usage_metadata or {})
        out = {"usage": usage, "model": name, "cost_usd": cost({"usage": usage}, self.cfg.price(name)),
               "latency_ms": round((time.monotonic() - started) * 1000, 1)}
        text = reasoning_text(message)[1].strip()
        if message.response_metadata.get("stop_reason") == "refusal":
            return {**out, "text": None, "error": "the model declined to draft it"}
        if not text:
            return {**out, "text": None, "error": "the model returned no text"}
        return {**out, "text": text, "error": None}
