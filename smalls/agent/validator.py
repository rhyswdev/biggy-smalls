"""Citation and structure checks for reports and board writes (docs/research/agent-design.md axis 5, the code
checks; J7, J13 and J14 in docs/decisions/tools-agent-build.md).

The rule the whole design rests on: a handle may be cited only if a tool result showed its content in this
run. A handle that was only listed as a child or a pointer becomes citable once a later call shows it. An
incident commander's note minted in this run is citable too.

One retry per submission cycle covers every finding. After it, a bad handle is dropped and recorded, and
a claim left with no handle is kept and flagged, so a degraded report says that it is one.

Claim ids: `h:<id>` a hypothesis, `t:<n>` a timeline row, `a:<rank>` an action, `d:<n>` a do-not row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from smalls.contracts.handles import is_handle

_WRAP = "`'\"()[]<>*.,;!?"
DROPPED = "[citation dropped]"


def inline_handles(*texts: str | None) -> set[str]:
    """Handles cited in prose, so the validator checks them like the listed ones."""
    found: set[str] = set()
    for text in texts:
        for token in (text or "").split():
            for part in _candidates(token):
                if is_handle(part):
                    found.add(part)
    return found


def _candidates(token: str) -> Iterable[str]:
    whole = token.strip(_WRAP).rstrip(":")
    if ":" not in whole:
        return ()
    if is_handle(whole):
        return (whole,)
    return tuple(part.strip(_WRAP).rstrip(":") for part in whole.split(","))


@dataclass
class Seen:
    """What this run may cite, read from the ledger and the notes."""

    shown: set[str] = field(default_factory=set)
    referenced: set[str] = field(default_factory=set)

    @classmethod
    def of(cls, state: dict[str, Any]) -> "Seen":
        seen = cls()
        for entry in state.get("ledger", []):
            seen.add(entry)
        seen.shown |= {note["handle"] for note in state.get("ic_notes", [])}
        return seen

    def add(self, entry: dict[str, Any]) -> None:
        if entry.get("status") in ("ok", "repeat"):
            self.shown |= set(entry.get("shown") or [])
            self.referenced |= set(entry.get("referenced") or [])

    def split(self, cited: Iterable[str]) -> tuple[list[str], list[str]]:
        """(never seen, only listed) among the handles cited."""
        cited = set(cited)
        only_listed = sorted(h for h in cited if h in self.referenced and h not in self.shown)
        unknown = sorted(h for h in cited if h not in self.shown and h not in self.referenced)
        return unknown, only_listed


def explain(unknown: list[str], only_listed: list[str]) -> str:
    """The two fixed sentences a citation finding is reported with."""
    parts = []
    if unknown:
        parts.append(f"No tool result in this run showed these handles: {', '.join(unknown)}. Cite only a handle a tool "
                     "result showed, exactly as printed, or drop the claim.")
    if only_listed:
        parts.append(f"These handles were only listed, never shown: {', '.join(only_listed)}. Fetch each with get_record "
                     "before citing it.")
    return " ".join(parts)


# -- board writes ------------------------------------------------------------------------------------------


def board_handles(args: Any) -> set[str]:
    """Every handle a board update cites."""
    cited: set[str] = set()
    for h in args.hypotheses or []:
        for name in ("support", "against", "changes", "because", "discriminating"):
            cited |= set(getattr(h, name, None) or [])
    for row in args.timeline or []:
        cited |= set(row.evidence)
    return cited


# -- reports -----------------------------------------------------------------------------------------------


def report_claims(report: dict[str, Any]) -> dict[str, list[str]]:
    """Every claim in a report with the handles listed for it, by claim id."""
    claims: dict[str, list[str]] = {}
    for h in report.get("hypotheses", []):
        claims[f"h:{h['id']}"] = [*(h.get("support") or []), *(h.get("against") or [])]
    for n, row in enumerate(report.get("timeline", []), 1):
        claims[f"t:{n}"] = list(row.get("evidence") or [])
    for a in report.get("actions", []):
        claims[f"a:{a['rank']}"] = list(a.get("evidence") or [])
    for n, row in enumerate(report.get("do_not", []), 1):
        claims[f"d:{n}"] = list(row.get("evidence") or [])
    return claims


def report_handles(report: dict[str, Any]) -> set[str]:
    """Every handle a report cites, listed or inline."""
    cited = {h for handles in report_claims(report).values() for h in handles}
    cited |= {a["handle"] for a in report.get("alerts", [])}
    for h in report.get("hypotheses", []):
        cited |= set(h.get("changes") or []) | set(h.get("discriminating") or []) | inline_handles(h.get("statement"))
    cited |= inline_handles(report.get("summary"), *report.get("open_questions", []),
                            *(row.get("event") for row in report.get("timeline", [])),
                            *(a.get("action") for a in report.get("actions", [])),
                            *(text for row in report.get("do_not", []) for text in (row.get("action"), row.get("why"))),
                            *(report.get("stakeholder_update") or {}).values())
    impact = report.get("impact") or {}
    cited |= inline_handles(*(v for v in impact.values() if isinstance(v, str)))
    return cited


def _scrub(value: Any, bad: set[str]) -> Any:
    """A copy with the bad handles gone: removed from a list of handles, marked in prose."""
    if isinstance(value, dict):
        return {k: _scrub(v, bad) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, bad) for v in value if not (isinstance(v, str) and v in bad)]
    if isinstance(value, str):
        for handle in sorted(bad, key=len, reverse=True):
            value = value.replace(handle, DROPPED)
        return value
    return value


def drop(report: dict[str, Any], bad: Iterable[str]) -> tuple[dict[str, Any], list[str]]:
    """The report without the handles that cannot be grounded, and the ids of the claims that are left
    with no handle at all. A verdict on an alert no tool showed goes with its handle."""
    bad = set(bad)
    before = report_claims(report)
    cleaned = {k: ([a for a in v if a["handle"] not in bad] if k == "alerts" else _scrub(v, bad)) for k, v in report.items()}
    after = report_claims(cleaned)
    flagged = sorted(cid for cid, handles in after.items() if not handles and before.get(cid))
    return cleaned, flagged
