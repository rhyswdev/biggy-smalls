"""Carry-forward between checkpoints, and the board rendered as text (docs/research/agent-design.md axis 2;
J8, J9 and J16 in docs/decisions/tools-agent-build.md).

A later look at the same incident starts from the earlier look's board. Nothing is taken on trust: every
handle the earlier board cites is served again at this run's `now`, by the same tools, and enters the
ledger as `carry_forward`. A record is fetched again by its handle. A count or a windowed metric is minted
per call, so its earlier call is run again over the window it resolved to then, and the board cites the
handle the new call mints. A handle that no longer resolves, and an earlier note, are dropped from the
seed and listed.

The seeded hypotheses keep their ids, and what they claimed then. Whether they still hold is this run's
to decide: a carried leading hypothesis must cite a row that became known after the earlier look and that
a call of this run, not the carry-forward, showed. A count never satisfies that.

The same renderer writes the board for the first turn of a later look and for the first turn after a
restart, so it is built once and used twice.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from smalls.contracts.handles import handle_class
from smalls.tools import registry
from smalls.tools.registry import ToolContext, ToolSpec

BY = "carry_forward"
LISTS = ("support", "against", "discriminating", "changes")
NOTE = "ic:note/"
ROW_CHARS = 200
EARLIER = "The board as it stood at {as_of}, from run {run_id}. Each hypothesis is on your board under the same id and is marked re-test."
RESTARTED = ("The board of this run as it stands. The conversation was restarted from it to free context: what it holds was established in this run, "
             "its cited rows were shown in this run and can still be cited, and the opening sweep follows again.")
RESERVED = "Rows served again for this run, one line each; get_record returns a whole record:"
DROPPED = "Not carried, and removed from the entries above: {listed}."
WHY_NOTE, WHY_GONE, WHY_NO_CALL = "an earlier note is not carried", "it no longer resolves", "the call that minted it could not be run again"
CONTINUITY = ("{id} was carried from the earlier look at {as_of} and cites no row that became known after it and that a call of this run showed. "
              "Cite such a row in its support, or change which hypothesis leads")


def load(runs_dir: Path, run_id: str) -> dict[str, Any]:
    """What a later look needs of an earlier run: its board, its report's hypotheses and its calls."""
    folder = Path(runs_dir) / run_id

    def read(name: str) -> Any:
        return json.loads((folder / name).read_text(encoding="utf-8"))

    try:
        board, report = read("board.json"), read("report.json") or {}
        calls = [json.loads(line) for line in (folder / "calls.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError) as problem:
        raise SystemExit(f"cannot carry forward from {run_id}: its run record is not readable ({type(problem).__name__})") from None
    if not board.get("as_of"):
        raise SystemExit(f"cannot carry forward from {run_id}: its board has no as_of")
    return {"run_id": run_id, "as_of": board["as_of"], "board": board, "calls": calls,
            "checkpoints": {h["id"]: h.get("history") or [] for h in report.get("hypotheses") or []}}


def _entry(n: int, tool: str, args: dict[str, Any], env: Any, started: float) -> dict[str, Any]:
    return {"call_id": f"carry_{n:02d}_{tool}", "round": 0, "by": BY, "tool": tool, "kind": "read", "args": args, "rationale": None,
            "status": env.status, "shown": list(env.shown), "referenced": list(env.referenced), "t_known": {r.handle: r.t_known for r in env.rows},
            "native_query": env.native_query, "window": list(env.window) if env.window else None, "chars": len(env.text),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2), "repeat_of": None, "used_tag_input": bool(env.meta.get("tag")),
            "empty": bool(env.meta.get("empty")), "identity": None}       # no identity: asking for it again in this run is not a repeat


def seed(earlier: dict[str, Any], read_tools: dict[str, ToolSpec], tool_ctx: ToolContext,
         evidence: Callable[[str, Any], None] | None = None) -> dict[str, Any]:
    """Serve again what the earlier board cites, and build the board this run starts from."""
    board = earlier["board"]
    cited = list(dict.fromkeys([h for hyp in board.get("hypotheses") or [] for name in LISTS for h in hyp.get(name) or []]
                               + [h for row in board.get("timeline") or [] for h in row.get("evidence") or []]))
    minted = {h: call for call in earlier["calls"] if call.get("status") == "ok" and call.get("by") != BY for h in call.get("shown") or []}
    ledger, rows, replaced, dropped = [], {}, {}, {}
    for handle in cited:
        if handle.startswith(NOTE):
            dropped[handle] = WHY_NOTE
            continue
        per_call = handle_class(handle) == "aggregate" or (handle.startswith("metric:") and "@" in handle)
        if per_call:
            before = minted.get(handle)
            spec = read_tools.get(before["tool"]) if before else None
            if spec is None:
                dropped[handle] = WHY_NO_CALL
                continue
            window = before.get("window") or [None, None]
            tool, args = spec.name, {**before["args"], **({"since": window[0], "until": window[1]} if window[0] and "since" in spec.args.model_fields else {})}
        else:
            spec, tool, args = read_tools.get("get_record"), "get_record", {"handle": handle}
            if spec is None:
                dropped[handle] = WHY_GONE
                continue
        started = time.perf_counter()
        env = registry.call(spec, args, tool_ctx)
        if env.status != "ok" or not env.shown:
            dropped[handle] = WHY_NO_CALL if per_call else WHY_GONE
            continue
        entry = _entry(len(ledger) + 1, tool, args, env, started)
        ledger.append(entry)
        if evidence:
            evidence(entry["call_id"], env.rows)
        new = handle if handle in env.shown else env.shown[0]
        if new != handle:
            replaced[handle] = new
        rows[new] = env.rows[0].text.split("\n", 1)[0]

    def kept(handles: list[str] | None) -> list[str]:
        return list(dict.fromkeys(replaced.get(h, h) for h in handles or [] if h not in dropped))

    hypotheses = {}
    for hyp in board.get("hypotheses") or []:
        earlier_rows = earlier["checkpoints"].get(hyp["id"]) or [{"as_of": earlier["as_of"], "status": hyp.get("status"), "confidence": hyp.get("confidence"),
                                                                 "because": []}]
        hypotheses[hyp["id"]] = {**{k: v for k, v in hyp.items() if k not in ("history", *LISTS)}, **{name: kept(hyp.get(name)) for name in LISTS},
                                 "history": [{"round": 0, "call_id": BY, "from": None, "to": {"status": hyp.get("status"), "confidence": hyp.get("confidence")},
                                              "because": []}],
                                 "checkpoints": [{**row, "because": kept(row.get("because"))} for row in earlier_rows], "carried": True, "_seed": True}
    timeline = [{**row, "evidence": kept(row.get("evidence"))} for row in board.get("timeline") or [] if kept(row.get("evidence"))]
    return {"hypotheses": hypotheses, "timeline": timeline, "open_questions": list(board.get("open_questions") or []), "ledger": ledger, "rows": rows,
            "carried": {"run_id": earlier["run_id"], "as_of": earlier["as_of"], "ids": list(hypotheses), "dropped": dropped, "replaced": replaced}}


def render(hypotheses: dict[str, dict[str, Any]], timeline: list[dict[str, Any]], open_questions: list[str], *, head: str,
           rows: dict[str, str] | None = None, dropped: dict[str, str] | None = None, retest: bool = False) -> str:
    """The board as text, for the first turn of a later look or of a restart."""
    lines = [head]
    for h in hypotheses.values():
        lines.append(f"{h['id']}  [{h.get('status')} {h.get('role')}, confidence {float(h.get('confidence') or 0):.2f}, blames {h.get('subject')}, "
                     f"source {h.get('source')}]{' re-test' if retest else ''}  {h.get('statement')}")
        lines += [f"    {name}: {' '.join(h[name])}" for name in LISTS if h.get(name)]
    if timeline:
        lines.append("timeline:")
        lines += [f"    {row.get('t_utc')}  {row.get('event')}  ({' '.join(row.get('evidence') or [])})" for row in timeline]
    if open_questions:
        lines.append("open questions:")
        lines += [f"    {q}" for q in open_questions]
    if rows:
        lines.append(RESERVED)
        lines += ["    " + (text if len(text) <= ROW_CHARS else text[:ROW_CHARS] + " […]") for text in rows.values()]
    if dropped:
        lines.append(DROPPED.format(listed="; ".join(f"{handle} ({why})" for handle, why in dropped.items())))
    return "\n".join(lines)


def carried_only(ledger: list[dict[str, Any]]) -> frozenset[str]:
    """The handles that are in this run only because they were carried: no call of this run's own has shown them."""
    own = {h for e in ledger if e.get("by") != BY and e.get("status") == "ok" for h in e.get("shown") or []}
    return frozenset(h for e in ledger if e.get("by") == BY for h in e.get("shown") or []) - own


def _ms(text: str | None) -> int | None:
    try:
        return int(datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp() * 1000) if text else None
    except ValueError:
        return None


def continuity(report: dict[str, Any], carried: dict[str, Any] | None, ledger: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """The carry-forward check (J9): (`passed`, `failed` or None when nothing was carried; the finding when it fails)."""
    if not carried:
        return None, None
    lead = next((h for h in report.get("hypotheses") or [] if h.get("status") == "leading"), None)
    if lead is None or lead["id"] not in carried.get("ids", []):
        return "passed", None            # the rule binds a carried leading hypothesis; a new one, or none, rests on this run
    since = _ms(carried.get("as_of")) or 0
    known: dict[str, int] = {}
    for entry in ledger:
        if entry.get("by") == BY or entry.get("status") != "ok":
            continue
        for handle, when in (entry.get("t_known") or {}).items():
            at = _ms(when)
            if at is not None:
                known[handle] = max(known.get(handle, 0), at)
    fresh = [h for h in lead.get("support") or [] if handle_class(h) != "aggregate" and known.get(h, 0) > since]
    return ("passed", None) if fresh else ("failed", CONTINUITY.format(id=lead["id"], as_of=carried.get("as_of")))
