"use client";

import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import { Button, Handle, ToolResult } from "@/components/ds";
import { answerGate } from "@/lib/actions/chats";
import { readCall } from "@/lib/actions/reads";
import type { BoardChange, Group, Pending, Row } from "@/lib/chat/reduce";
import type { CallView } from "@/lib/types";

export interface ToolSchema {
  name: string;
  description: string;
  parameters: { properties: Record<string, ParamSchema>; required?: string[] };
}

interface ParamSchema {
  type?: string;
  enum?: string[];
  items?: { type?: string; $ref?: string };
  description?: string;
  minimum?: number;
  maximum?: number;
}

type Local = { state: "approved" | "edited" | "skipped" | "rejected"; reason?: string; args?: Record<string, unknown> };

// Generic reasons a commander gives for not running a read; a reason is still required, and these only fill it.
const QUICK = ["Already known; not needed", "Too broad; narrow it first", "Not relevant to this incident"];

// The auto-approve switch, turned on while a gate waits, approves the reads nobody has decided yet. Decisions the
// commander already made on that gate stand: the gate sends them first, with the undecided reads approved, and only
// then does the switch reach the API, whose standing answer would otherwise approve every card on the gate.
const standing = new Map<string, () => Promise<void>>();

export async function answerRestFirst(chatId: string): Promise<void> {
  await standing.get(chatId)?.();
}

type Answers = Parameters<typeof answerGate>[2];

function answersOf(asked: Row[], local: Record<string, Local>): Answers {
  return Object.fromEntries(asked.map((r) => {
    const d = local[r.call_id]!;
    if (d.state === "approved") return [r.call_id, { approve: true }];
    if (d.state === "edited") return [r.call_id, { approve: true, args: d.args }];
    if (d.state === "skipped") return [r.call_id, { defer: true }];
    return [r.call_id, { approve: false, reason: d.reason }];
  }));
}

const typing = (e: React.KeyboardEvent) => {
  const t = (e.target as HTMLElement)?.tagName;
  return t === "INPUT" || t === "TEXTAREA" || t === "SELECT";
};

function chips(args: Record<string, unknown>): { k: string; v: string }[] {
  return Object.entries(args).map(([k, v]) => ({
    k,
    v: Array.isArray(v) ? v.map((x) => (typeof x === "string" ? (/\s/.test(x) ? `"${x}"` : x) : JSON.stringify(x))).join(", ")
      : typeof v === "string" ? (/\s/.test(v) ? `"${v}"` : v) : JSON.stringify(v),
  }));
}

function query(row: Row): string {
  return row.native_query ?? JSON.stringify(row.args, null, 2);
}

/** After any decision, focus goes to the waiting gate's target Approve, unless the person is typing somewhere or
 * focus is already in the gate (section 4, Keys). */
export function refocus(): void {
  setTimeout(() => {
    const g = document.getElementById("pending");
    if (!g) return;
    const a = document.activeElement as HTMLElement | null;
    if (a && g.contains(a) && a !== g) return;
    if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA" || a.tagName === "SELECT")) return;
    (g.querySelector<HTMLElement>("[data-kf] button") ?? g).focus();
  }, 30);
}

// -- the edit form ---------------------------------------------------------------------------------------------

function initialValues(args: Record<string, unknown>, schema: ToolSchema | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [name, p] of Object.entries(schema?.parameters.properties ?? {})) {
    const v = args[name];
    if (v === undefined) out[name] = "";
    else if (p.type === "array" && p.items?.type === "string" && Array.isArray(v)) out[name] = v.join(", ");
    else if (typeof v === "string") out[name] = v;
    else out[name] = JSON.stringify(v);
  }
  return out;
}

