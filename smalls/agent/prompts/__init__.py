"""Reader for the four prompt files beside this module. Their text is written blind
(docs/decisions/tools-agent-build.md section 3): the session that builds the agent never writes it, and
until the blind pass lands each file holds placeholder text. A live run refuses to start while any
placeholder remains, here or in the tool descriptions.
"""

from __future__ import annotations

import string
from functools import cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = ("investigator", "first_turn", "verifier_claims", "verifier_contrarian")
# The first-turn template is filled by code; these are the slots it must keep, exactly as written.
SLOTS = ("now", "inventory", "trigger", "budget", "prior_board")
PLACEHOLDER = "PLACEHOLDER"


@cache
def load(name: str) -> str:
    if name not in NAMES:
        raise KeyError(f"no prompt named {name}")
    return (HERE / f"{name}.md").read_text(encoding="utf-8")


def slots_of(text: str) -> list[str]:
    return [field for _, field, _, _ in string.Formatter().parse(text) if field is not None]


def first_turn(**values: str) -> str:
    """The first user turn: the template with its five slots filled."""
    return load("first_turn").format(**{slot: values.get(slot, "") for slot in SLOTS})


def placeholders() -> list[str]:
    """The prompt files that still hold placeholder text."""
    return [f"{name}.md" for name in NAMES if PLACEHOLDER in load(name)]


def slot_problems() -> list[str]:
    """The slot check: the first-turn template names each of its five slots once, and no other. The other
    three prompts are sent as written and are never filled in."""
    out = []
    try:
        found = slots_of(load("first_turn"))
    except ValueError as error:      # a stray brace
        return [f"first_turn.md: not a valid template ({error})"]
    out += [f"first_turn.md: slot {{{s}}} appears {found.count(s)} times, not once" for s in SLOTS if found.count(s) != 1]
    out += [f"first_turn.md: unknown slot {{{s}}}" for s in sorted(set(found) - set(SLOTS))]
    return out
