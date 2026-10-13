"""What the tagger sees. A unit is a kind and a text built from one record version's own bytes (rule T1):
no neighbouring records, no bundle summary, no corpus statistics. Many records share a unit: every log
record of a template, every version of a ticket whose text did not change, every event of one alert,
every bucket of one metric series.

The unit's kind decides the profiles it is asked by, and they its facet (taxonomy v1 as amended):
- signal: log records, by template; alerts, by title; alert definitions and incident tickets, by title and
  text (each of these asked pick-one and as a yes or no per option); metric series, by their query
- act: chat messages a person wrote, and notes on tickets and pages
- change_kind: change requests, pipeline runs and commits
A bot post is `act=automated` by its subtype, in code, with no request. Everything else is untagged.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from smalls.tools.ingest.base import Point, Record
from smalls.tools.ingest.tagging.profiles import Profile, classifier_version, for_kind

CLIP = 1200          # characters of free text a request carries; the state is hashed after clipping
CLIP_SHORT = 600
_STATE_PREFIX = re.compile(r"^\[[^\]]+\]\s*")       # "[Triggered on host:x] title": the alert is the title


@dataclass(frozen=True)
class Unit:
    kind: str
    text: str

    @property
    def profiles(self) -> tuple[Profile, ...]:
        return for_kind(self.kind)

    @property
    def facet(self) -> str:
        return self.profiles[0].facet

    @property
    def state(self) -> dict:
        return {"kind": self.kind, "text": self.text}

    @property
    def canonical(self) -> str:
        return json.dumps(self.state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    @property
    def id(self) -> str:
        """A stable name for the unit's content alone: the same text has the same id under any profile or
        classifier version. The hand labels are keyed by it."""
        return hashlib.sha256(self.canonical.encode("utf-8")).hexdigest()[:12]


def tag_key(unit: Unit, profile: Profile, version: str | None = None) -> str:
    """The cache key of one request: sha256 over the pinned classifier version, the profile id and
    version, and the canonical state."""
    parts = (version or classifier_version(), profile.id, unit.canonical)
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def unit_key(unit: Unit, version: str | None = None) -> str:
    """The key a row carries (`tag_key`): the pinned classifier version and the canonical state, whichever
    profiles were asked. It joins a row to every answer about its text."""
    return hashlib.sha256("\n".join((version or classifier_version(), unit.canonical)).encode("utf-8")).hexdigest()


def _join(*parts: str | None, clip: int = CLIP_SHORT) -> str:
    head, *rest = [p.strip() for p in parts if p and p.strip()] or [""]
    return "\n".join([head, *[r[:clip] for r in rest]])


def code_tags(r: Record) -> dict[str, list[str]]:
    """Tags that need no judgement."""
    if r.kind == "message" and r.attrs.get("subtype") == "bot_message":
        return {"act": ["automated"]}
    return {}


def unit_for(r: Record) -> Unit | None:
    if r.kind == "log":
        return Unit("log line", r.template) if r.template else None
    if r.kind == "alert" or (r.kind == "incident" and r.stream_key):       # a page is its alert's title
        title = _STATE_PREFIX.sub("", r.title or "")
        return Unit("alert", title) if title else None
    if r.kind == "alert_def":
        return Unit("alert definition", _join(r.title, r.body))
    if r.kind == "incident":
        return Unit("incident ticket", _join(r.title, r.body))
    if r.kind == "message":
        if r.attrs.get("subtype") or not (r.body or "").strip():
            return None
        return Unit("chat message", r.body.strip()[:CLIP])
    if r.kind == "note":
        text = (r.body or r.title or "").strip()
        return Unit("work note", text[:CLIP]) if text else None
    if r.kind == "change":
        return Unit("change request", _join(r.title, r.body))
    if r.kind == "build":
        params = ", ".join(f"{k}={v}" for k, v in (r.attrs.get("parameters") or {}).items())
        return Unit("pipeline run", _join(r.attrs.get("job"), f"parameters: {params}" if params else None,
                                         "; ".join(r.attrs.get("causes") or [])))
    if r.kind == "commit":
        paths = ", ".join((r.attrs.get("paths") or [])[:8])
        return Unit("commit", _join(r.title, f"files: {paths}" if paths else None))
    return None


def unit_for_point(p: Point) -> Unit:
    """A metric bucket's unit is its series: the expression that names what is measured."""
    return Unit("metric series", p.series)
