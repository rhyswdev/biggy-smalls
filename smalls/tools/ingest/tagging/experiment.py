"""Trying a candidate facet before it is ruled in. An experiment is a TOML file of candidate facets and an
answers file beside it; nothing an experiment writes is read by ingest, and it never touches the committed
cache. It runs through `smalls tag --experiment`, so `smalls tag` stays the only command that calls the
classifier.

A candidate facet is either a `choice` (one option from a closed set, like the shipped profiles) or a
`multi`: one yes/no question per option, for texts that are two things at once. A request carries one
unit's `{"kind", "text"}` and every question that applies to its kind. Candidate facets are prompt text:
the runner refuses a file that contains a bundle string when it is given the list to check against.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from smalls.tools.ingest.tagging.profiles import OTHER
from smalls.tools.ingest.tagging.units import Unit


@dataclass(frozen=True)
class Candidate:
    facet: str
    multi: bool
    applies: tuple[str, ...]
    instructions: str
    options: dict[str, str]

    def questions(self) -> dict[str, dict]:
        if not self.multi:
            return {self.facet: {"type": "choice", "instructions": self.instructions, "criteria": dict(self.options)}}
        return {f"{self.facet}.{name}": {"type": "noul", "instructions": self.instructions.replace("{meaning}", meaning)}
                for name, meaning in self.options.items() if name != OTHER}


def load(path: Path) -> tuple[dict[str, Candidate], str]:
    """The candidates in a spec file, and a hash of the file: answers are kept per spec version."""
    text = Path(path).read_text(encoding="utf-8")
    out = {}
    for facet, c in tomllib.loads(text).items():
        out[facet] = Candidate(facet=facet, multi=c.get("type", "choice") == "multi", applies=tuple(c["applies"]),
                               instructions=c["instructions"], options=dict(c["options"]))
    return out, hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def prompt_texts(path: Path) -> list[tuple[str, str]]:
    return [(str(path), Path(path).read_text(encoding="utf-8"))]


def answers_path(spec: Path) -> Path:
    return Path(spec).with_suffix(".answers.jsonl")


def read_answers(spec: Path) -> dict[str, dict]:
    path = answers_path(spec)
    _, version = load(spec)
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x] if path.is_file() else []
    return {r["unit"]: r for r in rows if r["spec"] == version}


def run(spec: Path, units: dict[str, Unit], ask, ask_all, concurrency: int = 8) -> dict:
    """Ask every candidate question of every unit it applies to, one request per unit, skipping units this
    version of the spec already has answers for. The answers file holds no record text."""
    candidates, version = load(spec)
    have = read_answers(spec)
    jobs = []
    for unit_id, unit in sorted(units.items()):
        questions = {}
        for c in candidates.values():
            if unit.kind in c.applies:
                questions.update(c.questions())
        if questions and unit_id not in have:
            jobs.append((unit_id, unit.state, questions))
    done, failures = ask_all(jobs, ask, concurrency)
    for (unit_id, _, _), got in done:
        have[unit_id] = {"unit": unit_id, "kind": units[unit_id].kind, "spec": version, "classifier": got["classifier"],
                         "answers": got["answers"], "usage": got["usage"]}
    if done:
        answers_path(spec).write_text("".join(json.dumps(have[k], ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for k in sorted(have)),
                                      encoding="utf-8")
    return {"units": len(have), "requests": len(done), "failures": failures,
            "input_tokens": sum(g["usage"].get("input_tokens") or 0 for _, g in done),
            "output_tokens": sum(g["usage"].get("output_tokens") or 0 for _, g in done)}


def decide(candidate: Candidate, answers: dict, floor: float) -> list[str]:
    """The values a candidate would store for one unit at a floor: the confident choice, or every confident yes."""
    if candidate.multi:
        return [name for name in candidate.options
                if name != OTHER and (answers.get(f"{candidate.facet}.{name}") or {}).get("noul", 0) >= floor]
    a = answers.get(candidate.facet) or {}
    choice = a.get("choice")
    if choice in candidate.options and choice != OTHER and (a.get("probabilities") or {}).get(choice, 0) >= floor:
        return [choice]
    return []
