"""Config: `config.toml` beside this module, plus environment overrides. A model name appears only in that
file or in the environment (SMALLS_MODEL).

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
NO_PRICE = {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0, "cache_write_1h": 0.0}


@dataclass(frozen=True)
class ModelSpec:
    profile: str
    provider: str
    model: str
    params: dict[str, Any] = field(default_factory=dict)

    def with_params(self, **params: Any) -> "ModelSpec":
        return replace(self, params={**self.params, **params})


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
    sweep: dict[str, str]
    verifier: dict[str, Any]
    runs_dir: Path
    pricing: dict[str, dict[str, float]]

    def price(self, model: str | None) -> dict[str, float]:
        return {**NO_PRICE, **self.pricing.get(model or "", {})}

    def public(self) -> dict[str, Any]:
        """What the run record stores. No secret lives in config, so all of it but the other profiles."""
        spec = self.investigator
        return {"profile": spec.profile, "provider": spec.provider, "model": spec.model, "params": spec.params,
                "auto_approve": self.auto_approve, "call_budget": self.call_budget, "revision_calls": self.revision_calls,
                "extra_turns": self.extra_turns, "stale_rounds": self.stale_rounds, "stuck_empty": self.stuck_empty,
                "context_fraction": self.context_fraction, "headless": self.headless, "sweep": self.sweep,
                "verifier": self.verifier}


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


def load_config(path: Path | None = None, *, profile: str | None = None, interactive: bool = False,
                runs_dir: Path | None = None, **overrides: Any) -> Config:
    raw = tomllib.loads((path or PATH).read_text(encoding="utf-8"))
    name = profile or os.environ.get("SMALLS_PROFILE") or raw["default_profile"]
    if not live_allowed():
        name = SCRIPTED
    if name not in raw["profiles"]:
        raise SystemExit(f"no profile named {name!r} in {path or PATH}; there are: {', '.join(raw['profiles'])}")
    profiles = {n: _spec(n, p) for n, p in raw["profiles"].items()}
    investigator = _spec(name, raw["profiles"][name], os.environ.get("SMALLS_MODEL") if name != SCRIPTED else None)
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
        sweep=dict(raw.get("sweep", {})), verifier=dict(raw.get("verifier", {})),
        runs_dir=Path(runs_dir or os.environ.get("SMALLS_RUNS_DIR") or ROOT / raw["runs_dir"]), pricing=raw.get("pricing", {}))
    return replace(cfg, **overrides) if overrides else cfg
