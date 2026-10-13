"""Tools for the spike.

`search_logs` and `get_record` are stubs with the signatures and the result envelope of PLAN section 4
(ruling A1) over a tiny synthetic fixture, because smalls/tools has no tools on this branch yet. They follow
the error contract (A2): errors come back as text the model can act on, never as exceptions.

`now` is injected through the run context and is never a tool parameter (PLAN 3.4).

Each tool has a kind that the gate policy reads: `read` (search, retrieval), `action` (mock remediation;
touches nothing real), `internal` (writes the agent's own state).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from smalls.contracts.handles import is_handle, log_handle, query_handle

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
Kind = Literal["read", "action", "internal"]


@dataclass
class RunContext:
    """Static per-run values the harness injects. Not a tool parameter, not checkpointed by LangGraph."""

    run_id: str
    now: datetime
    auto_approve: bool
    gate_actions_always: bool
    tool_call_budget: int
    max_rounds: int = 0          # model turns; 0 means budget + 6. Internal calls are free, so this bounds them.

    def __post_init__(self) -> None:
        if not self.max_rounds:
            self.max_rounds = self.tool_call_budget + 6


@dataclass
class Envelope:
    text: str
    handles: list[str] = field(default_factory=list)
    native_query: str | None = None
    window: list[str] | None = None
    status: Literal["ok", "error"] = "ok"


# ---------------------------------------------------------------- fixture store (stands in for the SQLite store)

@dataclass
class LogRow:
    handle: str
    t: datetime
    host: str
    level: str
    raw: str


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_t(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _load_logs() -> list[LogRow]:
    rows: list[LogRow] = []
    for p in sorted((FIXTURES / "logs").glob("*.log")):
        rel = f"logs/{p.name}"
        seen: dict[str, int] = {}
        for raw in p.read_text().splitlines():
            if not raw.strip():
                continue
            seen[raw] = seen.get(raw, 0) + 1
            ts, level = raw.split()[:2]
            rows.append(LogRow(log_handle(rel, raw, seen[raw]), _parse_t(ts), p.stem, level, raw))
    rows.sort(key=lambda r: r.t)
    return rows


LOGS = _load_logs()
RECORDS: dict[str, dict[str, Any]] = json.loads((FIXTURES / "records.json").read_text())


def _resolve(spec: str | None, now: datetime, default: datetime) -> datetime:
    """ISO time, or a relative window like -30m / -2h resolved against now."""
    if not spec:
        return default
    m = re.fullmatch(r"-(\d+)([mh])", spec.strip())
    if m:
        n, unit = int(m[1]), m[2]
        return now - (timedelta(minutes=n) if unit == "m" else timedelta(hours=n))
    return _parse_t(spec)


# ---------------------------------------------------------------- tool argument schemas (what the model fills in)

class SearchLogsArgs(BaseModel):
    """Search raw logs by substring terms (AND), with optional host and time filters. Rows mode by default;
    group_by gives counts instead of rows."""

    terms: list[str] = Field(default_factory=list, description="Substrings that must all appear, case-insensitive.")
    sourcetype: str | None = Field(None, description="Log sourcetype, e.g. 'app'.")
    host: str | None = Field(None, description="Host name from the source inventory.")
    since: str | None = Field(None, description="ISO-8601 UTC time or relative like '-30m'. Default: 2 h before now.")
    until: str | None = Field(None, description="ISO-8601 UTC time or relative. Clamped to now.")
    group_by: Literal["minute", "host", "level"] | None = Field(None, description="Return counts instead of rows.")
    limit: int = Field(30, ge=1, le=50)


class GetRecordArgs(BaseModel):
    """Fetch one record by handle, as it stood at now, in its native vendor shape."""

    handle: str = Field(description="A handle printed by another tool, e.g. 'jk:job#12' or 'conf:PAGE-KEY'.")


class ProposeRollbackArgs(BaseModel):
    """Propose rolling a service back to an earlier version. MOCK: nothing is executed; the proposal is
    recorded for the incident commander."""

    service: str
    to_version: str
    reason: str = Field(description="The evidence-based reason, citing handles.")


class HypothesisUpdate(BaseModel):
    id: str = Field(description="Stable short id, e.g. 'h-disk'. Reuse it to update.")
    statement: str
    status: Literal["leading", "open", "rejected"]
    confidence: float = Field(ge=0, le=1)
    support: list[str] = Field(default_factory=list, description="Handles that support it.")
    against: list[str] = Field(default_factory=list, description="Handles that contradict it.")
    because: list[str] = Field(default_factory=list, description="The handles that moved it this time.")


class UpdateHypothesesArgs(BaseModel):
    """Record what you now believe. Call it when evidence changes a hypothesis; it is your working memory."""

    updates: list[HypothesisUpdate]


class ReportHypothesis(BaseModel):
    statement: str
    confidence: float = Field(ge=0, le=1)
    support: list[str]
    against: list[str] = Field(default_factory=list)


class SubmitReportArgs(BaseModel):
    """Submit the final report. Every handle you cite must have been returned by a tool in this run."""

    summary: str
    hypotheses: list[ReportHypothesis]
    actions: list[str]
    open_questions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- implementations

def search_logs(a: SearchLogsArgs, ctx: RunContext) -> Envelope:
    now = ctx.now
    since = _resolve(a.since, now, now - timedelta(hours=2))
    until_req = _resolve(a.until, now, now)
    until = min(until_req, now)
    clamped = until_req > now
    rows = [r for r in LOGS if r.t < now and since <= r.t < until
            and (a.host is None or r.host == a.host)
            and all(t.lower() in r.raw.lower() for t in a.terms)]
    spl = (f'search index=app {" ".join(json.dumps(t) for t in a.terms)}'
           + (f" host={a.host}" if a.host else "") + f' earliest="{_iso(since)}" latest="{_iso(until)}"')
    window = [_iso(since), _iso(until)]
    note = f" (until clamped to now {_iso(now)})" if clamped else ""
    if not rows:
        return Envelope(f"0 rows for terms={a.terms} host={a.host} window {window[0]}..{window[1]}{note}. "
                        "Widen the window, drop a term, or remove host=.", [], spl, window)
    if a.group_by:
        key = {"minute": lambda r: r.t.strftime("%H:%M"), "host": lambda r: r.host, "level": lambda r: r.level}[a.group_by]
        counts: dict[str, int] = {}
        for r in rows:
            counts[key(r)] = counts.get(key(r), 0) + 1
        h = query_handle(f"search_logs|{a.model_dump_json()}", _iso(now))
        body = "\n".join(f"{k}\t{v}" for k, v in sorted(counts.items()))
        return Envelope(f"{h} counts by {a.group_by}, {len(rows)} rows{note}\n{body}\n[SPL] {spl} | stats count by {a.group_by}",
                        [h], spl, window)
    shown = rows[: a.limit]
    lines = [f"{r.handle}  {_iso(r.t)}  {r.host}  {r.raw[:300]}" for r in shown]
    footer = f"[showing {len(shown)} of {len(rows)}; narrow with terms=, host= or a shorter window]" if len(rows) > len(shown) else f"[{len(rows)} rows]"
    return Envelope("\n".join(lines + [footer + note, f"[SPL] {spl}"]), [r.handle for r in shown], spl, window)


def get_record(a: GetRecordArgs, ctx: RunContext) -> Envelope:
    h = a.handle.strip()
    rec = RECORDS.get(h)
    log = next((r for r in LOGS if r.handle == h), None)
    # A handle that is not yet visible gets the same answer as an unknown one (A2).
    if log is not None and log.t < ctx.now:
        return Envelope(f"{h}  {_iso(log.t)}  {log.host}\n{log.raw}", [h], None, None)
    if rec is None or _parse_t(rec["t_known"]) >= ctx.now:
        hint = "" if is_handle(h) else " It does not match the handle grammar; copy handles exactly as tools print them."
        return Envelope(f"{h}: not in this bundle.{hint}", [], None, None, status="error")
    native = {k: v for k, v in rec["native"].items() if v not in ("", None)}
    return Envelope(f"{h} (as of {_iso(ctx.now)})\n" + json.dumps(native, indent=1)[:2000], [h], None, None)


def propose_rollback(a: ProposeRollbackArgs, ctx: RunContext) -> Envelope:
    # The proposal is durable because the tools node writes it to the ledger in state, not here.
    return Envelope(f"MOCK: rollback of {a.service} to {a.to_version} recorded as a proposed action for the "
                    "incident commander. Nothing was executed.", [], None, None)


def _internal(a: BaseModel, ctx: RunContext) -> Envelope:  # handled by the tools node, which owns state
    raise NotImplementedError


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: Kind
    args: type[BaseModel]
    fn: Callable[[Any, RunContext], Envelope]

    def model_schema(self, with_rationale: bool = True) -> dict[str, Any]:
        """The tool definition the model sees: the tool's own arguments plus, for gated tools, a rationale
        that the gate shows the approver and the trace keeps. The rationale is stripped before execution."""
        params = self.args.model_json_schema()
        if with_rationale and self.kind != "internal":
            params = dict(params)
            params["properties"] = {"rationale": {"type": "string", "description":
                                    "One sentence: why this call, and what result you expect."}, **params["properties"]}
            params["required"] = ["rationale", *params.get("required", [])]
        return {"type": "function", "function": {"name": self.name, "description": (self.args.__doc__ or "").strip(),
                                                  "parameters": params}}


TOOLS: dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("search_logs", "read", SearchLogsArgs, search_logs),
    ToolSpec("get_record", "read", GetRecordArgs, get_record),
    ToolSpec("propose_rollback", "action", ProposeRollbackArgs, propose_rollback),
    ToolSpec("update_hypotheses", "internal", UpdateHypothesesArgs, _internal),
    ToolSpec("submit_report", "internal", SubmitReportArgs, _internal),
]}


INLINE_HANDLE = re.compile(r"\b(?:log|jk|conf|q|snow|dt|pd|dd|metric|sw|psp|spl|k8s|slack):[^\s,;()\[\]]+")


def inline_handles(*texts: str) -> set[str]:
    """Handles cited in prose (summary, actions, questions), so the validator checks them too."""
    return {h.rstrip(".") for t in texts for h in INLINE_HANDLE.findall(t or "") if is_handle(h.rstrip("."))}


def parse_args(spec: ToolSpec, raw: dict[str, Any]) -> tuple[BaseModel | None, str | None, str]:
    """Validate model-supplied arguments. Returns (args, rationale, error text)."""
    raw = dict(raw or {})
    rationale = raw.pop("rationale", None)
    try:
        return spec.args.model_validate(raw), rationale, ""
    except ValidationError as e:
        errs = "; ".join(f"{'.'.join(str(x) for x in er['loc'])}: {er['msg']}" for er in e.errors())
        return None, rationale, f"Invalid arguments for {spec.name}: {errs}. Fix them and call again."


def inventory(now: datetime) -> str:
    """Source inventory built from visible records only (ruling R1), injected in the first user turn."""
    hosts = sorted({r.host for r in LOGS if r.t < now})
    first = min(r.t for r in LOGS if r.t < now)
    recs = [h for h, r in RECORDS.items() if _parse_t(r["t_known"]) < now]
    return (f"Logs: sourcetype app; hosts {', '.join(hosts)}; coverage {_iso(first)} to now.\n"
            f"Records you can fetch with get_record: {', '.join(sorted(recs))}.")
