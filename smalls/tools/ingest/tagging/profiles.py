"""The tagging config and the tag profiles, read from the two TOML files beside this module. Nothing here
imports the classifier SDK: ingest uses this to read the cache, and `smalls tag` uses it to build requests.
"""

from __future__ import annotations

import random
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
OTHER = "other"
# Values code assigns with no request: a bot post is automated by its subtype, not by judgement.
CODE_VALUES = {"act": ("automated",)}
# The words a request uses for what a record is. They are prompt text too.
KINDS = ("log line", "alert", "alert definition", "incident ticket", "chat message", "work note", "change request",
         "pipeline run", "commit")


@dataclass(frozen=True)
class Profile:
    facet: str
    version: int
    instructions: str
    options: dict[str, str]      # option -> its one line of meaning, in the order the request lists them

    @property
    def id(self) -> str:
        return f"{self.facet}@{self.version}"

    def question(self, shuffle_seed: str | None = None) -> dict:
        """The request's one question. `shuffle_seed` reorders the options (the stability check's last
        repeat); `other` stays last."""
        names = list(self.options)
        if shuffle_seed is not None:
            head = [n for n in names if n != OTHER]
            random.Random(shuffle_seed).shuffle(head)
            names = head + [n for n in names if n == OTHER]
        return {"type": "choice", "instructions": self.instructions, "criteria": {n: self.options[n] for n in names}}

    def vocabulary(self) -> tuple[str, ...]:
        """Every value a record's tag on this facet may hold."""
        return tuple(n for n in self.options if n != OTHER) + CODE_VALUES.get(self.facet, ())


@cache
def config() -> dict:
    return tomllib.loads((HERE / "config.toml").read_text(encoding="utf-8"))


@cache
def profiles() -> dict[str, Profile]:
    raw = tomllib.loads((HERE / "profiles.toml").read_text(encoding="utf-8"))
    out = {}
    for facet, p in raw.items():
        options = dict(p["options"])
        if list(options)[-1] != OTHER:
            raise ValueError(f"profile {facet}: `other` must be the last option")
        out[facet] = Profile(facet=facet, version=int(p["version"]), instructions=p["instructions"], options=options)
    return out


def classifier_version() -> str:
    return config()["classifier"]["version"]


def floor() -> float:
    return float(config()["rules"]["floor"])


def prompt_texts() -> list[tuple[str, str]]:
    """Every piece of text a request carries besides the record itself, as (where, text): what the
    bundle-string lint reads."""
    out = [("tagging kinds", ", ".join(KINDS))]
    for p in profiles().values():
        out.append((f"profile {p.id} instructions", p.instructions))
        out += [(f"profile {p.id} option {name}", f"{name}: {meaning}") for name, meaning in p.options.items()]
    return out
