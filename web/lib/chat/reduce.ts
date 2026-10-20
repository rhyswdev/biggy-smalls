// The one fold of a chat's events into what its screens show (ruling U4). Pure: no React, no clock, no I/O.
// The server folds the stored events for the first paint; the browser folds the same events, then each new
// one as it streams, so a replayed chat and a live one render the same way.
//
// The events are the agent's protocol (smalls/agent/events.py), numbered per chat.

import type { Card, ChatEvent, Hypothesis, Report } from "@/lib/types";

export type RowState = "proposed" | "awaiting" | "approved" | "auto" | "edited" | "rejected" | "skipped" | "refused";

export interface Row {
  call_id: string;
  run_id: string;
  round: number;
  tool: string;
  kind: string;
  by: string;
  args: Record<string, unknown>;
  rationale: { tests: string; expect: string } | null;
  native_query: string | null;
  window: [string, string] | null;
  state: RowState;
  who: string | null;           // who decided: human, auto or policy
  reason: string | null;        // a rejection's or a deferral's words
  note: string | null;          // the ic:note handle a person's rejection minted
  edited_args: Record<string, unknown> | null;
  running: boolean;
  result: { status: string; first_line: string; shown: string[]; referenced: string[]; chars: number } | null;
}

export interface BoardChange {
  id: string;
  statement: string;
  from: { status: string; confidence: number } | null;
  to: { status: string; confidence: number };
}

export interface Group {
  kind: "group";
  key: string;
  run_id: string;
  round: number;
  by: "harness" | "agent";
  calls: string[];
  interrupt_id: string | null;
  narrative: string | null;
  text: string | null;
  board: BoardChange[];
}

export type Turn =
  | { kind: "ic"; key: string; run_id: string; text: string; trigger: boolean; at: string }
  | { kind: "note"; key: string; run_id: string; handle: string; text: string; at: string; followup: boolean }
  | Group
  | { kind: "answer"; key: string; run_id: string; text: string; note: string | null }
  | { kind: "report"; key: string; run_id: string; status: string; report: Report; seq: number }
  | { kind: "notice"; key: string; run_id: string; tone: "info" | "error" | "clamped" | "empty"; text: string };

export interface BoardEntry {
  id: string;
  statement: string;
  role: string;
  status: "leading" | "open" | "rejected";
  confidence: number;
  first: number;                // where the claim started: the tick on its bar
  subject: string;
  support: string[];
  against: string[];
}

export interface Step {
  call_id: string;
  tool: string;
  state: "done" | "failed" | "running" | "queued";
  text: string;
}

export interface Pending {
  run_id: string;
  interrupt_id: string;
  round: number;
  cards: Card[];
}

export interface ChatState {
  lastSeq: number;
  status: "idle" | "running" | "waiting" | "stopped" | "paused" | "completed" | "incomplete" | "failed";
  phase: string | null;
  runs: string[];
  startedAt: string | null;     // the latest segment's start, wall clock
  lastAt: string | null;        // the latest event's wall clock
  heartbeat: number | null;     // seconds the model has been working with nothing to show
  rows: Record<string, Row>;
  turns: Turn[];
  board: Record<string, BoardEntry>;
  pending: Pending | null;
  report: Report | null;
  reportStatus: string | null;
  draftSeq: number | null;
  verifier: { status: string; disputed: string[]; checks: { statement: string; tool: string; args: Record<string, unknown> }[] } | null;
  answers: { text: string; note: string | null }[];
  followups: { n: number; outcome: string; note: string }[];
  finished: { status: string; tool_calls: number; model_calls: number; wall_s: number; cost_usd: number | null } | null;
  modelCost: number;
  autoApprove: boolean | null;
  trigger: string | null;
  now: string | null;
  attended: boolean | null;
  outcomes: Record<string, string>;     // call id -> the summariser's clause for "How Smalls got here" (v4 plan 5.1)
  outcomeSummary: string | null;        // the summariser's headline of what the checks established; the latest wins
}

