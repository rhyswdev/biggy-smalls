"""Config: a TOML file plus environment overrides. The only place a model name may appear is config.toml
or the environment (SMALLS_MODEL)."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class ModelSpec:
    profile: str
    provider: str
    model: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    investigator: ModelSpec
    auto_approve: bool
    gate_actions_always: bool
    tool_call_budget: int
    runs_dir: Path
    pricing: dict[str, dict[str, float]]

    def price(self, model: str) -> dict[str, float]:
        return self.pricing.get(model, {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0})

    def public(self) -> dict[str, Any]:
        """What the run record stores. No secrets live in config, so all of it."""
        return {
            "investigator": {"profile": self.investigator.profile, "provider": self.investigator.provider,
                             "model": self.investigator.model, "params": self.investigator.params},
            "auto_approve": self.auto_approve,
            "gate_actions_always": self.gate_actions_always,
            "tool_call_budget": self.tool_call_budget,
        }


def load_config(path: Path | None = None, *, profile: str | None = None, interactive: bool = False,
                runs_dir: Path | None = None) -> Config:
    raw = tomllib.loads((path or ROOT / "config.toml").read_text())
    name = profile or os.environ.get("SMALLS_PROFILE", "scripted")
    prof = dict(raw["profiles"][name])
    provider = prof.pop("provider")
    model = os.environ.get("SMALLS_MODEL") or prof.pop("model")
    prof.pop("model", None)
    run = raw["run"]
    return Config(
        investigator=ModelSpec(name, provider, model, prof),
        auto_approve=bool(run["auto_approve"]) and not interactive,
        gate_actions_always=bool(run["gate_actions_always"]),
        tool_call_budget=int(run["tool_call_budget"]),
        runs_dir=runs_dir or ROOT / run["runs_dir"],
        pricing=raw.get("pricing", {}),
    )