function parsed(values: Record<string, string>, schema: ToolSchema | undefined): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [name, p] of Object.entries(schema?.parameters.properties ?? {})) {
    const raw = (values[name] ?? "").trim();
    if (!raw) continue;
    if (p.type === "integer") out[name] = Number.parseInt(raw, 10);
    else if (p.type === "number") out[name] = Number(raw);
    else if (p.type === "boolean") out[name] = raw === "true";
    else if (p.type === "array" && p.items?.type === "string") out[name] = raw.split(",").map((s) => s.trim()).filter(Boolean);
    else if (p.type === "array" || p.type === "object") {
      try { out[name] = JSON.parse(raw); } catch { out[name] = raw; }
    } else out[name] = raw;
  }
  return out;
}

// -- one round of reads: a waiting gate, or its receipts ---------------------------------------------------------

export function GroupView({ chatId, group, rows, pending, schemas, onOpen, onDecided, onRefused }: {
  chatId: string; group: Group; rows: Record<string, Row>; pending: Pending | null; schemas: ToolSchema[];
  onOpen: (handle: string) => void; onDecided?: () => void; onRefused?: (message: string) => void;
}) {
  const calls = group.calls.map((id) => rows[id]).filter((r): r is Row => Boolean(r));
  const waitingHere = pending && calls.some((r) => pending.cards.some((c) => c.call_id === r.call_id));
  if (!calls.length && !group.board.length) return null;
  return waitingHere
    ? <WaitingGate key={pending!.interrupt_id} chatId={chatId} rows={calls} pending={pending!} schemas={schemas} onOpen={onOpen} onDecided={onDecided}
        onRefused={onRefused} board={group.board} />
    : <Settled chatId={chatId} rows={calls} board={group.board} onOpen={onOpen} />;
}

/** A read that was let through and came back as an error: its arguments did not run, or the tool failed. */
const failedRow = (r: Row) => r.result?.status === "error";

function title(rows: Row[], local: Record<string, Local> = {}): string {
  const reads = rows.length;
  const state = (r: Row) => local[r.call_id]?.state ?? r.state;
  const approved = rows.filter((r) => ["approved", "auto", "edited"].includes(state(r)));
  const failed = approved.filter((r) => failedRow(r)).length;     // approved, then answered with an error
  const ran = approved.length - failed;
  const rej = rows.filter((r) => ["rejected", "refused"].includes(state(r))).length;
  const sk = rows.filter((r) => state(r) === "skipped").length;
  const noun = rows.every((r) => r.kind === "action") ? (reads === 1 ? "action" : "actions") : reads === 1 ? "read" : "reads";
  return `${reads} ${noun} · ${[ran ? `${ran} run` : "", failed ? `${failed} failed` : "", rej ? `${rej} rejected` : "", sk ? `${sk} skipped` : ""]
    .filter(Boolean).join(", ") || "decided"}`;
}

function Settled({ chatId, rows, board, onOpen }: { chatId: string; rows: Row[]; board: BoardChange[]; onOpen: (h: string) => void }) {
  if (!rows.length) return <BoardLines board={board} />;
  return (
    <section className="gg" role="group" aria-label={title(rows)}>
      <div className="gg-head"><span className="gg-title">{title(rows)}</span></div>
      {rows.map((r) => <Receipt key={r.call_id} chatId={chatId} row={r} onOpen={onOpen} />)}
      <BoardLines board={board} />
    </section>
  );
}

function BoardLines({ board }: { board: BoardChange[] }) {
  if (!board.length) return null;
  return (
    <div className="rcpt" style={{ flexDirection: "column", alignItems: "stretch", gap: 2 }}>
      {board.map((c) => (
        <span key={c.id} className="small">
          <span className="label" style={{ marginRight: 6 }}>Board</span>
          <span className="tool">{c.id}</span>{" "}
          {c.from ? <span className="time">{c.from.status} {c.from.confidence.toFixed(2)} → </span> : <span className="small muted">new, </span>}
          <span className="time">{c.to.status} {c.to.confidence.toFixed(2)}</span>
          <span className="muted"> · {c.statement}</span>
        </span>
      ))}
    </div>
  );
}