export function initial(): ChatState {
  return {
    lastSeq: 0, status: "idle", phase: null, runs: [], startedAt: null, lastAt: null, heartbeat: null, rows: {}, turns: [], board: {},
    pending: null, report: null, reportStatus: null, draftSeq: null, verifier: null, answers: [], followups: [], finished: null, modelCost: 0,
    autoApprove: null, trigger: null, now: null, attended: null, outcomes: {}, outcomeSummary: null,
  };
}

const str = (v: unknown): string => (typeof v === "string" ? v : v == null ? "" : String(v));
const num = (v: unknown): number => (typeof v === "number" ? v : Number(v) || 0);
const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(str) : []);

// The turns array is copied per event, but its items are shared with the previous state, so a group is never
// changed in place: `patch` puts a new object at its index.
function groupFor(state: ChatState, run_id: string, round: number, by: "harness" | "agent"): number {
  const key = `${run_id}:${by === "harness" ? "sweep" : round}`;
  const found = state.turns.findIndex((t) => t.kind === "group" && t.key === key);
  if (found >= 0) return found;
  state.turns.push({ kind: "group", key, run_id, round, by, calls: [], interrupt_id: null, narrative: null, text: null, board: [] });
  return state.turns.length - 1;
}

function lastGroup(state: ChatState, run_id: string): number {
  for (let i = state.turns.length - 1; i >= 0; i--) {
    const t = state.turns[i]!;
    if (t.kind === "group" && t.run_id === run_id && t.round >= 0) return i;
  }
  return -1;
}

function provisional(state: ChatState, run_id: string, seq: number): number {
  const last = state.turns[state.turns.length - 1];
  if (last && last.kind === "group" && last.round === -1) return state.turns.length - 1;
  state.turns.push({ kind: "group", key: `${run_id}:pending:${seq}`, run_id, round: -1, by: "agent", calls: [], interrupt_id: null, narrative: null, text: null, board: [] });
  return state.turns.length - 1;
}

function group(state: ChatState, i: number): Group {
  return state.turns[i] as Group;
}

function patch(state: ChatState, i: number, change: Partial<Group>): void {
  state.turns[i] = { ...group(state, i), ...change };
}

function decisionState(decision: string, who: string, edited: boolean): RowState {
  if (decision === "rejected") return who === "human" ? "rejected" : "refused";
  if (decision === "deferred") return "skipped";
  if (edited) return "edited";
  return who === "human" ? "approved" : "auto";
}

/** Fold one event into the state. Events at or below the last seen number are ignored, so a replay and a
 * reconnect can overlap without double counting. */
