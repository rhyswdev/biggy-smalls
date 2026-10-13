"""Lint invariant 16: no bundle string in prompt text on the agent side. Prompt text is anything a model
is told besides the evidence itself. Today that is the tag profiles (instructions, option descriptions and
the words a request uses for a record's kind); the agent's prompts join the list when they exist.

The bundle strings are built from `scenario/facts.yaml`: the company, people, groups, entities with every
alias, hosts and addresses, and the named or versioned parameters, plus the strings the repo rule names
outright. The lint lives on the generator side because only the generator may read facts.yaml.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from smalls_data.gen.core import SCENARIO

# Named in the repo rule (CLAUDE.md). Matched as plain substrings, so `Hikari` also catches `HikariPool-1`.
SEEDS = ("PayCore", "Hikari", "pay-db", "3.8.0", "Larkspur")
_NAMED = re.compile(r"^[\w.:/@-]+( \d+)?$")


def _leaves(node):
    if isinstance(node, dict):
        for v in node.values():
            yield from _leaves(v)
    elif isinstance(node, list):
        for v in node:
            yield from _leaves(v)
    elif isinstance(node, str):
        yield node


def bundle_strings(facts: dict | None = None) -> list[str]:
    facts = facts or yaml.safe_load((SCENARIO / "facts.yaml").read_text())
    out = {facts["company"], facts["bundle"]}
    for person in facts.get("people", []):
        out |= {person["name"], person["handle"], person.get("display_name") or "", *person["name"].split()}
    for user in facts.get("integration_users", []):
        out |= {user["user_name"], user["name"]}
    out |= {g["name"] for g in facts.get("groups", [])}
    for key, entity in facts.get("entities", {}).items():
        out |= {key, *_leaves(entity.get("aliases", {}))}
    network = facts.get("network", {})
    out |= {k for k in network if re.search(r"[\d-]", k)} | set(_leaves(network))
    # Parameters: only named or versioned things (a pool name, a role, a version), not settings words.
    out |= {s for s in _leaves(facts.get("parameters", {})) if _NAMED.match(s) and re.search(r"[\d_.-]", s)}
    return sorted(s for s in out if len(s) >= 3)


AGENT_SIDE = ("smalls/agent", "smalls/tools")
TEXT_FILES = (".md", ".yaml", ".yml", ".toml", ".json")


def string_literals(source: str) -> str:
    """Every string a Python file holds, docstrings and f-string parts included, joined by newlines.
    Comments are not strings and are not read."""
    import ast

    return "\n".join(node.value for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Constant) and isinstance(node.value, str))


def agent_side_texts(root: Path | None = None) -> list[tuple[str, str]]:
    """Every string under the agent and the tool layer: the prompt files, the tool descriptions, the
    config, and the string literals of the code, because a tool's row layout, footer or error sentence
    reaches the model as surely as a prompt does."""
    root = root or Path(__file__).resolve().parent.parent
    out = []
    for folder in AGENT_SIDE:
        for path in sorted((root / folder).rglob("*")):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            where = path.relative_to(root).as_posix()
            if path.suffix == ".py":
                out.append((where, string_literals(path.read_text(encoding="utf-8"))))
            elif path.suffix in TEXT_FILES:
                out.append((where, path.read_text(encoding="utf-8")))
    return out


def prompt_texts() -> list[tuple[str, str]]:
    """Every prompt text on the agent side, as (where, text): what a tag request carries, then every
    string under `smalls/agent/` and `smalls/tools/`, which takes in the four prompt files and
    `descriptions.yaml`."""
    from smalls.tools.ingest.tagging import profiles

    return [*profiles.prompt_texts(), *agent_side_texts()]


def find(texts: list[tuple[str, str]], strings: list[str] | None = None) -> list[str]:
    """Every bundle string a prompt text contains: whole strings, any case; the seeds as substrings."""
    strings = bundle_strings() if strings is None else strings
    patterns = [(s, re.compile(r"(?<![A-Za-z0-9])" + re.escape(s) + r"(?![A-Za-z0-9])", re.I)) for s in strings]
    patterns += [(s, re.compile(re.escape(s), re.I)) for s in SEEDS]
    hits = []
    for where, text in texts:
        found = sorted({s for s, pattern in patterns if pattern.search(text)})
        hits += [f"{where} contains the bundle string {s!r}" for s in found]
    return hits
