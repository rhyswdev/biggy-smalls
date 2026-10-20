// The shapes the web app reads from the API. The request bodies are generated (api-types.ts); these responses
// are plain objects on the API side, so they are written here, as the API and the agent's event protocol send them.

export interface Capabilities {
  mode: "live" | "scripted";
  checkpoint: string;
  now: string;
  profile: string;
  drafter: string;
  seams: Record<string, boolean>;
  last_recorded: RecordedRun | null;
}

export interface RecordedRun {
  run_id: string;
  created: string | null;
  profile: string | null;
  wall_s: number | null;
  reads: number;
  tool_calls: number | null;
  model_calls: number | null;
  gate: Record<string, number> | null;
  cost_usd: number | null;
}

export interface IncidentCard {
  incident: {
    number: string | null;
    short_description: string | null;
    priority: string | null;
    priority_code: string | null;
    state: string | null;
    impact: string | null;
    urgency: string | null;
    service: string | null;
    group: string | null;
    ci: string | null;
    assignee: string | null;
    handle: string;
    opened: string;
    updated: string;
  } | null;
  note: { author: string | null; role: string | null; time: string | null; text: string };
  found_by: string;
  trigger: string;
}

export type ChatOrigin = "chat" | "headless" | "blank" | "past";
export type ChatStatus = "running" | "waiting" | "stopped" | "failed" | "resolved" | "finished";

export interface ChatSummary {
  id: string;
  origin: ChatOrigin;
  title: string;
  incident: string | null;
  now: string;
  created: string;
  auto_approve: boolean;
  status: ChatStatus;
  recorded: string | null;
  pending: number;
  tally: { proposed: number; run: number; rejected: number; skipped: number };
  recorded_run?: RecordedRun | null;
}

export interface RunRow {
  run_id: string;
  chat_id: string;
  trigger: string;
  status: "running" | "paused" | "completed" | "incomplete" | "stopped" | "failed";
  started: string;
  finished: string | null;
  folder: string | null;
}

export interface Card {
  call_id: string;
  tool: string;
  kind: "read" | "action" | string;
  by: string;
  args: Record<string, unknown>;
  rationale: { tests: string; expect: string } | null;
  native_query?: string | null;
  window?: [string, string] | null;
}

export interface Gate {
  chat_id: string;
  run_id: string;
  interrupt_id: string;
  round: number | null;
  cards: Card[];
  asked_at: string;
  decided_at: string | null;
}

export interface ChatEvent {
  seq: number;
  run_id: string;
  agent_seq: number;
  type: string;
  data: Record<string, unknown>;
  t_wall: string;
}

export interface ChatView {
  chat: ChatSummary;
  runs: RunRow[];
  events: ChatEvent[];
  pending_gate: Gate | null;
  notes: { handle: string; text: string; submitted_at: string; delivered_at: string | null; run_id: string | null }[];
}

// -- the report, as the agent's contract has it (smalls/contracts/report.py) ---------------------------------

export interface Hypothesis {
  id: string;
  statement: string;
  role: "cause" | "trigger" | "contributing" | "symptom" | "unrelated" | string;
  status: "leading" | "open" | "rejected";
  subject: string;
  entities: string[];
  changes: string[];
  confidence: number;
  support: string[];
  against: string[];
  source: "stated" | "derived";
  history: { as_of: string; status: string; confidence: number; because: string[] }[];
}

export interface Report {
  as_of: string;
  summary: string;
  impact: {
    business_service: string; cis: string[]; priority: { suggested: string; impact: string; urgency: string };
    assignment_group: string; oncall: string; customer: string;
  } | null;
  hypotheses: Hypothesis[];
  timeline: { t_utc: string; event: string; evidence: string[] }[];
  actions: { rank: number; action: string; owner: string; evidence: string[] }[];
  do_not: { action: string; why: string; evidence: string[] }[];
  alerts: { handle: string; verdict: "this_incident" | "other_incident" | "noise" | "unassessed" }[];
  open_questions: string[];
  stakeholder_update: {
    status: string; customer_impact: string; scope: string; changed_since_last: string; next_steps: string; next_update_at: string;
  } | null;
  proposed_actions: {
    call_id: string; tool: string; args: Record<string, unknown>; rationale: { tests: string; expect: string } | null;
    decision: "approved" | "rejected" | "deferred"; by: { who: string; via: string }; reason: string | null; decided_at: string | null;
  }[];
  validation: { ok: boolean; attempts: number; dropped: string[]; flagged: string[]; continuity: string | null; verifier: string | null; disputed: string[] } | null;
}

