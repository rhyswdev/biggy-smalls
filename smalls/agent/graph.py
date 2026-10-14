"""The investigator as an explicit StateGraph (docs/research/agent-design.md axis 1 and section 3):

    orient -> sweep -> gate -> [ask] -> tools -> agent -> gate -> [ask] -> tools -> agent ... -> finalize

- orient (code): the first user turn, from `now`, the source inventory, the trigger and the budget.
- sweep (code): the opening sweep. What is alerting, what changed and what people are saying is not a
  judgement call, so code asks. The calls are written to state as proposed calls from the harness and go
  through the same gate and the same tools node as the model's; their results enter as one user message.
- agent (model): one model call with every tool bound. The system prompt and the tool list never change,
  so they are a stable cached prefix, and the message history only grows.
- gate (code): applies the approval policy to each proposed call and writes the calls that need a person to
  state, with the round and the time asked. It runs once, so the policy is never decided again on resume.
- ask (code): only calls `interrupt()`, and records who decided and how long it took.
- tools (code): runs approved calls, turns rejections and deferrals into results, writes the ledger and the
  board, validates a submitted report, and appends the status line.
- finalize (code): the report with the fields code writes, or a partial one rendered from the board.

The registry and the inventory are injected, which is how offline tests run the same graph over a fixture.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import RetryPolicy, interrupt

from smalls.agent import board, carry, gate, prompts, validator, verifier
from smalls.agent.internal_tools import SUBMIT_REPORT, UPDATE_BOARD, HypothesisUpdate, toolset
from smalls.agent.models import as_model_call_error, provider_tools, reasoning_text, retryable
from smalls.agent.state import Call, InvestigationState, LedgerEntry, RunContext, merge_hypotheses
from smalls.contracts.envelope import Envelope
from smalls.contracts.handles import ic_note_handle
from smalls.contracts.report import Rationale, Report
from smalls.tools import errors, registry
from smalls.tools.registry import ToolContext, ToolSpec

Inventory = Callable[[ToolContext], Envelope]
NO_PRIOR_BOARD = "none"
UNPARSEABLE = "Could not parse the arguments for {tool}: {error}. Call it again with valid arguments."
ACCEPTED = "Report accepted."
NOT_ACCEPTED = "Report not accepted. {findings} Fix these and submit again."
NOT_RECORDED = "Not recorded. {findings}"
RECORDED = "Recorded: {what}."
NOTE_ROW = "{handle}  incident commander, {at}: {text}"
SWEEP_HEAD = "Opening sweep, run by the harness before your first turn. Each block is one tool call and its result."
SWEEP_BLOCK = "== {tool} {args}\n{text}"
SWEEP_RATIONALE = {"tests": "the opening sweep", "expect": "what is alerting, what changed and what people are saying"}
REPEAT = ("Already run in round {round} with the same arguments (call {call_id}): its result is above and would be the same now. "
          "Nothing was run and nothing was charged.")
STUCK = "[stuck: the last {n} reads returned nothing. What exists, from the inventory: {inventory}]"
REFUSED = "the model declined to continue"
OUT_OF_TOKENS = "the model ran out of output tokens before it finished a turn"
NO_VERDICT = "These alert streams from the opening sweep have no verdict: {handles}. Give each one this_incident, other_incident or noise."
FOLLOWUP = ("A follow-up from the incident commander, after your report:\n{row}\n"
            "If your report already answers it, answer in text with no tool call, and the report stands as submitted. "
            "If it needs evidence, investigate: you have {calls} more tool calls and {turns} more turns. "
            "Submit the report again only if what you found changes it.")
FOLLOWUP_TURNS = 2          # turns a follow-up gets beyond its calls: one to answer or submit, one to write the board


def iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rounds(state: dict[str, Any]) -> int:
    """Model turns so far. Counted from the usage rows, which a restart carries over; the messages it does not."""
    return sum(1 for row in state.get("usage", []) if row.get("node") == "agent")


def call_limit(state: dict[str, Any], ctx: RunContext) -> int:
    """The gated calls this run may have run by now: the budget, a revision's extra calls, or, during a
    follow-up, the calls used when it started plus the calls it was given."""
    followup = state.get("followup")
    return followup["calls_until"] if followup else ctx.budget(state.get("revisions", 0))


def turn_limit(state: dict[str, Any], ctx: RunContext) -> int:
    """The model turns this run may have taken by now, on the same terms as `call_limit`."""
    followup = state.get("followup")
    return followup["turns_until"] if followup else ctx.turn_cap(state.get("revisions", 0))


def _latest(verdict: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each claim's latest verdict: from the first claim-support pass, overlaid by any re-check."""
    if verdict.get("latest") is not None:
        return dict(verdict["latest"])
    rows = {c["id"]: c for c in ((verdict.get("claims") or {}).get("claims") or [])}
    rows.update({c["id"]: c for c in ((verdict.get("recheck") or {}).get("claims") or [])})
    return rows