export function receiptText(r: Row, local?: Local): string {
  const s = local?.state ?? r.state;
  if (s === "skipped") return r.who === "policy" ? `Deferred by policy${r.reason ? `: ${r.reason}` : ""}.` : "Skipped. Smalls carried on without it.";
  if (s === "rejected") return `Rejected by you: “${local?.reason ?? r.reason ?? ""}”`;
  if (s === "refused") return `Refused by policy: ${r.reason ?? "not run"}.`;
  if (local && !r.result) return "Decided. Waiting for the other reads on this gate.";
  const prefix = s === "auto" ? (r.who === "policy" ? "Approved by policy. " : "Auto-approved. ") : s === "edited" ? "Approved with your edits. " : "";
  if (r.result) return `${prefix}${r.result.status === "ok" ? r.result.first_line : `Error: ${r.result.first_line}`}`;
  return `${prefix}${r.running ? "Running…" : "Queued."}`;
}

function Receipt({ chatId, row, local, onOpen }: { chatId: string; row: Row; local?: Local; onOpen: (h: string) => void }) {
  const [open, setOpen] = useState(false);
  const [call, setCall] = useState<CallView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const s = local?.state ?? row.state;
  const ran = ["approved", "auto", "edited"].includes(s) && row.result;
  const no = s === "rejected" || s === "refused" || failedRow(row);
  const glyph = s === "skipped" ? "–" : no ? "✕" : "✓";
  const cls = no ? "g-no" : s === "skipped" ? "g-skip" : "g-ok";
  const handle = row.result?.shown[0];
  async function toggle() {
    if (!open && !call) {
      const r = await readCall(chatId, row.call_id, row.run_id);
      if (r.call) setCall(r.call); else setError(r.error ?? "The result could not be read.");
    }
    setOpen(!open);
  }
  return (
    <>
      <div className="rcpt" data-call={row.call_id}>
        <span className={cls} aria-hidden="true">{glyph}</span>
        <span className="tool">{row.tool}</span>
        <span className="rtext">{receiptText(row, local)}</span>
        {ran && handle ? <Handle id={handle} onOpen={onOpen} /> : null}
        {ran ? <button type="button" className="linkbtn" onClick={toggle} aria-expanded={open}>{open ? "Hide result" : "Show result"}</button> : null}
      </div>
      {open ? (
        <div style={{ padding: "0 14px 12px" }}>
          {call ? (
            <ToolResult tool={row.tool} onOpenHandle={onOpen} result={{
              id: call.call_id, status: call.status === "ok" ? "ok" : "error", native_query: call.native_query,
              text: call.rows.length ? call.rows.map((x) => x.text).join("\n") : row.result?.first_line ?? "",
              window: call.window ? { since: call.window[0], until: call.window[1] } : null, handles: call.shown,
            }} />
          ) : <p className="small muted" role={error ? "alert" : undefined}>{error ?? "Loading…"}</p>}
        </div>
      ) : null}
    </>
  );
}

// -- the gate waiting on the commander -----------------------------------------------------------------------

