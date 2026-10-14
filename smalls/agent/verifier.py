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

Whether a report goes back is decided by code, from the two verdicts, and never by the `overall` a pass
wrote. It goes back, once, when a claim in the leading hypothesis is unsupported or contradicted, or when
an alternative rated an equal fit names a check that is a valid read and is not already in the ledger. A
contrarian always proposes something, so a proposed check alone sends nothing back. After the revision,
only the claims that were added or changed are checked again, and what is still not supported is shown as
disputed.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from smalls.agent import validator
from smalls.contracts.envelope import Envelope
from smalls.contracts.report import Strict
from smalls.tools import windows
from smalls.tools.registry import ToolContext, ToolSpec
from smalls.tools.schemas import Pair

SUBMIT_VERDICT = "submit_verdict"
CLAIMS, CONTRARIAN, RECHECK = "claims", "contrarian", "recheck"
NOT_SUPPORTED = ("unsupported", "contradicted")
DISPUTED = ("partial", "unsupported", "contradicted")
LEADING = "Leading hypothesis: {id}. Claims in the leading hypothesis: {claims}."
NO_LEADING = "Leading hypothesis: none. No hypothesis is marked leading."
REPORT_HEAD, PACKETS_HEAD, DIGEST_HEAD = "Report:", "Packets:", "Digest of rows shown but not cited, newest first:"
SENT_BACK = "The report is not final: the verifier sends it back once. {points} You have {calls} more tool calls and {turns} more turns. Settle these points, update the board, and submit the report again."
POINT_CLAIM = "Claim {id} is {verdict}{said}."
POINT_ALTERNATIVE = "An alternative fits the evidence as well as the leading hypothesis: {statement} The check that would tell them apart has not been run: {call}."
POINT_IGNORED = "Rows that count against the leading hypothesis and are not in the report: {handles}."
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



# -- what a pass is given, and what it answers -----------------------------------------------------------------


def user_turn(report: dict[str, Any], evidence: list[dict[str, Any]], which: str, *, digest_tokens: int = 1500, only: set[str] | None = None) -> str:
    """The one user turn of a pass. Claim support gets the packets and which hypothesis leads. The
    contrarian pass also gets the report and the digest of what was shown and not cited."""
    claims = leading_claims(report)
    lead = LEADING.format(id=claims[0], claims=", ".join(claims)) if claims and claims[0].startswith("h:") else NO_LEADING
    if which != CONTRARIAN:
        return f"{lead}\n\n{packets(report, evidence, only)}"
    shown = {key: report.get(key) for key in ("as_of", "summary", "hypotheses", "timeline", "actions", "do_not", "open_questions")}
    return "\n\n".join([f"{REPORT_HEAD}\n{json.dumps(shown, indent=1, ensure_ascii=False)}", lead, f"{PACKETS_HEAD}\n{packets(report, evidence)}",
                         f"{DIGEST_HEAD}\n{digest(report, evidence, digest_tokens)}"])


def ask(bound: Any, system: str, turn: str) -> tuple[SubmitVerdictArgs | None, list[dict[str, Any]]]:
    """One pass: the verdict, or None when two tries gave nothing that parses, and a usage row per try."""
    rows = []
    for attempt in (1, 2):
        started = time.perf_counter()
        ai = bound.invoke([SystemMessage(system), HumanMessage(turn)])
        rows.append({"attempt": attempt, "latency_ms": round((time.perf_counter() - started) * 1000, 1), "usage": ai.usage_metadata or {},
                     "stop_reason": ai.response_metadata.get("stop_reason"), "model": ai.response_metadata.get("model_name")})
        for call in ai.tool_calls:
            if call["name"] != SUBMIT_VERDICT:
                continue
            try:
                return SubmitVerdictArgs.model_validate({k: v for k, v in call["args"].items() if v is not None}), rows
            except Exception:
                break
    return None, rows


# -- the decision, by code -------------------------------------------------------------------------------------


def identity(spec: ToolSpec, args: dict[str, Any], now: datetime) -> str | None:
    """What makes two calls the same call: the tool and its validated arguments, with defaults filled and
    relative times resolved (J4). None when the arguments do not validate."""
    try:
        data = spec.args.model_validate(args).model_dump()
        if data.get("since"):
            window = windows.resolve(data["since"], data.get("until"), now)
            data["since"], data["until"] = window.iso
    except Exception:
        return None
    return json.dumps({"tool": spec.name, **data}, sort_keys=True, default=str)


