"""The verifier: its verdict, and the packets it reads (docs/research/agent-design.md axis 5; J13 in
docs/decisions/tools-agent-build.md).

The verifier never sees the investigator's conversation and has no read tools of its own: it gets evidence
packets, which is one model call and decorrelates its errors from the investigator's. Two passes share one
verdict shape:

- claim support: for each claim, do the rows it cites support it? `supported`, `partial`, `unsupported` or
  `contradicted`, each with a quote from a row;
- the contrarian case: the strongest alternative the evidence fits, up to three checks that would tell
  them apart, as concrete tool calls, and any evidence the report passed over.

The verifier answers through `submit_verdict`. Forced tool choice is not relied on, so it is the model's
own choice, and a verdict that does not parse gets one retry.

Claim ids: `h:<id>` a hypothesis, `t:<n>` a timeline row, `a:<rank>` an action, `d:<n>` a do-not row. "In
the leading hypothesis" means `h:<leading>` and every `a:` row.

Packets are built from the run record's evidence file: what the model actually saw, at the time it saw
it. The digest is the rows that were shown but never cited, newest first, so the contrarian pass can see
what the report ignored.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from smalls.agent import validator
from smalls.contracts.envelope import Envelope
from smalls.contracts.report import Strict
from smalls.tools.registry import ToolContext, ToolSpec
from smalls.tools.schemas import Pair

SUBMIT_VERDICT = "submit_verdict"
ROW_CHARS = 1500            # one cited row in a packet; a longer one is cut and says so
DIGEST_ROW_CHARS = 200      # one uncited row in the digest
CUT = " […cut for the packet]"
NOT_SHOWN = "(no tool result in this run showed this handle)"


class ClaimVerdict(Strict):
    id: str
    verdict: Literal["supported", "partial", "unsupported", "contradicted"]
    quote: str
    handle: str | None = None


class Check(Strict):
    tool: str
    args: list[Pair] = Field(default_factory=list)


class Alternative(Strict):
    statement: str
    fit: Literal["equal", "weaker"]
    check: Check | None = None


class SubmitVerdictArgs(Strict):
    claims: list[ClaimVerdict] = Field(default_factory=list)
    alternatives: list[Alternative] = Field(default_factory=list, max_length=3)
    ignored: list[str] = Field(default_factory=list)
    overall: Literal["pass", "revise"]


def _in_the_verify_node(args: BaseModel, ctx: ToolContext) -> Envelope:
    raise NotImplementedError("the verdict is handled by the verify node")


def spec() -> ToolSpec:
    """The one tool a verifier pass is given."""
    return ToolSpec(SUBMIT_VERDICT, "internal", SubmitVerdictArgs, _in_the_verify_node, cap_tokens=500)


# -- packets -----------------------------------------------------------------------------------------------


def evidence_by_handle(evidence: list[dict[str, Any]]) -> dict[str, str]:
    """Each handle's row as the model last saw it."""
    return {row["handle"]: row["text"] for row in evidence}


def _row(handle: str, shown: dict[str, str], limit: int = ROW_CHARS) -> str:
    text = shown.get(handle)
    if text is None:
        return f"    {handle}  {NOT_SHOWN}"
    text = text if len(text) <= limit else text[:limit] + CUT
    return "\n".join("    " + line for line in text.split("\n"))


def leading_claims(report: dict[str, Any]) -> list[str]:
    """The claim ids that are "in the leading hypothesis": the hypothesis itself and every action."""
    lead = next((h for h in report.get("hypotheses", []) if h["status"] == "leading"), None)
    return ([f"h:{lead['id']}"] if lead else []) + [f"a:{a['rank']}" for a in report.get("actions", [])]


def packets(report: dict[str, Any], evidence: list[dict[str, Any]], only: set[str] | None = None) -> str:
    """One packet per claim: the claim in the report's words, then the rows of every handle it cites.
    `only` limits it to some claim ids, for the re-check of what a revision changed."""
    shown = evidence_by_handle(evidence)
    out: list[str] = []

    def add(claim: str, words: str, groups: list[tuple[str, list[str]]]) -> None:
        if only is not None and claim not in only:
            return
        lines = [f"Claim {claim}: {words}"]
        for label, handles in groups:
            lines.append(f"  {label}:" if handles else f"  {label}: (none)")
            lines += [_row(h, shown) for h in handles]
        out.append("\n".join(lines))

    for h in report.get("hypotheses", []):
        add(f"h:{h['id']}", f"[{h['status']} {h['role']}, confidence {h['confidence']:.2f}, blames {h['subject']}] {h['statement']}",
            [("support", h.get("support") or []), ("against", h.get("against") or [])])
    for n, row in enumerate(report.get("timeline", []), 1):
        add(f"t:{n}", f"at {row['t_utc']}: {row['event']}", [("evidence", row.get("evidence") or [])])
    for a in report.get("actions", []):
        add(f"a:{a['rank']}", f"do: {a['action']} (owner: {a['owner']})", [("evidence", a.get("evidence") or [])])
    for n, row in enumerate(report.get("do_not", []), 1):
        add(f"d:{n}", f"do not: {row['action']}, because {row['why']}", [("evidence", row.get("evidence") or [])])
    return "\n\n".join(out)


def digest(report: dict[str, Any], evidence: list[dict[str, Any]], tokens: int) -> str:
    """The rows that were shown but not cited, newest first, 200 characters each, inside a token budget.
    Without it the contrarian pass could not name what the report ignored."""
    cited = validator.report_handles(report)
    seen: set[str] = set()
    lines: list[str] = []
    used = 0
    for row in sorted(evidence, key=lambda r: r.get("t_known") or "", reverse=True):
        if row["handle"] in cited or row["handle"] in seen:
            continue
        seen.add(row["handle"])
        text = row["text"].split("\n", 1)[0]
        line = text if len(text) <= DIGEST_ROW_CHARS else text[:DIGEST_ROW_CHARS] + " […]"
        if (used + len(line)) // 4 > tokens:
            lines.append(f"[{len({r['handle'] for r in evidence} - cited - seen) + 1} more uncited rows are not listed]")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) or "(every row that was shown is cited)"