function WaitingGate({ chatId, rows, pending, schemas, onOpen, onDecided, onRefused, board }: {
  chatId: string; rows: Row[]; pending: Pending; schemas: ToolSchema[]; onOpen: (h: string) => void; onDecided?: () => void;
  onRefused?: (message: string) => void; board: BoardChange[];
}) {
  const [local, setLocal] = useState<Record<string, Local>>({});
  const [mode, setMode] = useState<Record<string, "edit" | "reject" | null>>({});
  const [reason, setReason] = useState<Record<string, string>>({});
  const [values, setValues] = useState<Record<string, Record<string, string>>>({});
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const [kfocus, setKfocus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, start] = useTransition();
  const sent = useRef(false);
  const asked = rows.filter((r) => pending.cards.some((c) => c.call_id === r.call_id));
  const waiting = asked.filter((r) => !local[r.call_id]);
  const batch = waiting.length > 1;
  const target = waiting.find((r) => r.call_id === kfocus) ?? waiting[0] ?? null;
  const schemaOf = (tool: string) => schemas.find((s) => s.name === tool);
  const editable = (r: Row) => r.kind === "read" && Boolean(schemaOf(r.tool));

  const latest = useRef({ asked, local });
  useEffect(() => { latest.current = { asked, local }; });

  const send = useCallback(async (answers: Answers) => {
    const result = await answerGate(chatId, pending.interrupt_id, answers);
    if (!result.ok) {
      const message = result.status === 409 ? "This gate was already answered, in another tab or by the switch. The run went on with that answer."
        : result.error ?? "The answer was not taken.";
      setError(message);
      onRefused?.(message);           // shown by the chat too: the gate settles as the first answer lands
      if (result.status !== 409) { sent.current = false; setLocal({}); }
    }
    onDecided?.();
  }, [chatId, pending.interrupt_id, onDecided, onRefused]);

  // Every card has an answer: send them all at once. The API takes the first answer and refuses any other.
  useEffect(() => {
    if (sent.current || !asked.length || asked.some((r) => !local[r.call_id])) return;
    sent.current = true;
    const answers = answersOf(asked, local);
    start(async () => { await send(answers); });
  }, [local, asked, send]);

  // The switch turned on: approve the reads still undecided and send them with the decisions already made. With
  // nothing decided yet, the switch's own answer covers the gate; with an action undecided, the gate keeps waiting.
  useEffect(() => {
    const flush = async () => {
      const { asked, local } = latest.current;
      const rest = asked.filter((r) => !local[r.call_id]);
      if (sent.current || rest.length === asked.length || rest.some((r) => r.kind !== "read")) return;
      const all: Record<string, Local> = { ...local, ...Object.fromEntries(rest.map((r) => [r.call_id, { state: "approved" as const }])) };
      sent.current = true;
      setLocal(all);
      await send(answersOf(asked, all));
    };
    standing.set(chatId, flush);
    return () => { if (standing.get(chatId) === flush) standing.delete(chatId); };
  }, [chatId, send]);

  useEffect(() => { refocus(); }, []);

  const decide = useCallback((id: string, d: Local) => {
    setLocal((l) => (l[id] ? l : { ...l, [id]: d }));
    setMode((m) => ({ ...m, [id]: null }));
    refocus();
  }, []);
  const all = (state: "approved" | "skipped") => {
    setLocal((l) => ({ ...l, ...Object.fromEntries(waiting.map((r) => [r.call_id, { state }])) }));
    setMode({});
    refocus();
  };
  const cancel = (id: string) => { setMode((m) => ({ ...m, [id]: null })); setKfocus(id); refocus(); };
  const edit = (r: Row) => {
    setValues((v) => (v[r.call_id] ? v : { ...v, [r.call_id]: initialValues(r.args, schemaOf(r.tool)) }));
    setMode((m) => ({ ...m, [r.call_id]: "edit" }));
  };
  const approveEdited = (r: Row) => decide(r.call_id, { state: "edited", args: parsed(values[r.call_id] ?? {}, schemaOf(r.tool)) });
  const confirmReject = (r: Row) => { const text = (reason[r.call_id] ?? "").trim(); if (text) decide(r.call_id, { state: "rejected", reason: text }); };

  function rowKey(r: Row, e: React.KeyboardEvent) {
    if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey || e.repeat) return;
    const k = e.key.toLowerCase();
    const m = mode[r.call_id];
    if (k === "escape") { if (m) { e.preventDefault(); cancel(r.call_id); } return; }
    if (m || typing(e) || local[r.call_id]) return;
    if (k === "a") { e.preventDefault(); decide(r.call_id, { state: "approved" }); }
    else if (k === "e" && editable(r)) { e.preventDefault(); edit(r); }
    else if (k === "s") { e.preventDefault(); decide(r.call_id, { state: "skipped" }); }
    else if (k === "r") { e.preventDefault(); setMode((x) => ({ ...x, [r.call_id]: "reject" })); }
  }
  function gateKey(e: React.KeyboardEvent) {
    if (!waiting.length || e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || e.repeat || typing(e)) return;
    const k = e.key.toLowerCase();
    if (e.shiftKey) {
      if (k === "a") { e.preventDefault(); all("approved"); }
      else if (k === "s") { e.preventDefault(); all("skipped"); }
      return;
    }
    const inRow = (e.target as HTMLElement)?.closest?.(".row");
    if (!inRow && target) rowKey(target, e);
  }

  const w = waiting.length;
  const isAction = (r: Row) => r.kind === "action";
  const [one, many] = asked.every(isAction) ? ["action", "actions"] : ["read", "reads"];
  const head = w ? `◆ ${w === 1 ? `1 ${one} needs` : `${w} ${many} need`} your approval` : title(asked, local);
  return (
    <section className={w ? "gg wait" : "gg"} role="group" id={w ? "pending" : undefined} aria-label={head} onKeyDown={gateKey} tabIndex={-1}>
      <div className="gg-head">
        <span className="gg-title">{head}</span>
        {w ? <span className="gg-sub">{asked.every(isAction) ? "Nothing is executed until you choose; an action here is a proposal for you to act on." : "Read-only. Nothing runs until you choose."}</span> : null}
        {w ? (
          <span className="keys" aria-hidden="true">
            <span><kbd>A</kbd>approve</span>
            {waiting.some(editable) ? <span><kbd>E</kbd>edit</span> : null}
            <span><kbd>S</kbd>skip</span>
            <span><kbd>R</kbd>reject</span>
            {batch ? <span><kbd>⇧A</kbd>{w === 2 ? "approve both" : "approve all"}</span> : null}
          </span>
        ) : null}
      </div>
      {error ? <div className="rcpt" role="alert" style={{ color: "var(--danger)" }}>{error}</div> : null}
      {asked.map((r) => {
        if (local[r.call_id] || submitting) return <Receipt key={r.call_id} chatId={chatId} row={r} local={local[r.call_id]} onOpen={onOpen} />;
        const m = mode[r.call_id] ?? null;
        const schema = schemaOf(r.tool);
        return (
          <div className="row" key={r.call_id} onKeyDown={(e) => rowKey(r, e)} tabIndex={-1} data-call={r.call_id}>
            <div className="row-main">
              <div className="row-top"><span className="tool">{r.tool}</span><span className="reason">{r.by === "harness" ? "Part of the opening sweep, run before Smalls’ first turn." : <>{r.rationale?.tests}{r.rationale?.expect ? <span className="muted"> · expects {r.rationale.expect}</span> : null}</>}</span></div>
              <div className="chips">
                {chips(r.args).map((c) => <span className="chip" key={c.k}><i>{c.k}</i> {c.v}</span>)}
                <button type="button" className="linkbtn" onClick={() => setShown((s) => ({ ...s, [r.call_id]: !s[r.call_id] }))} aria-expanded={Boolean(shown[r.call_id])}>
                  {shown[r.call_id] ? "Hide query" : "Show query"}
                </button>
              </div>
              {shown[r.call_id] ? <pre className="sm-pre">{query(r)}{r.window ? `\nwindow ${r.window[0]} to ${r.window[1]}` : ""}</pre> : null}
              {m === "edit" ? (
                <div className="sub-form">
                  <span className="label">Change the parameters before it runs</span>
                  {Object.entries(schema?.parameters.properties ?? {}).map(([name, p], i) => (
                    <label key={name} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <span className="time muted">{name}{p.type === "array" && p.items?.type === "string" ? " (comma-separated)" : ""}</span>
                      {p.enum ? (
                        <select className="sm-input" style={{ maxWidth: 360 }} value={values[r.call_id]?.[name] ?? ""} autoFocus={i === 0}
                          onChange={(e) => setValues((v) => ({ ...v, [r.call_id]: { ...v[r.call_id], [name]: e.target.value } }))}
                          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); approveEdited(r); } else if (e.key === "Escape") { e.preventDefault(); cancel(r.call_id); } }}>
                          <option value="">(left out)</option>
                          {p.enum.map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                      ) : (
                        <input className="sm-input" style={{ maxWidth: 360, fontFamily: "var(--font-mono)" }} value={values[r.call_id]?.[name] ?? ""} autoFocus={i === 0}
                          inputMode={p.type === "integer" || p.type === "number" ? "numeric" : undefined}
                          onChange={(e) => setValues((v) => ({ ...v, [r.call_id]: { ...v[r.call_id], [name]: e.target.value } }))}
                          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); approveEdited(r); } else if (e.key === "Escape") { e.preventDefault(); cancel(r.call_id); } }} />
                      )}
                    </label>
                  ))}
                  <div className="acts">
                    <Button variant="primary" onClick={() => approveEdited(r)}>Approve with edit</Button>
                    <Button variant="quiet" onClick={() => cancel(r.call_id)} title="Cancel (Esc)">Cancel</Button>
                    <span className="sub-keys" aria-hidden="true"><kbd>Enter</kbd> approve with edit · <kbd>Esc</kbd> cancel</span>
                  </div>
                </div>
              ) : null}
              {m === "reject" ? (
                <div className="sub-form">
                  <span className="label">Why not? Smalls reads your reason and takes another path</span>
                  <div className="chips">
                    {QUICK.map((q) => <button key={q} type="button" className="fchip" onClick={() => setReason((x) => ({ ...x, [r.call_id]: q }))}>{q}</button>)}
                  </div>
                  <input className="sm-input" value={reason[r.call_id] ?? ""} autoFocus placeholder="Your reason, in a sentence" aria-label="Reason for rejecting"
                    onChange={(e) => setReason((x) => ({ ...x, [r.call_id]: e.target.value }))}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); confirmReject(r); } else if (e.key === "Escape") { e.preventDefault(); cancel(r.call_id); } }} />
                  <div className="acts">
                    <Button variant="danger" onClick={() => confirmReject(r)} disabled={!(reason[r.call_id] ?? "").trim()}>Reject this {r.kind === "action" ? "action" : "read"}</Button>
                    <Button variant="quiet" onClick={() => cancel(r.call_id)} title="Cancel (Esc)">Cancel</Button>
                    <span className="sub-keys" aria-hidden="true"><kbd>Enter</kbd> reject · <kbd>Esc</kbd> cancel</span>
                  </div>
                </div>
              ) : null}
            </div>
            {!m ? (
              <div className="row-acts" data-kf={target?.call_id === r.call_id ? "1" : undefined}>
                <Button variant={batch ? "secondary" : "primary"} onClick={() => decide(r.call_id, { state: "approved" })} title="Approve (A)" aria-keyshortcuts="A">Approve</Button>
                {editable(r) ? <Button variant="quiet" onClick={() => edit(r)} title="Edit (E)" aria-keyshortcuts="E">Edit</Button> : null}
                <Button variant="quiet" onClick={() => decide(r.call_id, { state: "skipped" })} title="Skip (S)" aria-keyshortcuts="S">Skip</Button>
                <Button variant="danger" onClick={() => setMode((x) => ({ ...x, [r.call_id]: "reject" }))} title="Reject (R)" aria-keyshortcuts="R">Reject…</Button>
              </div>
            ) : null}
          </div>
        );
      })}
      {batch && !submitting ? (
        <div className="gg-foot">
          <Button variant="primary" onClick={() => all("approved")} title={`${w === 2 ? "Approve both" : `Approve all ${w}`} (Shift+A)`} aria-keyshortcuts="Shift+A">{w === 2 ? "Approve both" : `Approve all ${w}`}</Button>
          <Button variant="quiet" onClick={() => all("skipped")} title={`${w === 2 ? "Skip both" : "Skip all"} (Shift+S)`} aria-keyshortcuts="Shift+S">{w === 2 ? "Skip both" : "Skip all"}</Button>
          <span className="small muted">or decide each one above</span>
        </div>
      ) : null}
      <BoardLines board={board} />
    </section>
  );
}