def check_args(spec: ToolSpec, pairs: list[Pair]) -> dict[str, Any]:
    """A check's name and value pairs as a tool's arguments: a text input as written, anything else as JSON."""
    out: dict[str, Any] = {}
    for pair in pairs:
        field = spec.args.model_fields.get(pair.name)
        if field is not None and field.annotation in (str, str | None):
            out[pair.name] = pair.value
            continue
        try:
            out[pair.name] = json.loads(pair.value)
        except ValueError:
            out[pair.name] = pair.value
    return out


def decide(report: dict[str, Any], claims: SubmitVerdictArgs | None, contrarian: SubmitVerdictArgs | None, *, read_tools: dict[str, ToolSpec],
           ledger: list[dict[str, Any]], shown: set[str], now: datetime) -> dict[str, Any]:
    """Whether the report goes back, and exactly why. A check counts only when it is a read this run has,
    its arguments validate, and no call that ran already made it."""
    lead = set(leading_claims(report))
    unsupported = [c.model_dump() for c in (claims.claims if claims else []) if c.id in lead and c.verdict in NOT_SUPPORTED]
    ran = {e.get("identity") for e in ledger if e.get("identity") and e.get("status") == "ok"}
    new, discarded = [], []
    for alt in (contrarian.alternatives if contrarian else []):
        if alt.fit != "equal" or alt.check is None:
            continue
        spec = read_tools.get(alt.check.tool)
        key = identity(spec, check_args(spec, alt.check.args), now) if spec is not None and spec.kind == "read" else None
        row = {"statement": alt.statement, "tool": alt.check.tool, "args": {p.name: p.value for p in alt.check.args}}
        if key is None:
            discarded.append({**row, "why": "not a valid read call"})
        elif key in ran:
            discarded.append({**row, "why": "already in the ledger"})
        else:
            new.append(row)
    ignored = [h for h in (contrarian.ignored if contrarian else []) if h in shown]
    return {"revise": bool(unsupported or new), "unsupported": unsupported, "checks": new, "discarded_checks": discarded, "ignored": ignored}


def sent_back(decision: dict[str, Any], *, calls: int, turns: int) -> str:
    """The revision request, as the result of the `submit_report` call it answers."""
    points = []
    for c in decision["unsupported"]:
        said = f'; the closest the cited rows say is "{c["quote"]}"' + (f" ({c['handle']})" if c.get("handle") else "") if c.get("quote") else ""
        points.append(POINT_CLAIM.format(id=c["id"], verdict=c["verdict"], said=said))
    for check in decision["checks"]:
        call = " ".join([check["tool"], *(f"{name}={value}" for name, value in check["args"].items())])
        points.append(POINT_ALTERNATIVE.format(statement=check["statement"].rstrip(".") + ".", call=call))
    if decision["ignored"]:
        points.append(POINT_IGNORED.format(handles=", ".join(decision["ignored"])))
    return SENT_BACK.format(points=" ".join(points), calls=calls, turns=turns)


def claim_texts(report: dict[str, Any]) -> dict[str, str]:
    """Each claim of a report as its packet, without the rows: what the claim says and what it cites."""
    return {block.split(":", 2)[0].removeprefix("Claim ") + ":" + block.split(":", 2)[1]: block for block in packets(report, []).split("\n\n") if block}


def changed_claims(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    """What a revision did: the claim ids it added, changed and removed."""
    old, new = claim_texts(before), claim_texts(after)
    return {"added": sorted(set(new) - set(old)), "changed": sorted(k for k in new if k in old and new[k] != old[k]), "removed": sorted(set(old) - set(new))}


def disputed(report: dict[str, Any], *verdicts: SubmitVerdictArgs | dict[str, Any] | None) -> list[str]:
    """The claims of the final report whose latest verdict is short of supported. A later verdict on a
    claim replaces an earlier one."""
    latest: dict[str, str] = {}
    for verdict in verdicts:
        rows = verdict.claims if isinstance(verdict, SubmitVerdictArgs) else (verdict or {}).get("claims") or []
        for row in rows:
            row = row.model_dump() if isinstance(row, BaseModel) else row
            latest[row["id"]] = row["verdict"]
    return sorted(claim for claim in claim_texts(report) if latest.get(claim) in DISPUTED)