def proposals(state: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per gated action call with its gate decision: written by code from the ledger, never by the
    model, so the report cannot say an action is awaiting approval while the ledger says it was refused."""
    decided = {g["call_id"]: g for g in state.get("gate_log", [])}
    out = []
    for entry in state.get("ledger", []):
        if entry.get("kind") != "action":
            continue
        g = decided.get(entry["call_id"], {})
        out.append({"call_id": entry["call_id"], "tool": entry["tool"], "args": entry["args"], "rationale": entry.get("rationale"),
                    "decision": g.get("decision", "deferred"), "by": g.get("by") or {"who": "policy", "via": "headless"},
                    "reason": g.get("reason"), "decided_at": g.get("decided_at")})
    return out


def partial_report(state: dict[str, Any], why: str) -> dict[str, Any]:
    """Rendered by code from the board when the agent did not submit a report. Only board entries that
    passed the citation check exist, so this is as grounded as a submitted report."""
    as_of = state.get("as_of")
    return {"as_of": as_of, "summary": f"Incomplete ({why}). This is the investigation board as it stood.", "impact": None,
            "hypotheses": board.for_report(state.get("hypotheses") or {}, as_of), "timeline": list(state.get("timeline") or []),
            "actions": [], "do_not": [], "alerts": [{"handle": h, "verdict": "unassessed"} for h in state.get("swept") or []],
            "open_questions": list(state.get("open_questions") or []), "stakeholder_update": None,
            "proposed_actions": proposals(state), "validation": None}


def build_graph(model: BaseChatModel, read_tools: dict[str, ToolSpec], inventory: Inventory, *, provider: str = "scripted",
                bind_kwargs: dict[str, Any] | None = None, checkpointer=None, texts: dict[str, Any] | None = None, strict: bool = True,
                fallback: tuple[BaseChatModel, str, dict[str, Any]] | None = None, offered: tuple[str, ...] | None = None,
                verify: dict[str, Any] | None = None):
    """`offered` names the tools the model is given, when that is fewer than the run has: the harness can
    still call the rest, as the opening sweep does. `verify` holds the verifier's passes, each a bound
    model with its system prompt, under `claims`, `contrarian` and `recheck`; without it no report is
    verified."""
    tools = toolset(read_tools)
    definitions = [registry.definition(spec, texts) for spec in tools.values() if offered is None or spec.name in offered]
    bound = model.bind_tools(provider_tools(definitions, provider, strict=strict), **(bind_kwargs or {}))
    # A fallback profile, used once when the model declines to continue: (model, provider, bind kwargs).
    second = fallback[0].bind_tools(provider_tools(definitions, fallback[1], strict=strict), **fallback[2]) if fallback else None
    system = prompts.load("investigator")

    def orient(state: InvestigationState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        ctx = runtime.context
        listed = inventory(ctx.tool_ctx)
        seeded = carry.seed(ctx.carry, read_tools, ctx.tool_ctx, ctx.evidence) if ctx.carry else None
        earlier = NO_PRIOR_BOARD
        if seeded:      # a later look: the earlier board, with everything it cites served again at this now (J8)
            earlier = carry.render(seeded["hypotheses"], seeded["timeline"], seeded["open_questions"], rows=seeded["rows"],
                                   dropped=seeded["carried"]["dropped"], retest=True,
                                   head=carry.EARLIER.format(as_of=seeded["carried"]["as_of"], run_id=seeded["carried"]["run_id"]))
        first = prompts.first_turn(now=iso(ctx.now), inventory=listed.text, trigger=state["trigger"],
                                   budget=f"{ctx.call_budget} tool calls, {ctx.turn_cap()} model turns",
                                   prior_board=earlier)
        # The inventory names handles without showing them, so it enters the ledger as referenced only.
        entry: LedgerEntry = {"call_id": "inventory", "round": 0, "by": "harness", "tool": "inventory", "kind": "internal", "args": {},
                              "rationale": None, "status": "ok", "shown": list(listed.shown), "referenced": list(listed.referenced),
                              "t_known": {}, "native_query": None, "window": None, "chars": len(listed.text), "latency_ms": 0.0,
                              "repeat_of": None, "used_tag_input": False, "empty": False, "identity": None}
        update = {"messages": [HumanMessage(first)], "as_of": iso(ctx.now), "checkpoint": ctx.checkpoint, "calls_used": 0,
                  "report_attempts": 0, "revisions": 0, "status": "investigating", "proposed": [], "stop_reason": None,
                  "ledger": [entry]}
        if seeded:
            update.update(hypotheses=seeded["hypotheses"], timeline=seeded["timeline"], open_questions=seeded["open_questions"],
                          ledger=[entry, *seeded["ledger"]], carried=seeded["carried"])
        return update

    def sweep(state: InvestigationState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        """The fixed, generic opening: alerts and changes over their windows, then the chat. No tag filter,
        ever. A tool the registry lacks is skipped."""
        ctx = runtime.context
        wanted = [("list_alerts", {"since": ctx.sweep.get("alerts", "-2h")}), ("list_changes", {"since": ctx.sweep.get("changes", "-24h")}),
                  ("read_chat", {"since": ctx.sweep.get("chat", "-2h"), **({"channel": ctx.channel} if ctx.channel else {})})]
        again = f"r{state['restarts']}_" if state.get("restarts") else ""      # after a restart the sweep runs again, under its own ids
        calls: list[Call] = [{"call_id": f"sweep_{again}{n:02d}_{tool}", "tool": tool, "args": args, "rationale": dict(SWEEP_RATIONALE),
                              "by": "harness", "invalid": None} for n, (tool, args) in enumerate(wanted, 1) if tool in tools]
        return {"proposed": calls}

    def route_sweep(state: InvestigationState) -> str:
        return "gate" if state.get("proposed") else "agent"

    def agent(state: InvestigationState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        ctx = runtime.context
        writer = get_stream_writer()
        started = time.perf_counter()
        try:
            ai = bound.invoke([SystemMessage(system), *state["messages"]])
        except Exception as error:      # recorded without any key value it may quote; the retry policy still sees its kind
            raise as_model_call_error(error) from None
        usage = ai.usage_metadata or {}
        row = {"node": "agent", "round": rounds(state) + 1, "attempt": getattr(runtime.execution_info, "node_attempt", 1),
               "latency_ms": round((time.perf_counter() - started) * 1000, 1), "usage": usage,
               "stop_reason": ai.response_metadata.get("stop_reason"), "model": ai.response_metadata.get("model_name")}
        writer({"type": "model_call", **row})
        rows = [row]
        fell_back = state.get("fallback_used", False)
        if row["stop_reason"] == "refusal" and second is not None and not fell_back:
            # The provider declined. Its own fallback, when the request carries one, has already been tried,
            # so the turn is asked once more of the fallback profile. Once per run.
            started, fell_back = time.perf_counter(), True
            ai = second.invoke([SystemMessage(system), *state["messages"]])
            usage = ai.usage_metadata or {}
            row = {**row, "node": "fallback", "latency_ms": round((time.perf_counter() - started) * 1000, 1), "usage": usage,
                   "stop_reason": ai.response_metadata.get("stop_reason"), "model": ai.response_metadata.get("model_name")}
            writer({"type": "model_call", **row})
            rows.append(row)
        # An input sent as null means "left out": a provider whose strict mode wants every property present sends it so.
        proposed: list[Call] = [{"call_id": c["id"], "tool": c["name"], "by": "agent", "invalid": None,
                                 "args": {k: v for k, v in c["args"].items() if k != "rationale" and v is not None},
                                 "rationale": c["args"].get("rationale")} for c in ai.tool_calls]
        proposed += [{"call_id": c["id"], "tool": c.get("name") or "unknown", "by": "agent", "args": {}, "rationale": None,
                      "invalid": str(c.get("error") or "invalid JSON")} for c in ai.invalid_tool_calls]
        update: dict[str, Any] = {"messages": [ai], "usage": rows, "proposed": proposed, "fallback_used": fell_back}
        if row["stop_reason"] == "refusal":
            update.update(stop_reason=REFUSED, proposed=[])
        elif row["stop_reason"] == "max_tokens" and not ai.tool_calls:
            update.update(stop_reason=OUT_OF_TOKENS, proposed=[])
        elif state.get("followup") and not proposed:
            # A turn with no tool call ends a follow-up, and its text is the answer. A model that wrote only
            # reasoning is answered by its reasoning, so the commander is never left with nothing.
            reasoning, text = reasoning_text(ai)
            update["followup"] = {**state["followup"], "answer": (text.strip() or reasoning.strip())}
        # The context guard is measured, not estimated: this call's real input plus the room the next
        # answer needs, against the model's input limit.
        if ctx.max_input_tokens and (usage.get("input_tokens") or 0) + ctx.max_tokens >= ctx.context_fraction * ctx.max_input_tokens:
            submitting = len(proposed) == 1 and proposed[0]["tool"] == SUBMIT_REPORT
            if not state.get("restarts") and not submitting and not ctx.single_call and not update.get("stop_reason"):
                # Once per run the runner restarts the conversation from the board, on a new thread (J16).
                # This turn's calls are dropped with it: nothing ran, and nothing is charged.
                update.update(restart_pending=True, proposed=[])
            else:
                update["stop_reason"] = "the context limit was reached"
        return update

    def route_agent(state: InvestigationState) -> str:
        if state.get("restart_pending"):
            return END
        proposed = state.get("proposed") or []
        submitting = len(proposed) == 1 and proposed[0]["tool"] == SUBMIT_REPORT
        if not proposed or (state.get("stop_reason") and not submitting):
            return "finalize"
        return "gate"

    def gate_node(state: InvestigationState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        ctx = runtime.context
        calls = list(state.get("proposed") or [])
        seen = validator.Seen.of(state)
        refuse = None
        if not any(c["tool"] == UPDATE_BOARD for c in calls):
            since = (state.get("followup") or {}).get("round", 0)
            refuse = board.stale_refusal(state.get("hypotheses") or {}, rounds(state), ctx.stale_rounds, since=since)
        decisions, ask = gate.apply(calls, tools, used=state.get("calls_used", 0), budget=call_limit(state, ctx),
                                    auto_approve=ctx.auto_approve, via=ctx.via, refuse_gated=refuse)
        # An action never reaches a person with evidence no tool showed.
        for card in list(ask):
            if card["kind"] != "action":
                continue
            unknown, only_listed = seen.split(card["args"].get("evidence") or [])
            if unknown or only_listed:
                ask.remove(card)
                call = next(c for c in calls if c["call_id"] == card["call_id"])
                decisions[card["call_id"]] = gate.decision(call, "rejected", "policy", ctx.via, validator.explain(unknown, only_listed))
        pending = {"round": rounds(state), "asked_at": gate.now_iso(), "calls": ask} if ask else None
        return {"decisions": decisions, "gate_log": list(decisions.values()), "pending": pending}

    def route_gate(state: InvestigationState) -> str:
        return "ask" if state.get("pending") else "tools"

    def ask(state: InvestigationState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        # Only the interrupt lives here. On resume this node runs again from the top, which is harmless.
        # Resume value per call: {"approve": true} | {"approve": false, "reason": "..."} | {"defer": true}
        pend = state["pending"]
        answers = interrupt(pend)
        decided_at = datetime.now(timezone.utc)
        wait_ms = round((decided_at - datetime.fromisoformat(pend["asked_at"])).total_seconds() * 1000, 1)
        decisions = dict(state["decisions"])
        new, notes = [], []
        # A person answers where the caller says the run is attended (the terminal or the web); the runner's
        # own headless answers are policy's.
        person_via = runtime.context.via if runtime.context.via != "headless" else "cli"
        for card in pend["calls"]:
            reply = (answers or {}).get(card["call_id"])
            verdict, who, reason = gate.answer(card, reply)
            via = "headless" if who == "policy" else person_via
            args, refused = gate.edited(card, reply, tools.get(card["tool"]))
            if refused:         # an edit that cannot run is not run: the call is deferred, and the reason says why
                verdict, reason = "deferred", refused
            handle = None
            if who == "human" and reason and verdict == "rejected":     # a person's reason is a citable note
                at = decided_at.isoformat(timespec="milliseconds")
                handle = ic_note_handle(runtime.context.run_id, at, reason)
                notes.append({"handle": handle, "text": reason, "submitted_at": at, "by": "incident commander", "call_id": card["call_id"]})
            d = gate.decision(card, verdict, who, via, handle or reason, reason)
            if args is not None:
                d["edited_args"] = args
            decisions[card["call_id"]] = d
            new.append(d)
        said = (answers or {}).get(gate.NOTE_KEY)
        if said:        # a note the commander added at the card: it does not block, and the model sees it next round
            at = decided_at.isoformat(timespec="milliseconds")
            notes.append({"handle": ic_note_handle(runtime.context.run_id, at, said), "text": said, "submitted_at": at,
                          "by": "incident commander", "call_id": None})
        return {"decisions": decisions, "gate_log": new, "pending": None, "ic_notes": notes,
                "gate_waits": [{"round": pend["round"], "asked_at": pend["asked_at"], "decided_at": decided_at.isoformat(),
                                "wait_ms": wait_ms, "calls": len(new)}]}

    def tools_node(state: InvestigationState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        ctx = runtime.context
        writer = get_stream_writer()
        decisions = state["decisions"]
        round_now = rounds(state)
        seen = validator.Seen.of(state)
        notes = {n["call_id"]: n for n in state.get("ic_notes", [])}
        used = state.get("calls_used", 0)
        attempts = state.get("report_attempts", 0)
        current = dict(state.get("hypotheses") or {})
        out: list[ToolMessage] = []
        blocks: list[str] = []          # the harness's own calls: one user message, never a fabricated tool turn
        swept: list[str] = []
        ledger: list[LedgerEntry] = []
        update: dict[str, Any] = {}
        accepted = None
        accepted_call = None

        for call in state.get("proposed") or []:
            d = decisions[call["call_id"]]
            spec = tools.get(call["tool"])
            entry: LedgerEntry = {"call_id": call["call_id"], "round": round_now, "by": call.get("by", "agent"), "tool": call["tool"],
                                  "kind": spec.kind if spec else "unknown", "args": call["args"], "rationale": call.get("rationale"),
                                  "status": "ok", "shown": [], "referenced": [], "t_known": {}, "native_query": None, "window": None,
                                  "chars": 0, "latency_ms": 0.0, "repeat_of": None, "used_tag_input": False, "empty": False,
                                  "identity": None}
            started = time.perf_counter()
            if call.get("invalid"):
                env = errors.error(UNPARSEABLE.format(tool=call["tool"], error=call["invalid"]), "arguments")
                entry["status"] = "error"
            elif d["decision"] == "rejected":
                text = gate.REJECTED.format(who=d["by"]["who"], reason=(d.get("reason_text") or d.get("reason") or "").rstrip("."))
                note = notes.get(call["call_id"])
                if note:
                    text += "\n" + NOTE_ROW.format(handle=note["handle"], at=note["submitted_at"], text=note["text"])
                env = errors.error(text)
                entry["status"] = "rejected"
            elif d["decision"] == "deferred":       # neither run nor refused: no signal either way
                env = Envelope(gate.DEFERRED)
                entry["status"] = "deferred"
            else:
                writer({"type": "tool_started", "call_id": call["call_id"], "tool": call["tool"]})
                if call["tool"] == UPDATE_BOARD:
                    env, merged, args = _update_board(spec, call, seen, current, round_now)
                    if merged:
                        update.setdefault("hypotheses", {}).update(merged)
                        current = merge_hypotheses(current, merged)
                        for hid in merged:      # the event carries the merged entry, not the partial update
                            writer({"type": "hypothesis_update", **current[hid]})
                    if args and args.timeline:
                        update["timeline"] = [*update.get("timeline", []), *(row.model_dump() for row in args.timeline)]
                    if args and args.open_questions:
                        update["open_questions"] = [*update.get("open_questions", []), *args.open_questions]
                elif call["tool"] == SUBMIT_REPORT:
                    env, result = _submit_report(spec, call, state, seen, current, round_now, attempts, single=ctx.single_call)
                    attempts = result["attempts"]
                    update["validation"] = result["validation"]
                    if result.get("report"):
                        accepted, accepted_call = result["report"], call["call_id"]
                        if result["merged"]:
                            update.setdefault("hypotheses", {}).update(result["merged"])
                            current = merge_hypotheses(current, result["merged"])
                else:
                    if d.get("edited_args") is not None:     # approved with edits: the edited arguments run (S2)
                        entry["edited_args"] = d["edited_args"]
                        call = {**call, "args": d["edited_args"]}
                    entry["identity"] = _identity(spec, call, ctx)
                    earlier = next((e for e in [*state.get("ledger", []), *ledger] if entry["identity"] and e.get("identity") == entry["identity"]
                                    and e["status"] == "ok" and e["round"] >= state.get("restart_round", 0)), None)
                    if earlier is not None:     # a repeated identical call returns a pointer and costs nothing (J4)
                        env = Envelope(REPEAT.format(round=earlier["round"], call_id=earlier["call_id"]), meta={"repeat": True})
                        entry["repeat_of"] = earlier["call_id"]
                    else:
                        env = _run(spec, call, ctx)
                        if entry.get("edited_args") is not None:     # the result says so on its first line
                            env = replace(env, text="\n".join([gate.EDITED.format(edited=json.dumps(entry["edited_args"], ensure_ascii=False),
                                                                                   proposed=json.dumps(entry["args"], ensure_ascii=False)), env.text]))
                        if entry["by"] == "agent" and env.meta.get("error") != "arguments":     # a call counts when it runs; the sweep is free (J5)
                            used += 1
                        if env.status == "ok" and ctx.evidence:
                            ctx.evidence(call["call_id"], env.rows)
                entry.update(status="repeat" if env.meta.get("repeat") else env.status, shown=list(env.shown), referenced=list(env.referenced),
                             t_known={r.handle: r.t_known for r in env.rows}, native_query=env.native_query,
                             window=list(env.window) if env.window else None, used_tag_input=bool(env.meta.get("tag")),
                             empty=bool(env.meta.get("empty")))
            seen.add(entry)     # handles from earlier calls in the same batch are citable too
            entry["chars"] = len(env.text)
            entry["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            ledger.append(entry)
            writer({"type": "tool_result", **{k: entry[k] for k in ("call_id", "tool", "kind", "by", "status", "shown", "referenced",
                                                                    "native_query", "window", "chars", "latency_ms")},
                    "edited_args": entry.get("edited_args"),
                    "first_line": env.text.split("\n", 1)[0][:200]})
            if entry["by"] == "harness":
                blocks.append(SWEEP_BLOCK.format(tool=call["tool"], args=json.dumps(call["args"], ensure_ascii=False), text=env.text))
                if call["tool"] == "list_alerts" and env.status == "ok":
                    swept += list(env.shown)
            else:
                out.append(ToolMessage(env.text, tool_call_id=call["call_id"], name=call["tool"],
                                       status="error" if env.status == "error" or entry["status"] == "rejected" else "success"))

        limit = call_limit(state, ctx)
        turns_left = turn_limit(state, ctx) - round_now
        messages: list[Any] = list(out)
        if blocks:
            messages.append(HumanMessage("\n\n".join([SWEEP_HEAD, *blocks])))
            update["swept"] = swept
        all_notes = state.get("ic_notes", [])
        fresh = [n for n in all_notes[state.get("notes_shown", 0):] if not n.get("call_id")]      # a rejection's reason is already in its result
        # Notes the commander added while the run was running (S4). They are only read here: the runner takes
        # them out of its inbox once this update is applied, so a node that runs again loses none.
        known = {n["handle"] for n in all_notes}
        arrived = [n for n in (ctx.inbox() if ctx.inbox else []) if n["handle"] not in known]
        if accepted is None and messages and (fresh or arrived):     # a note steers; it reaches the model as a citable row
            rows = "\n".join(NOTE_ROW.format(handle=n["handle"], at=n["submitted_at"], text=n["text"]) for n in [*fresh, *arrived])
            messages[-1].content = f"{messages[-1].content}\n{rows}"
            update["notes_shown"] = len(all_notes) + len(arrived)
            if arrived:
                update["ic_notes"] = arrived
        reads = [e for e in [*state.get("ledger", []), *ledger] if e["by"] == "agent" and e["kind"] == "read" and e["status"] == "ok"]
        empty_run = next((n for n, e in enumerate(reversed(reads)) if not e["empty"]), len(reads))
        if accepted is None and messages and empty_run >= ctx.stuck_empty and any(e["kind"] == "read" and e["status"] == "ok" for e in ledger):
            hint = " ".join(inventory(ctx.tool_ctx).text.split())[:600]      # nothing is refused: this is a hint, and an event
            messages[-1].content = f"{messages[-1].content}\n{STUCK.format(n=empty_run, inventory=hint)}"
            writer({"type": "stuck", "empty_reads": empty_run, "round": round_now})
        if accepted is None and messages:   # appended once, when the message is created: the history is append-only
            only_carried = carry.carried_only([*state.get("ledger", []), *ledger])      # these do not count toward ready (J2)
            line = board.status_line(current, round=round_now, used=used, limit=limit, turns_left=turns_left, carried=only_carried)
            messages[-1].content = f"{messages[-1].content}\n{line}"
            writer({"type": "board_status", "line": line, "ready": not board.missing_for_ready(current, only_carried),
                    "stale": board.stale_ids(current, round_now)})
        lead = board.leading(current)
        # The round's closing line: everything a terminal needs to draw it, so a replay draws the same line.
        writer({"type": "budget", "used": used, "limit": limit, "turns_left": turns_left, "round": round_now,
                "board": {"leading": {"id": lead["id"], "confidence": lead.get("confidence")} if lead else None,
                          "open": sum(1 for h in current.values() if h.get("status") == "open"),
                          "rejected": sum(1 for h in current.values() if h.get("status") == "rejected")}})
        update.update(messages=messages, ledger=ledger, calls_used=used, report_attempts=attempts, proposed=[])
        if accepted is not None:
            update["report"] = accepted
            if verify and ctx.verifier and not ctx.single_call:
                # The verifier answers this call, so its verdict can come back as the call's own result (J13).
                update.update(messages=[m for m in messages if getattr(m, "tool_call_id", None) != accepted_call], verify_call=accepted_call,
                              status="verifying")
        return update

    def _identity(spec: ToolSpec, call: Call, ctx: RunContext) -> str | None:
        """What makes two calls the same call (J4): the verifier asks the same question of a proposed check."""
        return verifier.identity(spec, call["args"], ctx.now)

    def _run(spec: ToolSpec, call: Call, ctx: RunContext) -> Envelope:
        if call.get("by", "agent") == "agent":      # the model owes a reason for every gated call
            try:
                Rationale.model_validate(call.get("rationale"))
            except Exception:
                return errors.bad_arguments(spec.name, "rationale: an object with `tests` and `expect` is required")
        return registry.call(spec, call["args"], ctx.tool_ctx)

    def _update_board(spec: ToolSpec, call: Call, seen: validator.Seen, current: dict[str, Any], round_now: int):
        """Returns (the result, the hypothesis updates to merge, the arguments when the write was taken)."""
        try:
            args = spec.args.model_validate(call["args"])
        except Exception as problem:
            return errors.bad_arguments(spec.name, problem), {}, None
        unknown, only_listed = seen.split(validator.board_handles(args))
        if unknown or only_listed:      # board writes are grounded like reports
            return errors.error(NOT_RECORDED.format(findings=validator.explain(unknown, only_listed))), {}, None
        merged, problems, notes = board.apply_update(current, args.hypotheses or [], round=round_now, call_id=call["call_id"])
        if problems:
            return errors.error(NOT_RECORDED.format(findings="; ".join(problems) + ".")), {}, None
        after = merge_hypotheses(current, merged)
        what = [f"{hid} {after[hid]['status']} {after[hid]['confidence']:.2f}" for hid in merged]
        what += [f"{len(args.timeline)} timeline rows"] if args.timeline else []
        what += [f"{len(args.open_questions)} open questions"] if args.open_questions else []
        text = RECORDED.format(what="; ".join(what) or "nothing changed")
        return Envelope(" ".join([text, *(n + "." for n in notes)])), merged, args

    def _submit_report(spec: ToolSpec, call: Call, state: dict[str, Any], seen: validator.Seen, current: dict[str, Any],
                       round_now: int, attempts: int, *, single: bool = False) -> tuple[Envelope, dict[str, Any]]:
        """Validate a submitted report. One retry per submission cycle covers every finding; after it a
        bad handle is dropped and recorded, and a claim left with no handle is kept and flagged (J14).
        In a single-call run there is no board tool and no second turn: a hypothesis may be new here, as
        one the model derived, and the first submission is treated as the last."""
        result: dict[str, Any] = {"attempts": attempts, "validation": state.get("validation"), "merged": {}}
        try:
            args = spec.args.model_validate(call["args"])
        except Exception as problem:
            return errors.bad_arguments(spec.name, problem), result
        updates = [HypothesisUpdate(**h.model_dump(exclude_unset=True), source="derived") if single and h.id not in current else h
                   for h in args.hypotheses]
        merged, problems, notes = board.apply_update(current, updates, round=round_now, call_id=call["call_id"], allow_new=single)
        after = merge_hypotheses(current, merged)
        draft = {**args.model_dump(exclude={"hypotheses"}), "as_of": state["as_of"], "hypotheses": board.for_report(after, state["as_of"])}
        unknown, only_listed = seen.split(validator.report_handles(draft))
        findings = [p + "." for p in problems]
        if unknown or only_listed:
            findings.append(validator.explain(unknown, only_listed))
        unjudged = [h for h in state.get("swept") or [] if h not in {a["handle"] for a in draft["alerts"]}]
        if unjudged:        # one verdict for every alert stream the sweep showed (J7)
            findings.append(NO_VERDICT.format(handles=", ".join(unjudged)))
        _, stale_lead = carry.continuity(draft, state.get("carried"), state.get("ledger", []))
        if stale_lead:      # a carried leading hypothesis needs something this run found (J9)
            findings.append(stale_lead + ".")
        findings += [f"{claim}: {what}." for claim, what in validator.structure(draft)]
        if findings and attempts == 0 and not single:
            result.update(attempts=1, validation={"ok": False, "attempts": 1, "findings": findings})
            return errors.error(NOT_ACCEPTED.format(findings=" ".join(findings))), result
        if problems:        # the board would not take the update even on the retry: the board stands as it was
            merged, after = {}, current
            draft["hypotheses"] = board.for_report(after, state["as_of"])
        bad = [*unknown, *only_listed]
        report, flagged = validator.drop(draft, bad) if bad else (draft, [])
        flagged = sorted({*flagged, *(claim for claim, _ in validator.structure(report))})
        report["alerts"] = [*report["alerts"], *({"handle": h, "verdict": "unassessed"} for h in unjudged)]    # code adds these, never the model
        merged = {hid: {k: ([h for h in v if h not in bad] if isinstance(v, list) else v) for k, v in upd.items()} for hid, upd in merged.items()}
        continuity, _ = carry.continuity(report, state.get("carried"), state.get("ledger", []))
        validation = {"ok": not (bad or problems or flagged or unjudged or continuity == "failed"), "attempts": attempts + 1, "dropped": bad,
                      "flagged": flagged, "continuity": continuity, "verifier": None, "disputed": []}
        report["validation"] = validation
        result.update(attempts=attempts, validation=validation, merged=merged, report=report)
        return Envelope(" ".join([ACCEPTED, *(n + "." for n in notes)])), result

    def verify_node(state: InvestigationState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        """The verifier's passes over an accepted report, and what code decides from them. The first time,
        claim support and the contrarian case; after a revision, claim support on what the revision changed."""
        ctx = runtime.context
        writer = get_stream_writer()
        report, call_id, round_now = state["report"], state["verify_call"], rounds(state)
        evidence = ctx.read_evidence() if ctx.read_evidence else []
        usage: list[dict[str, Any]] = []

        def run(which: str, turn: str) -> verifier.SubmitVerdictArgs | None:
            bound_pass, system_prompt = verify[which]
            try:
                verdict, rows = verifier.ask(bound_pass, system_prompt, turn)
            except Exception as error:
                raise as_model_call_error(error) from None
            for row in rows:
                row = {"node": f"verifier_{which}", "round": round_now, **row}
                writer({"type": "model_call", **row})
                usage.append(row)
            return verdict

        def answer(text: str, ok: bool) -> ToolMessage:
            return ToolMessage(text, tool_call_id=call_id, name=SUBMIT_REPORT, status="success" if ok else "error")

        def stands(verdict: dict[str, Any], *judged: Any) -> dict[str, Any]:
            final = {**report, "validation": {**(report.get("validation") or {}), "verifier": verdict["status"],
                                              "disputed": verifier.disputed(report, *judged)}}
            writer({"type": "verifier_verdict", "status": verdict["status"], "disputed": final["validation"]["disputed"],
                    "decision": verdict["decision"], "diff": verdict.get("diff")})
            return {"messages": [answer(ACCEPTED, True)], "usage": usage, "verdict": verdict, "report": final, "verify_call": None}

        first = state.get("verdict")
        if (state.get("revisions", 0) or state.get("followup")) and first:
            # The re-check, after a revision or a follow-up: only what the new report added or changed. The
            # other claims keep the latest verdict they had, so a second re-check builds on the first.
            diff = verifier.changed_claims(state.get("first_report") or {}, report)
            only = set(diff["added"]) | set(diff["changed"])
            again = run(verifier.RECHECK, verifier.user_turn(report, evidence, verifier.RECHECK, only=only)) if only and verifier.RECHECK in verify else None
            prior = _latest(first)
            kept = {"claims": [c for cid, c in prior.items() if cid not in only]}
            latest = {**{cid: c for cid, c in prior.items() if cid not in diff["removed"]},
                      **{c["id"]: c for c in (again.model_dump()["claims"] if again else [])}}
            return stands({**first, "status": "revised", "recheck": again.model_dump() if again else None, "diff": diff, "latest": latest},
                          kept, again)
        claims = run(verifier.CLAIMS, verifier.user_turn(report, evidence, verifier.CLAIMS)) if verifier.CLAIMS in verify else None
        contrarian = (run(verifier.CONTRARIAN, verifier.user_turn(report, evidence, verifier.CONTRARIAN, digest_tokens=verify.get("digest_tokens", 1500)))
                      if verifier.CONTRARIAN in verify else None)
        decision = verifier.decide(report, claims, contrarian, read_tools=read_tools, ledger=state.get("ledger", []),
                                   shown={row["handle"] for row in evidence}, now=ctx.now)
        verdict = {"claims": claims.model_dump() if claims else None, "contrarian": contrarian.model_dump() if contrarian else None,
                   "decision": decision, "status": "pass" if (claims or contrarian) else "unverified", "recheck": None, "diff": None}
        if decision["revise"] and not state.get("revisions", 0) and not state.get("stop_reason") and not state.get("followup"):
            verdict["status"] = "revise"
            turns = ctx.turn_cap(1) - ctx.turn_cap(0)
            line = board.status_line(state.get("hypotheses") or {}, round=round_now, used=state.get("calls_used", 0), limit=ctx.budget(1),
                                     turns_left=ctx.turn_cap(1) - round_now)
            writer({"type": "verifier_verdict", "status": "revise", "disputed": [], "decision": decision, "diff": None})
            return {"messages": [answer(verifier.sent_back(decision, calls=ctx.revision_calls, turns=turns) + "\n" + line, False)], "usage": usage,
                    "verdict": verdict, "first_report": report, "report": None, "verify_call": None, "revisions": 1, "report_attempts": 0,
                    "status": "investigating"}
        return stands(verdict, claims)

    def route_verify(state: InvestigationState) -> str:
        return "finalize" if state.get("report") else "agent"

    def route_tools(state: InvestigationState, runtime: Runtime[RunContext]) -> str:
        if state.get("report") and state.get("verify_call"):
            return "verify"
        if state.get("report") or state.get("stop_reason"):
            return "finalize"
        if rounds(state) >= turn_limit(state, runtime.context):   # internal calls are free, so turns are capped too
            return "finalize"
        return "agent"

    def finalize(state: InvestigationState, runtime: Runtime[RunContext]) -> dict[str, Any]:
        if state.get("followup"):
            return _end_followup(state, runtime.context)
        if state.get("report"):
            report = {**state["report"], "proposed_actions": proposals(state)}
            Report.model_validate(report)
            return {"status": "completed", "report": report}
        why = state.get("stop_reason") or ("the model-turn limit was reached" if rounds(state) >= runtime.context.turn_cap(state.get("revisions", 0))
                                           else "the agent stopped without submitting a report")
        report = partial_report(state, why)
        Report.model_validate(report)
        return {"status": "incomplete", "report": report, "stop_reason": why}

    def _end_followup(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
        """A follow-up ends with a new report, an answer in text, or a stop. Without a new report, the report
        that stood when it began stands, with its status, and only its code-written proposals are brought up
        to date from the ledger, since the follow-up may have proposed an action too."""
        f = state["followup"]
        stopped = state.get("stop_reason") or ("the follow-up's turn limit was reached" if rounds(state) >= turn_limit(state, ctx) else None)
        outcome = "report" if state.get("report") else "answered" if f.get("answer") else "stopped"
        report = {**(state.get("report") or f["report"]), "proposed_actions": proposals(state)}
        Report.model_validate(report)
        done = {k: v for k, v in f.items() if k != "report"}
        done.update(outcome=outcome, stop_reason=stopped if outcome == "stopped" else None, ended_round=rounds(state))
        return {"status": "completed" if outcome == "report" else f["status"], "report": report, "followup": None, "followups": [done],
                "stop_reason": None if outcome == "report" else f.get("stop_reason")}

    retry = RetryPolicy(max_attempts=2, retry_on=retryable)
    g = StateGraph(InvestigationState, context_schema=RunContext)
    g.add_node("orient", orient)
    g.add_node("sweep", sweep)
    g.add_node("agent", agent, retry_policy=retry)
    g.add_node("gate", gate_node)
    g.add_node("ask", ask)
    g.add_node("tools", tools_node)
    g.add_node("verify", verify_node, retry_policy=retry)
    g.add_node("finalize", finalize)
    g.add_edge(START, "orient")
    g.add_edge("orient", "sweep")
    g.add_conditional_edges("sweep", route_sweep, ["gate", "agent"])
    g.add_conditional_edges("agent", route_agent, ["gate", "finalize", END])
    g.add_conditional_edges("gate", route_gate, ["ask", "tools"])
    g.add_edge("ask", "tools")
    g.add_conditional_edges("tools", route_tools, ["agent", "verify", "finalize"])
    g.add_conditional_edges("verify", route_verify, ["agent", "finalize"])
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