export function apply(prev: ChatState, ev: ChatEvent): ChatState {
  if (ev.seq <= prev.lastSeq) return prev;
  const state: ChatState = { ...prev, rows: { ...prev.rows }, turns: [...prev.turns], board: { ...prev.board }, lastSeq: ev.seq, lastAt: ev.t_wall };
  const d = ev.data;
  const run = ev.run_id;
  if (ev.type !== "heartbeat") state.heartbeat = null;
  switch (ev.type) {
    case "run_started": {
      if (!state.runs.includes(run)) state.runs = [...state.runs, run];
      state.status = "running";
      state.startedAt = ev.t_wall;
      state.trigger = str(d.trigger);
      state.now = str(d.now);
      state.autoApprove = Boolean(d.auto_approve);
      state.attended = Boolean(d.attended);
      state.finished = null;
      state.turns.push({ kind: "ic", key: `${run}:trigger`, run_id: run, text: str(d.trigger), trigger: true, at: str(d.now) });
      break;
    }
    case "phase": {
      const name = str(d.name);
      if (name === "resumed" || name === "followup") {
        state.status = "running";
        state.startedAt = ev.t_wall;
        state.finished = null;
      }
      if (name !== "resumed") state.phase = name;
      break;
    }
    case "heartbeat":
      state.heartbeat = num(d.seconds);
      break;
    case "auto_approve":
      state.autoApprove = Boolean(d.on);
      break;
    case "thinking_delta":
    case "thinking":
    case "text_delta": {
      // A model call's reasoning and its visible text come just before its model_call event: they are held in a
      // provisional group until that event says which round they belong to. The reasoning grows as its deltas
      // stream in, and the whole of it, sent once at the end of the call, replaces what the deltas built.
      const i = provisional(state, run, ev.seq);
      const g = group(state, i);
      patch(state, i, ev.type === "thinking" ? { narrative: str(d.text) }
        : ev.type === "thinking_delta" ? { narrative: (g.narrative ?? "") + str(d.text) } : { text: (g.text ?? "") + str(d.text) });
      break;
    }
    case "model_call": {
      if (str(d.node) !== "agent" && str(d.node) !== "fallback") {
        state.modelCost += num(d.cost_usd);
        break;
      }
      state.modelCost += num(d.cost_usd);
      const round = num(d.round);
      // The narrative that came just before this call becomes its round's narrative.
      const last = state.turns[state.turns.length - 1];
      const before = last && last.kind === "group" && last.round === -1 ? (state.turns.pop() as Group) : null;
      const i = groupFor(state, run, round, "agent");
      if (before) patch(state, i, { narrative: before.narrative, text: before.text });
      break;
    }
    case "tool_call_proposed": {
      const kind = str(d.kind);
      // Internal calls are the board's, and a hidden one is a malformed call the agent was told to fix and retried
      // (malformed_shown): neither is a read the commander sees. Its decision and result find no row and are skipped too.
      if (kind === "internal" || d.hidden === true) break;
      const by = str(d.by) === "harness" ? "harness" : "agent";
      const last = lastGroup(state, run);
      const i = by === "harness" ? groupFor(state, run, 0, "harness") : last >= 0 ? last : groupFor(state, run, 0, "agent");
      const g = group(state, i);
      const call_id = str(d.call_id);
      if (!g.calls.includes(call_id)) patch(state, i, { calls: [...g.calls, call_id] });
      state.rows[call_id] = {
        call_id, run_id: run, round: g.round, tool: str(d.tool), kind, by, args: (d.args as Record<string, unknown>) ?? {},
        rationale: (d.rationale as Row["rationale"]) ?? null, native_query: null, window: null, state: "proposed", who: null, reason: null,
        note: null, edited_args: null, running: false, result: null,
      };
      break;
    }
    case "gate_pending": {
      const cards = (d.calls as Card[]) ?? [];
      state.pending = { run_id: run, interrupt_id: str(d.interrupt_id), round: num(d.round), cards };
      for (const c of cards) {
        const row = state.rows[c.call_id];
        if (row) state.rows[c.call_id] = { ...row, state: "awaiting", native_query: c.native_query ?? row.native_query, window: c.window ?? row.window };
        const i = state.turns.findIndex((t) => t.kind === "group" && t.calls.includes(c.call_id));
        if (i >= 0) patch(state, i, { interrupt_id: str(d.interrupt_id) });
      }
      break;
    }
    case "gate_decision": {
      const row = state.rows[str(d.call_id)];
      const by = (d.by as { who?: string } | undefined) ?? {};
      if (row) {
        const edited = d.edited_args != null;
        state.rows[row.call_id] = {
          ...row, state: decisionState(str(d.decision), str(by.who), edited), who: str(by.who) || null,
          reason: d.reason_text != null ? str(d.reason_text) : d.decision !== "approved" && d.reason != null && !str(d.reason).startsWith("ic:note/") ? str(d.reason) : null,
          note: str(d.reason).startsWith("ic:note/") ? str(d.reason) : null, edited_args: (d.edited_args as Record<string, unknown>) ?? null,
        };
      }
      if (state.pending && state.pending.cards.every((c) => state.rows[c.call_id] && state.rows[c.call_id]!.state !== "awaiting")) state.pending = null;
      break;
    }
    case "tool_started": {
      const row = state.rows[str(d.call_id)];
      if (row) state.rows[row.call_id] = { ...row, running: true };
      break;
    }
    case "tool_result": {
      const row = state.rows[str(d.call_id)];
      if (row) {
        const window = Array.isArray(d.window) ? (d.window as [string, string]) : row.window;
        state.rows[row.call_id] = {
          ...row, running: false, native_query: d.native_query != null ? str(d.native_query) : row.native_query, window,
          edited_args: (d.edited_args as Record<string, unknown>) ?? row.edited_args,
          result: { status: str(d.status), first_line: str(d.first_line), shown: list(d.shown), referenced: list(d.referenced), chars: num(d.chars) },
        };
      }
      break;
    }
    case "hypothesis_update": {
      const h = d as unknown as Hypothesis & { history?: { round?: number; from?: { status: string; confidence: number } | null; to: { status: string; confidence: number } }[] };
      const history = h.history ?? [];
      const before = state.board[h.id];
      const first = history.length && history[0]?.to ? num(history[0].to.confidence) : before?.first ?? num(h.confidence);
      state.board[h.id] = {
        id: h.id, statement: str(h.statement), role: str(h.role), status: h.status, confidence: num(h.confidence), first, subject: str(h.subject),
        support: list(h.support), against: list(h.against),
      };
      const last = history[history.length - 1];
      const i = lastGroup(state, run);
      if (i >= 0 && last?.to) {
        const change: BoardChange = { id: h.id, statement: str(h.statement), from: last.from ?? null, to: last.to };
        patch(state, i, { board: [...group(state, i).board.filter((c) => c.id !== h.id), change] });
      }
      break;
    }
    case "ic_note": {
      if (d.call_id) break;       // a rejection's reason: shown on its row
      const followup = state.status !== "running" || state.phase === "done";
      state.turns.push({ kind: "note", key: `${run}:note:${str(d.handle)}`, run_id: run, handle: str(d.handle), text: str(d.text), at: str(d.submitted_at), followup });
      break;
    }
    case "answer": {
      const i = lastGroup(state, run);
      if (i >= 0 && group(state, i).text) patch(state, i, { text: null });     // the answer turn shows it
      state.answers = [...state.answers, { text: str(d.text), note: d.note != null ? str(d.note) : null }];
      state.turns.push({ kind: "answer", key: `${run}:answer:${ev.seq}`, run_id: run, text: str(d.text), note: d.note != null ? str(d.note) : null });
      break;
    }
    case "followup_finished":
      state.followups = [...state.followups, { n: num(d.n), outcome: str(d.outcome), note: str(d.note) }];
      break;
    case "draft_report":
      state.draftSeq = ev.seq;
      break;
    case "verifier_verdict":
      state.verifier = { status: str(d.status), disputed: list(d.disputed),
        checks: (((d.decision as { checks?: unknown[] } | undefined)?.checks ?? []) as { statement?: string; tool?: string; args?: Record<string, unknown> }[])
          .map((c) => ({ statement: str(c.statement), tool: str(c.tool), args: c.args ?? {} })) };
      break;
    case "report": {
      const report = d.report as Report;
      const status = str(d.status);
      const previous = state.report;
      state.report = report;
      state.reportStatus = status;
      // A report the run already showed (a follow-up answered in text leaves it standing) is not shown twice.
      const same = previous && JSON.stringify({ ...previous, proposed_actions: [] }) === JSON.stringify({ ...report, proposed_actions: [] });
      if (!same) state.turns.push({ kind: "report", key: `${run}:report:${ev.seq}`, run_id: run, status, report, seq: ev.seq });
      break;
    }
    case "outcomes":
      state.outcomes = { ...state.outcomes, ...((d.clauses as Record<string, string> | undefined) ?? {}) };
      if (typeof d.summary === "string" && d.summary.trim()) state.outcomeSummary = d.summary.trim();
      break;
    case "stuck":
      state.turns.push({ kind: "notice", key: `${run}:stuck:${ev.seq}`, run_id: run, tone: "empty",
        text: `The last ${plural(num(d.empty_reads), "check")} found nothing. A note can steer Smalls.` });
      break;
    case "error":
      state.turns.push({ kind: "notice", key: `${run}:error:${ev.seq}`, run_id: run, tone: "error",
        text: plainWords(`${str(d.where) === "interrupt" ? "The run was interrupted" : `The ${str(d.where) || "run"} failed`}${d.message ? `: ${str(d.message)}` : ""}. Next: ${str(d.next) || "nothing"}.`) });
      break;
    case "run_paused": {
      if (d.stopped) {
        state.status = "stopped";
        state.turns.push({ kind: "notice", key: `${run}:stopped:${ev.seq}`, run_id: run, tone: "info", text: "Stopped. The working picture as it stood is kept; the run can go on from here." });
      } else {
        state.status = d.interrupt_id ? "waiting" : "paused";
      }
      break;
    }
    case "run_finished": {
      const status = str(d.status) as ChatState["status"];
      state.status = status === "completed" || status === "incomplete" || status === "failed" ? status : "completed";
      state.phase = "done";
      state.pending = null;
      const tokens = (d.tokens as { cost_usd?: number } | undefined) ?? {};
      state.finished = { status: str(d.status), tool_calls: num(d.tool_calls), model_calls: num(d.model_calls), wall_s: num(d.wall_s),
        cost_usd: tokens.cost_usd ?? null };
      if (status === "incomplete" || status === "failed") {
        state.turns.push({ kind: "notice", key: `${run}:finished:${ev.seq}`, run_id: run, tone: status === "failed" ? "error" : "info",
          text: status === "failed" ? "The run failed and has nothing to resume." : plainWords(`The run stopped before a full report${d.stop_reason ? `: ${str(d.stop_reason)}` : ""}. The working picture as it stood is below.`) });
      }
      break;
    }
    default:
      break;
  }
  return state;
}

