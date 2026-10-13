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


@dataclass(frozen=True)
class Profile:
    name: str
    facet: str                   # the tag this profile writes
    version: int
    multi: bool                  # one yes/no question per option, where pick-one asks a single question
    kinds: tuple[str, ...]       # the kinds of unit it is asked about
    instructions: str
    options: dict[str, str]      # option -> its one line of meaning, in the order the request lists them

    @property
    def id(self) -> str:
        return f"{self.name}@{self.version}"

    def questions(self, shuffle_seed: str | None = None) -> dict[str, dict]:
        """The request's questions by id: `q` for pick-one, the option's name for each yes/no. A
        `shuffle_seed` reorders the options (the stability check's last repeat); `other` stays last."""
        names = list(self.options)
        if shuffle_seed is not None:
            head = [n for n in names if n != OTHER]
            random.Random(shuffle_seed).shuffle(head)
            names = head + [n for n in names if n == OTHER]
        if self.multi:
            return {n: {"type": "noul", "instructions": self.instructions.replace("{meaning}", self.options[n])} for n in names}
        return {"q": {"type": "choice", "instructions": self.instructions, "criteria": {n: self.options[n] for n in names}}}

    def values(self) -> tuple[str, ...]:
        """The values this profile can store."""
        return tuple(n for n in self.options if n != OTHER)


@cache
def config() -> dict:
    return tomllib.loads((HERE / "config.toml").read_text(encoding="utf-8"))


@cache
def profiles() -> dict[str, Profile]:
    raw = tomllib.loads((HERE / "profiles.toml").read_text(encoding="utf-8"))
    out = {}
    for name, p in raw.items():
        options = dict(p["options"])
        multi = p.get("type", "choice") == "multi"
        if multi and (OTHER in options or "{meaning}" not in p["instructions"]):
            raise ValueError(f"profile {name}: a yes/no profile has no `other` and names {{meaning}} in its instructions")
        if not multi and list(options)[-1] != OTHER:
            raise ValueError(f"profile {name}: `other` must be the last option")
        out[name] = Profile(name=name, facet=p.get("facet", name), version=int(p["version"]), multi=multi, kinds=tuple(p["kinds"]),
                            instructions=p["instructions"], options=options)
    facet_of: dict[str, str] = {}
    for p in out.values():
        for kind in p.kinds:
            if facet_of.setdefault(kind, p.facet) != p.facet:
                raise ValueError(f"the profiles asked about a {kind} must write one facet")
    return out


def for_kind(kind: str) -> tuple[Profile, ...]:
    """The profiles asked about a kind of unit, in file order. They all write the same facet."""
    return tuple(p for p in profiles().values() if kind in p.kinds)


def kinds() -> tuple[str, ...]:
    """The words a request uses for what a unit is. They are prompt text too."""
    return tuple(dict.fromkeys(k for p in profiles().values() for k in p.kinds))


def facets() -> dict[str, tuple[str, ...]]:
    """Every facet with the closed set of values a tag on it may hold: its profiles' options, less
    `other`, and the values code assigns."""
    out: dict[str, list[str]] = {}
    for p in profiles().values():
        seen = out.setdefault(p.facet, [])
        seen += [v for v in p.values() if v not in seen]
    return {facet: tuple(values) + CODE_VALUES.get(facet, ()) for facet, values in out.items()}


def multi_facets() -> set[str]:
    """Facets whose tag is a list of values, because a yes/no profile writes them."""
    return {p.facet for p in profiles().values() if p.multi}


def classifier_version() -> str:
    return config()["classifier"]["version"]


def floor() -> float:
    return float(config()["rules"]["floor"])


def tolerance() -> float:
    return float(config()["rules"]["tolerance"])


def prompt_texts() -> list[tuple[str, str]]:
    """Every piece of text a request carries besides the record itself, as (where, text): what the
    bundle-string lint reads."""
    out = [("tagging kinds", ", ".join(kinds()))]
    for p in profiles().values():
        out.append((f"profile {p.id} instructions", p.instructions))
        out += [(f"profile {p.id} option {name}", f"{name}: {meaning}") for name, meaning in p.options.items()]
    return out
