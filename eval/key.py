"""The answer key: its schema, its validation, the manifest it is read against and the alias table
(docs/PLAN.md section 3.3; row S1 of docs/decisions/eval-loop.md, whose section 14 holds the rulings).

The key lives in `scenario/content/answer_key.yaml` and is rendered into the bundle's `_internal/`. It names
facts by `sig_id` and generated class, entities by their `scenario/facts.yaml` key, and never a handle. The
scorer reads three things beside a run: the key, the manifest (what every handle is, and when it was known)
and the alias table (every name an entity goes by), all from the bundle and the scenario, never from a tool.

    python -m eval.key [--bundle <folder>]      # validate the key; exit 1 with the errors
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from smalls.contracts.handles import parse_metric_handle
from smalls.contracts.report import Role, Verdict

ROOT = Path(__file__).resolve().parents[1]
BUNDLES = ROOT / "bundles"
DEFAULT_BUNDLE = "larkspur-checkout-2026-10-13"
FACTS = ROOT / "scenario" / "facts.yaml"


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Fact(Strict):
    any_of: list[str] = Field(min_length=1)     # `sig.<id>` or `class:<family>.<name>`


class Leading(Strict):
    entity: str
    change_any: list[str] = Field(default_factory=list)     # change ids, build ids or versions that name the change


class EntityRef(Strict):
    entity: str


class Allowed(Strict):
    """How a hypothesis matching a trap may stand without falling for it, when it is not rejected."""

    role: list[Role] = Field(default_factory=list)
    status: list[Literal["open"]] = Field(default_factory=list)


class Trap(Strict):
    match: EntityRef
    allowed: Allowed
    absent_ok: bool = False


class Owner(Strict):
    any: list[str] = Field(min_length=1)
    never: list[str] = Field(default_factory=list)


class Confidence(Strict):
    wrong_leading_max: float
    right_leading_min: float | None = None


class Judge(Strict):
    """Per-bundle checklist items, id to the text the judge reads. The judge's prompt stays generic."""

    mechanism: dict[str, str]
    actions: dict[str, str]
    harmful_top3: dict[str, str]


class Checkpoint(Strict):
    leading: Leading | None = None
    leading_any: list[str] = Field(default_factory=list)    # a looser bar: one of these may lead, or still be open
    trigger: EntityRef | None = None
    trigger_optional: bool = False
    required_facts: list[str] = Field(default_factory=list)
    supporting_facts: list[str] = Field(default_factory=list)
    non_chat_required: bool = False
    traps: dict[str, Trap] = Field(default_factory=dict)
    contributing_any: list[str] = Field(default_factory=list)
    owner: Owner | None = None
    confidence: Confidence
    judge: Judge | None = None

    @model_validator(mode="after")
    def _one_leading(self) -> "Checkpoint":
        if bool(self.leading) == bool(self.leading_any):
            raise ValueError("a checkpoint gives exactly one of `leading` and `leading_any`")
        return self

    @property
    def cause_entities(self) -> list[str]:
        return [self.leading.entity] if self.leading else list(self.leading_any)


class AlertStream(Strict):
    sig: str | None = None
    class_: str | None = Field(default=None, alias="class")
    accept: list[Verdict] = Field(min_length=1)

    @model_validator(mode="after")
    def _one_match(self) -> "AlertStream":
        if bool(self.sig) == bool(self.class_):
            raise ValueError("an alert stream is matched by exactly one of `sig` and `class`")
        return self


class Alerts(Strict):
    default: list[Verdict] = Field(min_length=1)
    streams: list[AlertStream]


class AnswerKey(Strict):
    facts: dict[str, Fact]
    checkpoints: dict[str, Checkpoint]
    alerts: Alerts

    def checkpoint(self, cid: str) -> Checkpoint | None:
        """The checkpoint by the run's id (`1452`) or the key's (`14:52`)."""
        return next((cp for name, cp in self.checkpoints.items() if name.replace(":", "") == cid.replace(":", "")), None)


# -- the manifest ------------------------------------------------------------------------------------


@dataclass
class Manifest:
    """Every record version the bundle holds, by handle. Metric series are stored by identity, with no window."""

    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    def get(self, handle: str) -> list[dict[str, Any]]:
        if handle.startswith("metric:"):
            query, _, _ = parse_metric_handle(handle)
            return self.rows.get(f"metric:{query}", [])
        return self.rows.get(handle, [])

    def known(self, handle: str) -> bool:
        return bool(self.get(handle))

    def first_known(self, handle: str) -> str | None:
        """The earliest `t_known` of any version: a handle is future at a checkpoint only if no version was known."""
        rows = self.get(handle)
        return min(r["t_known"] for r in rows) if rows else None

    def visible(self, handle: str, as_of: str) -> list[dict[str, Any]]:
        """The versions visible at `as_of` (PLAN 3.4): known before it, and valid at it."""
        at = _norm(as_of)
        out = []
        for r in self.get(handle):
            if _norm(r["t_known"]) >= at:
                continue
            if r.get("valid_from") and _norm(r["valid_from"]) > at:
                continue
            if r.get("valid_to") and _norm(r["valid_to"]) <= at:
                continue
            out.append(r)
        return out

    def sigs_and_classes(self, handle: str, as_of: str) -> set[str]:
        """`sig.<id>` and `class:<name>` refs of the versions visible at `as_of`, the form the key's facts use."""
        out: set[str] = set()
        for r in self.visible(handle, as_of):
            if r.get("sig_id"):
                out.add(r["sig_id"])
            if r.get("class"):
                out.add(f"class:{r['class']}")
        return out