export function fold(events: readonly ChatEvent[], from: ChatState = initial()): ChatState {
  return events.reduce(apply, from);
}

/** The agent's own words where they reach the screen, in the v4 vocabulary (docs/v4-plan.md 6.2): the agent's
 * prompts and mechanical strings keep theirs, because changing them changes what the agent reads. */
export function plainWords(text: string): string {
  return text
    .replace(/\bthe incident commander\b/gi, "the Major Incident Manager").replace(/\bincident commander\b/gi, "Major Incident Manager")
    .replace(/\breads\b/g, "checks").replace(/\bReads\b/g, "Checks").replace(/\bread\b(?!-only)/g, "check").replace(/\bRead\b(?!-only)/g, "Check");
}

const plural = (n: number, one: string) => `${n} ${n === 1 ? one : `${one}s`}`;

// -- what the screens read from the state --------------------------------------------------------------------

/** Checks the agent proposed, ran, the person rejected and skipped (7.5). */
export function tally(state: ChatState): { proposed: number; run: number; rejected: number; skipped: number } {
  const rows = Object.values(state.rows).filter((r) => r.kind === "read" && r.by === "agent");
  return {
    proposed: rows.length,
    run: rows.filter((r) => r.result?.status === "ok").length,
    rejected: Object.values(state.rows).filter((r) => r.state === "rejected").length,
    skipped: Object.values(state.rows).filter((r) => r.state === "skipped" && r.who === "human").length,
  };
}

/** The overview panel's step list: the agent's checks, ✓ done, › running, ○ waiting. */
export function steps(state: ChatState): Step[] {
  return Object.values(state.rows)
    .filter((r) => r.by === "agent" && r.kind === "read")
    .map((r) => ({
      call_id: r.call_id, tool: r.tool,
      state: r.result ? (r.result.status === "ok" ? "done" : "failed") : r.running || r.state === "approved" || r.state === "auto" || r.state === "edited" ? "running" : "queued",
      text: short(r.result ? `${r.result.status === "error" ? "Error: " : ""}${r.result.first_line}` : r.rationale?.tests ?? "", 140),
    }));
}

export function short(text: string, max: number): string {
  return text.length <= max ? text : `${text.slice(0, max - 1).trimEnd()}…`;
}

export function leading(report: Report | null): Hypothesis | null {
  return report?.hypotheses.find((h) => h.status === "leading") ?? null;
}

/** Each hypothesis's first confidence on the board, where its bar's tick goes. */
export function startedAt(state: ChatState, h: Hypothesis): number | undefined {
  const first = state.board[h.id]?.first;
  if (typeof first === "number") return first;
  return h.history.length ? h.history[0]!.confidence : undefined;
}