export interface CallView {
  call_id: string;
  run_id: string;
  tool: string;
  kind: string;
  by: string;
  args: Record<string, unknown>;
  edited_args: Record<string, unknown> | null;
  rationale: { tests: string; expect: string } | null;
  status: string;
  shown: string[];
  referenced: string[];
  native_query: string | null;
  window: [string, string] | null;
  rows: { handle: string; t_known: string | null; text: string }[];
}

export interface EvidenceView {
  handle: string;
  class: "record" | "chat" | "aggregate" | "metric" | string;
  record: {
    handle: string; source: string | null; dataset?: string; kind: string | null; title?: string | null;
    event_utc: string | null; planned_utc?: string | null; known_utc: string | null; vendor_time: string | null; zone: string | null;
    path?: string; version?: number; native: string; format: "json" | "text"; withheld: string[]; parent: string | null; children: string[];
  } | null;
  shown: { run_id: string; call_id: string; t_known: string | null; text: string }[];
  note: { handle: string; text: string; submitted_at: string } | null;
  query: { tool: string; native_query: string | null; window: [string, string] | null; ends_at_now: boolean } | null;
  referenced_by: string[];
  claims: { claim: string; as: string; text: string }[];
}

// -- stakeholder messages (U8) ---------------------------------------------------------------------------------

export interface MessageRow {
  audience: "exec" | "support" | "status" | "eng";
  draft: string;
  text: string;
  copied: boolean;
  drafted_by: string;           // the drafter's profile, or "code"
  why_code: string | null;      // why code wrote it when a model was to
  cost_usd: number | null;
  drafted_at: string;
}

export interface MessagesView {
  drafter: string;
  messages: MessageRow[];
  entry: { title: string; body: string; terms: string; saved_at: string } | null;
}

export interface KbHit { id: number; chat_id: string; title: string; terms: string; saved_at: string; snippet?: string; score?: number }

// -- the service desk (v4 plan 5.2, 5.5) ---------------------------------------------------------------------

/** An incident as the header, the sidebar and the lists show it: its own fields at `now`. */
export interface IncidentSummary {
  number: string;
  short_description: string | null;
  priority: string | null;
  priority_code: string | null;
  state: string | null;
  impact: string | null;
  urgency: string | null;
  service: string | null;
  group: string | null;
  ci: string | null;
  assignee: string | null;
  handle: string;
  opened: string;
  updated: string;
  major: boolean;
  active: boolean;
}

/** An imported incident, with its diagnosis chat. */
export interface ImportedIncident extends IncidentSummary {
  chat_id: string;
  imported_at: string;
  status: ChatStatus;
  next_update_due: string | null;
  resolution: { resolved_at: string; resolved_by: string } | null;
}

export interface OpenIncidents {
  connected: boolean;
  incidents: (IncidentSummary & { imported: boolean; chat_id: string | null })[];
}

export interface TicketView {
  incident: IncidentSummary;
  zone: string;
  fields: {
    state: string | null; priority: string | null; impact: string | null; urgency: string | null; business_service: string | null;
    cmdb_ci: string | null; assignment_group: string | null; assigned_to: string | null;
  };
  opened: { utc: string; local: string };
  notes: { handle: string; author: string | null; user_name: string | null; utc: string; local: string; text: string }[];
}

export interface KbRow { id: number; chat_id: string; title: string; terms: string; saved_at: string }

/** An imported incident's overview (v4 plan 5.2): prose by the writer or by code, contacts by code. */
export interface Overview {
  whats_going_on: string;
  business_impact: string;
  affected_cis: { name: string; label: string; description: string; status: string; severity: "bad" | "warn" | "ok" | "unknown" }[];
  not_affected: string[];
  working_theories: { person: string; area: string; claim: string; tried: string }[];
  timeline: { time: string; event: string; impact_start: boolean }[];
  glossary: { term: string; meaning: string }[];
  contacts: { name: string; role: string; kind: "person" | "group"; phone: string | null }[];
  channels: string[];
  written_by: string;
  why_code: string | null;
}
