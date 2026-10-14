"""Config: `config.toml` beside this module, plus environment overrides. A model name appears only in that
file or in the environment (SMALLS_MODEL).

Models are chosen per role (docs/decisions/eval-loop.md, V5). Every agent that calls a model is a role, and
a named set maps each role to a profile, written `profile` or `profile@effort`. The last one given wins:
the config's `default_set`, then SMALLS_SET, then the `set_name` argument (`--set`), then each role override
from SMALLS_ROLES and then the `roles` argument (`--role`); the legacy `profile` argument and SMALLS_PROFILE
name the investigator's profile, and SMALLS_EFFORT or the `effort` argument (`--effort`) its effort. A run
records the map it resolved.

Nothing here decides to call a model by looking for a key. Compose passes the whole `.env` into every
container, tests included, so under pytest the profile is the scripted one whatever the environment says,
unless SMALLS_LIVE=1 (docs/decisions/tools-agent-build.md section 10.4, rule 1).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PATH = HERE / "config.toml"
SCRIPTED = "scripted"
# The agents that call a model. Arm A's single call is the investigator's.
ROLES = ("investigator", "contrarian", "claims", "overfit", "drafter")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
NO_PRICE = {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0, "cache_write_1h": 0.0}


@dataclass(frozen=True)
class ModelSpec:
    profile: str
    provider: str
    model: str
    params: dict[str, Any] = field(default_factory=dict)

    def with_params(self, **params: Any) -> "ModelSpec":
        return replace(self, params={**self.params, **params})

    @property
    def effort(self) -> str | None:
        """The reasoning effort this spec sends, wherever its provider keeps it."""
        reasoning = self.params.get("reasoning")
        return self.params.get("effort") or (reasoning.get("effort") if isinstance(reasoning, dict) else None)

    def with_effort(self, effort: str | None) -> "ModelSpec":
        """This spec at another effort, set where its provider reads it. The scripted model has none."""
        if not effort or self.provider == SCRIPTED:
            return self
        if self.provider == "openai":
            return self.with_params(reasoning={**(self.params.get("reasoning") or {}), "effort": effort})
        return self.with_params(effort=effort)

    def public(self) -> dict[str, Any]:
        return {"profile": self.profile, "provider": self.provider, "model": self.model, "effort": self.effort}


@dataclass(frozen=True)
class Config:
    investigator: ModelSpec
    profiles: dict[str, ModelSpec]
    auto_approve: bool
    call_budget: int
    revision_calls: int
    extra_turns: int
    stale_rounds: int
    stuck_empty: int
    context_fraction: float
    headless: str
    fallback_profile: str
    cache_ratio_floor: float
    timeout_s: float
    heartbeat_s: float
    sweep: dict[str, str]
    verifier: dict[str, Any]
    runs_dir: Path
    pricing: dict[str, dict[str, float]]
    roles: dict[str, ModelSpec | None] = field(default_factory=dict)
    set_name: str = ""

    def role(self, name: str) -> ModelSpec | None:
        """The spec a role resolved to; None when the set leaves the role empty, which cuts it (a verifier pass)."""
        return self.roles.get(name, self.investigator if name == "investigator" else None)

    def price(self, model: str | None) -> dict[str, float]:
        return {**NO_PRICE, **self.pricing.get(model or "", {})}

    def public(self) -> dict[str, Any]:
        """What the run record stores. No secret lives in config, so all of it but the other profiles."""
        spec = self.investigator
        return {"profile": spec.profile, "provider": spec.provider, "model": spec.model, "params": spec.params,
                "auto_approve": self.auto_approve, "call_budget": self.call_budget, "revision_calls": self.revision_calls,
                "extra_turns": self.extra_turns, "stale_rounds": self.stale_rounds, "stuck_empty": self.stuck_empty,
                "context_fraction": self.context_fraction, "headless": self.headless, "sweep": self.sweep,
                "verifier": self.verifier, "set": self.set_name,
                "roles": {name: (s.public() if s else None) for name, s in self.roles.items()}}


def under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ


def live_allowed() -> bool:
    """A live model may be built outside pytest, or inside it only when SMALLS_LIVE=1."""
    return not under_pytest() or os.environ.get("SMALLS_LIVE") == "1"


def _spec(name: str, raw: dict[str, Any], model_override: str | None = None) -> ModelSpec:
    params = dict(raw)
    provider = params.pop("provider")
    model = params.pop("model", "")
    return ModelSpec(name, provider, model_override or model, params)


def _assignment(text: str, where: str) -> tuple[str, str | None]:
    """`profile` or `profile@effort`, checked for its form; the profile is checked against the config later."""
    profile, _, effort = text.strip().partition("@")
    if effort and effort not in EFFORTS:
        raise SystemExit(f"{where}: no effort named {effort!r}; there are: {', '.join(EFFORTS)}")
    return profile, (effort or None)


def _overrides(text: str, where: str) -> dict[str, str]:
    """`role=profile[@effort]` pairs, comma-separated (SMALLS_ROLES) or given one at a time (`--role`)."""
    out: dict[str, str] = {}
    for pair in filter(None, (part.strip() for part in text.split(","))):
        role, sep, value = pair.partition("=")
        if not sep or role.strip() not in ROLES:
            raise SystemExit(f"{where}: {pair!r} is not role=profile[@effort]; the roles are: {', '.join(ROLES)}")
        out[role.strip()] = value.strip()
    return out


def resolve_roles(raw: dict[str, Any], profiles: dict[str, ModelSpec], *, set_name: str | None = None,
                  roles: list[str] | dict[str, str] | None = None, profile: str | None = None,
                  effort: str | None = None, model_override: str | None = None) -> tuple[str, dict[str, ModelSpec | None]]:
    """The set's name and every role's spec, after the overrides, in the order the module docstring gives."""
    sets = raw.get("sets") or {}
    name = set_name or os.environ.get("SMALLS_SET") or raw.get("default_set", "")
    if sets:
        if name not in sets:
            raise SystemExit(f"no set named {name!r} in the config; there are: {', '.join(sets)}")
        chosen = dict(sets[name])
        missing = [r for r in ROLES if r not in chosen]
        unknown = [r for r in chosen if r not in ROLES]
        if missing or unknown:
            raise SystemExit(f"set {name!r} must name every role once: {', '.join(ROLES)}"
                             + (f"; missing {', '.join(missing)}" if missing else "") + (f"; unknown {', '.join(unknown)}" if unknown else ""))
    else:       # a config written before sets: the default profile and the [verifier] profiles
        verifier = raw.get("verifier", {})
        base = raw["default_profile"]
        chosen = {"investigator": base, "contrarian": verifier.get("contrarian_profile", ""),
                  "claims": verifier.get("claims_profile", ""), "overfit": base, "drafter": base}
    chosen.update(_overrides(os.environ.get("SMALLS_ROLES", ""), "SMALLS_ROLES"))
    if isinstance(roles, dict):
        chosen.update(roles)
    else:
        for item in roles or ():
            chosen.update(_overrides(item, "--role"))
    legacy = profile or os.environ.get("SMALLS_PROFILE")
    if legacy:
        chosen["investigator"] = legacy
    investigator_effort = effort or os.environ.get("SMALLS_EFFORT")
    if investigator_effort:
        base, _ = _assignment(chosen["investigator"], "investigator")
        chosen["investigator"] = f"{base}@{investigator_effort}"
    resolved: dict[str, ModelSpec | None] = {}
    for role in ROLES:
        text = str(chosen.get(role) or "")
        if not text:
            resolved[role] = None
            continue
        prof, level = _assignment(text, role)
        if prof not in profiles:
            raise SystemExit(f"role {role} names no profile {prof!r}; there are: {', '.join(profiles)}")
        spec = profiles[prof].with_effort(level)
        if role == "investigator" and model_override and spec.provider != SCRIPTED:
            spec = replace(spec, model=model_override)
        resolved[role] = spec
    if not resolved["investigator"]:
        raise SystemExit("the investigator role must name a profile")
    return name, resolved


def load_config(path: Path | None = None, *, profile: str | None = None, interactive: bool = False,
                runs_dir: Path | None = None, set_name: str | None = None, roles: list[str] | dict[str, str] | None = None,
                effort: str | None = None, **overrides: Any) -> Config:
    raw = tomllib.loads((path or PATH).read_text(encoding="utf-8"))
    if profile and profile not in raw["profiles"]:
        raise SystemExit(f"no profile named {profile!r} in {path or PATH}; there are: {', '.join(raw['profiles'])}")
    profiles = {n: _spec(n, p) for n, p in raw["profiles"].items()}
    set_used, resolved = resolve_roles(raw, profiles, set_name=set_name, roles=roles, profile=profile, effort=effort,
                                       model_override=os.environ.get("SMALLS_MODEL"))
    if not live_allowed():      # under pytest every role is the scripted model, whatever the set says
        resolved = {role: (profiles[SCRIPTED] if spec else None) for role, spec in resolved.items()}
    investigator = resolved["investigator"]
    assert investigator is not None
    headless = str(raw["headless"])
    if headless not in ("approve", "defer", "reject"):
        raise SystemExit(f"headless must be approve, defer or reject, not {headless!r}")
    cfg = Config(
        investigator=investigator, profiles=profiles,
        auto_approve=bool(raw["auto_approve"]) and not interactive,
        call_budget=int(raw["call_budget"]), revision_calls=int(raw["revision_calls"]), extra_turns=int(raw["extra_turns"]),
        stale_rounds=int(raw["stale_rounds"]), stuck_empty=int(raw["stuck_empty"]),
        context_fraction=float(raw["context_fraction"]), headless=headless, timeout_s=float(raw["timeout_s"]),
        fallback_profile=str(raw.get("fallback_profile", "")), cache_ratio_floor=float(raw.get("cache_ratio_floor", 0.5)),
        sweep=dict(raw.get("sweep", {})), verifier=dict(raw.get("verifier", {})), heartbeat_s=float(raw.get("heartbeat_s", 10)),
        runs_dir=Path(runs_dir or os.environ.get("SMALLS_RUNS_DIR") or ROOT / raw["runs_dir"]), pricing=raw.get("pricing", {}),
        roles=resolved, set_name=set_used)
    return replace(cfg, **overrides) if overrides else cfg