def _norm(t: str) -> str:
    """ISO times compared as text: `2026-10-13T14:52:00Z` and `...00.000Z` must order the same."""
    t = t.rstrip("Z")
    return t if "." in t else t + ".000"


@cache
def load_manifest(bundle: str = DEFAULT_BUNDLE) -> Manifest:
    m = Manifest()
    for line in (bundle_dir(bundle) / "_internal" / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            m.rows.setdefault(row["handle"], []).append(row)
    return m


def bundle_dir(bundle: str | Path) -> Path:
    p = Path(bundle)
    return p if p.is_absolute() or p.exists() else BUNDLES / str(bundle)


@cache
def load_key(bundle: str = DEFAULT_BUNDLE) -> AnswerKey:
    return AnswerKey.model_validate(yaml.safe_load((bundle_dir(bundle) / "_internal" / "answer_key.yaml").read_text(encoding="utf-8")))


# -- the alias table ---------------------------------------------------------------------------------

# A name is matched whole: the characters around it may not be part of a name. A hyphen, a dot and an
# underscore are, so `payments` does not match inside `payments-gateway`.
NAME_CHAR = r"[a-z0-9._-]"


@dataclass
class Aliases:
    """Every name an entity goes by, from `scenario/facts.yaml`, its key included, compared without case."""

    names: dict[str, set[str]]      # lower-cased name -> entity keys

    def entities_in(self, text: str) -> list[str]:
        """The entity a free-text subject names. An exact name wins; otherwise the name that starts earliest in
        the text, the longest at that place. Several entities come back only when one name is shared."""
        t = text.strip().lower()
        if not t:
            return []
        if t in self.names:
            return sorted(self.names[t])
        best: tuple[int, int] | None = None
        found: set[str] = set()
        for name, ents in self.names.items():
            m = re.search(rf"(?<!{NAME_CHAR}){re.escape(name)}(?!{NAME_CHAR})", t)
            if not m:
                continue
            rank = (m.start(), -len(name))
            if best is None or rank < best:
                best, found = rank, set(ents)
            elif rank == best:
                found |= ents
        return sorted(found)

    def named(self, text: str, entity: str) -> bool:
        """Whether any name of `entity` appears anywhere in `text`."""
        t = text.lower()
        return any(entity in ents and re.search(rf"(?<!{NAME_CHAR}){re.escape(name)}(?!{NAME_CHAR})", t)
                   for name, ents in self.names.items())


MIN_NAME = 3        # a shorter alias would match by accident


@cache
def load_aliases(path: Path = FACTS) -> Aliases:
    facts = yaml.safe_load(path.read_text(encoding="utf-8"))
    names: dict[str, set[str]] = {}
    for key, entity in facts["entities"].items():
        for name in [key, *(a for per_source in (entity.get("aliases") or {}).values() for a in per_source or [])]:
            if len(name) >= MIN_NAME:
                names.setdefault(name.lower(), set()).add(key)
    return Aliases(names)


# -- validation ----------------------------------------------------------------------------------------


def checkpoint_time(bundle: str, cid: str) -> str:
    """The checkpoint's `now`, from the scenario's checkpoint list."""
    day = yaml.safe_load(FACTS.read_text(encoding="utf-8"))["date"]
    hhmm = cid.replace(":", "")
    return f"{day}T{hhmm[:2]}:{hhmm[2:]}:00Z"


def validate(bundle: str = DEFAULT_BUNDLE) -> list[str]:
    """Every error in the key, read against the manifest and the alias table. Empty when it validates."""
    try:
        key = load_key(bundle)
    except Exception as error:      # a schema error is the whole answer
        return [f"schema: {error}"]
    manifest, aliases = load_manifest(bundle), load_aliases()
    entities = set().union(*aliases.names.values())
    errors: list[str] = []
    all_refs = {ref for rows in manifest.rows.values() for r in rows
                for ref in ([r["sig_id"]] if r.get("sig_id") else []) + ([f"class:{r['class']}"] if r.get("class") else [])}
    for name, cp in key.checkpoints.items():
        as_of = checkpoint_time(bundle, name)
        visible = {ref for h in manifest.rows for ref in manifest.sigs_and_classes(h, as_of)}
        for fid in cp.required_facts + cp.supporting_facts:
            if fid not in key.facts:
                errors.append(f"{name}: fact {fid} is not defined")
                continue
            errors += [f"{name} {fid}: {ref} has no manifest row visible at {name}" for ref in key.facts[fid].any_of if ref not in visible]
        named = cp.cause_entities + cp.contributing_any + [t.match.entity for t in cp.traps.values()] + ([cp.trigger.entity] if cp.trigger else [])
        errors += [f"{name}: entity {e!r} is not a facts.yaml entity" for e in named if e not in entities]
        if cp.leading and cp.judge is None:
            errors.append(f"{name}: a checkpoint with a `leading` cause needs its judge checklist")
        if cp.leading and cp.confidence.right_leading_min is None:
            errors.append(f"{name}: a checkpoint with a `leading` cause needs `right_leading_min`")
    for i, s in enumerate(key.alerts.streams):
        ref = s.sig or f"class:{s.class_}"
        if ref not in all_refs:
            errors.append(f"alerts.streams[{i}]: {ref} matches no manifest row")
    return errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m eval.key", description="Validate the answer key against the manifest and the alias table.")
    p.add_argument("--bundle", default=DEFAULT_BUNDLE)
    args = p.parse_args(argv)
    errors = validate(args.bundle)
    for e in errors:
        print(e)
    print(f"answer key: {'valid' if not errors else f'{len(errors)} errors'}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
