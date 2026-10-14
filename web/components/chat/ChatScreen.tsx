"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import { Button, Handles, Notice, Toggle } from "@/components/ds";
import { EvidenceDrawer } from "@/components/evidence/Drawer";
import { moreRun, sendNote, setAutoApprove, stopRun } from "@/lib/actions/chats";
import { suggestions, type CodeAnswer, type Suggestion } from "@/lib/chat/answers";
import { startedAt, type ChatState, type Turn } from "@/lib/chat/reduce";
import { useChatEvents } from "@/lib/events";
import { clock, day, plural, seconds, stamp } from "@/lib/format";
import type { ChatSummary, IncidentCard } from "@/lib/types";
import { AnswerCard } from "./AnswerCard";
import { GroupView, refocus, type ToolSchema } from "./Gate";
import { Linked } from "./Linked";
import { ResolveDialog } from "./ResolveDialog";
import { WorkingPicture } from "./WorkingPicture";

interface Props {
  chat: ChatSummary;
  initial: ChatState;
  schemas: ToolSchema[];
  card: IncidentCard | null;
  resolved: boolean;
  children?: React.ReactNode;      // the app bar, rendered on the server
}

type Local = { key: string; q: string; answer?: CodeAnswer; pending?: { handle: string | null; text: string; delivery: string } };

const DELIVERY: Record<string, string> = {
  "next round": "Sent. Smalls sees it at its next round.",
  "with the answer": "Held. It goes to Smalls with your answer to the reads above.",
  "follow-up": "Sent. Smalls picks the run up where it stopped.",
  resume: "Sent. The run goes on from where it stopped.",
};

export function ChatScreen({ chat, initial, schemas, card, resolved, children }: Props) {
  const router = useRouter();
  const params = useSearchParams();
  const past = chat.origin === "past";
  const finishedAtStart = ["completed", "incomplete", "failed"].includes(initial.status);
  const state = useChatEvents(chat.id, initial, !past);
  const [drawer, setDrawer] = useState<string | null>(null);
  const [local, setLocal] = useState<Local[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [auto, setAuto] = useState(chat.auto_approve);
  const [busy, start] = useTransition();
  const resolving = params.get("resolve") !== null;
  const open = useCallback((h: string) => setDrawer(h), []);
  const [used, setUsed] = useState<string[]>([]);

  const running = state.status === "running" || state.status === "idle";
  const finished = ["completed", "incomplete", "failed"].includes(state.status);
  const waiting = state.pending ? state.pending.cards.length : 0;
  const delivered = new Set(state.turns.flatMap((t) => (t.kind === "note" ? [t.handle] : [])));
  const shownLocal = local.filter((l) => !l.pending || !l.pending.handle || !delivered.has(l.pending.handle));
  const offers = useMemo(() => (finished || past ? suggestions(state.report, state.verifier?.checks ?? [], state.verifier?.disputed ?? []) : []), [finished, past, state.report, state.verifier]);
  const offered = offers.filter((s) => !used.includes(s.id) && (!past || s.kind === "code"));

  useEffect(() => { if (finishedAtStart !== finished && finished) router.refresh(); }, [finished, finishedAtStart, router]);
  useEffect(() => { if (state.pending) refocus(); }, [state.pending]);

  function pick(s: Suggestion) {
    setUsed((u) => [...u, s.id]);
    if (s.kind === "code" && s.answer) setLocal((l) => [...l, { key: `${s.id}:${l.length}`, q: s.q, answer: s.answer }]);
    else if (s.kind === "run" && s.note) send(s.note);
    refocus();
  }
  function send(text: string) {
    const note = text.trim();
    if (!note) return;
    setError(null);
    start(async () => {
      const r = await sendNote(chat.id, note);
      if (!r.ok) { setError(r.error ?? "The note was not sent."); return; }
      setDraft("");
      setLocal((l) => [...l, { key: `note:${l.length}`, q: note, pending: { handle: r.handle ?? null, text: note, delivery: r.delivery ?? "next round" } }]);
      refocus();
    });
  }
  function switchAuto(on: boolean) {
    setAuto(on);
    start(async () => { const r = await setAutoApprove(chat.id, on); if (!r.ok) { setAuto(!on); setError(r.error ?? "The switch did not change."); } });
  }

  const summary = state.finished;
  const reads = Object.values(state.rows).filter((r) => r.by === "agent" && r.kind === "read");
  const asked = reads.filter((r) => r.who === "human").length;
  return (
    <div className="v-chat" style={{ minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      {children}
      <div style={{ flex: "1 1 auto", display: "flex", flexWrap: "wrap", alignItems: "flex-start", maxWidth: 1440, width: "100%", margin: "0 auto", boxSizing: "border-box" }}>
        <main aria-label="Conversation" style={{ flex: "999 1 640px", minWidth: 0, display: "flex", flexDirection: "column", minHeight: "calc(100vh - 64px)" }}>
          <div className="convo">
            {chat.origin === "headless" || past ? (
              <div className="origin">
                <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: "1 1 360px", minWidth: 0 }}>
                  <span style={{ fontWeight: 600 }}>
                    {past ? `Recorded run · made ${day(chat.recorded)}` : "Agent run"}
                    {summary ? <> · finished in <span className="time">{seconds(summary.wall_s)}</span> · {plural(reads.length, "read")}, {asked ? `${asked} needed approval` : "none needed approval"}</> : running ? " · running" : null}
                  </span>
                  <span className="small muted">
                    {past ? "A live run, kept as it was recorded. Nothing here calls a model; " : "Started from the incident commander's note, unattended: reads ran without asking and any action was left for you. "}
                    Below is exactly what Smalls did.{past ? " It is read-only." : " Ask a follow-up to keep going."}
                  </span>
                </div>
                <Link className="lbtn" href="/">Back to dashboard</Link>
              </div>
            ) : null}
            {state.turns.map((t) => <TurnView key={t.key} turn={t} chat={chat} card={card} state={state} schemas={schemas} onOpen={open} resolved={resolved} onRefused={setError}
              onResolve={past ? undefined : () => router.push(`/chats/${chat.id}?resolve`, { scroll: false })} />)}
            {shownLocal.map((l) => (
              <div key={l.key} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <div className="msg-ic"><span className="who"><b>You</b> · incident commander</span><span style={{ fontSize: 15, lineHeight: "22px", whiteSpace: "pre-line" }}>{l.q}</span>
                  {l.pending ? <span className="small muted" aria-live="polite">{DELIVERY[l.pending.delivery] ?? DELIVERY["next round"]}</span> : null}</div>
                {l.answer ? (
                  <div className="msg-sm">
                    <span className="who"><b>Smalls</b> · {l.answer.source}</span>
                    {l.answer.text.map((p, i) => <p key={i} className="narr"><Linked text={p} onOpen={open} /></p>)}
                    {l.answer.handles.length ? <Handles ids={l.answer.handles} onOpen={open} /> : null}
                  </div>
                ) : null}
              </div>
            ))}
            {running && !state.pending ? (
              <p className="small muted" aria-live="polite">{state.heartbeat ? `Model working, ${state.heartbeat} s` : "Smalls is working…"}</p>
            ) : null}
          </div>
          <div className="dock">
            <div className="dock-in">
              {offered.length ? (
                <div className="chips">
                  <span className="label" style={{ marginRight: 4 }}>Ask next</span>
                  {offered.map((s) => <button key={s.id} type="button" className="fchip" onClick={() => pick(s)}>{s.q}</button>)}
                </div>
              ) : null}
              {error ? <Notice tone="error">{error}</Notice> : null}
              {past ? (
                <p className="small muted" style={{ margin: 0 }}>A recorded run is read-only. The suggested questions above are answered from its report; to investigate again, start a new chat from the dashboard.</p>
              ) : (
                <div className="composer">
                  <textarea className="sm-input" rows={2} aria-label="Message Smalls" placeholder={finished ? "Ask Smalls a follow-up" : "Add a note for Smalls"} value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !waiting) { e.preventDefault(); send(draft); } }} />
                  <div className="acts">
                    <Toggle checked={auto} onChange={switchAuto} label="Auto-approve reads" disabled={busy} />
                    <span className="grow" />
                    {waiting ? (
                      <a className="wait-link" href="#pending" onClick={(e) => { e.preventDefault(); document.getElementById("pending")?.scrollIntoView({ block: "center" }); refocus(); }}>
                        <span aria-hidden="true">◆ </span>{waiting === 1 ? "1 read is" : `${waiting} reads are`} waiting for you above
                      </a>
                    ) : <span className="small muted">{auto ? "Reads run without asking" : "Smalls asks before every read"}</span>}
                    {running ? <Button variant="quiet" onClick={() => start(async () => { await stopRun(chat.id); })}>Stop</Button> : null}
                    {state.status === "stopped" ? <Button onClick={() => start(async () => { await moreRun(chat.id, true); })}>Go on</Button> : null}
                    <Button variant="primary" onClick={() => send(draft)} disabled={Boolean(waiting) || busy || !draft.trim()}>Send</Button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </main>
        <WorkingPicture state={state} />
      </div>
      {drawer ? <EvidenceDrawer key={drawer} chatId={chat.id} handle={drawer} onClose={() => setDrawer(null)} /> : null}
      {resolving && !past ? <ResolveDialog chat={chat} card={card} report={state.report} onClose={() => router.replace(`/chats/${chat.id}`, { scroll: false })} /> : null}
    </div>
  );
}

function TurnView({ turn, chat, card, state, schemas, onOpen, onResolve, onRefused, resolved }: {
  turn: Turn; chat: ChatSummary; card: IncidentCard | null; state: ChatState; schemas: ToolSchema[]; onOpen: (h: string) => void; onResolve?: () => void;
  onRefused: (message: string) => void; resolved: boolean;
}) {
  const [all, setAll] = useState(false);
  switch (turn.kind) {
    case "ic": {
      if ((chat.origin === "headless" || chat.origin === "past") && card?.incident) return <Ticket card={card} text={turn.text} />;
      return (
        <div className="msg-ic">
          <span className="who"><b>{chat.origin === "blank" ? "You" : card?.note.author ?? "You"}</b> · incident commander · <span className="time">{clock(turn.at)} UTC</span></span>
          <span style={{ fontSize: 15, lineHeight: "22px", whiteSpace: "pre-line" }}>{body(turn.text)}</span>
        </div>
      );
    }
    case "note":
      return (
        <div className="msg-ic">
          <span className="who"><b>You</b> · {turn.followup ? "follow-up" : "note while it ran"} · <span className="handle">{turn.handle}</span></span>
          <span style={{ fontSize: 15, lineHeight: "22px", whiteSpace: "pre-line" }}>{turn.text}</span>
        </div>
      );
    case "group": {
      const paragraphs = (turn.narrative ?? "").split(/\n\s*\n/).filter(Boolean);
      return (
        <div className="msg-sm">
          {turn.by === "harness" ? <span className="who"><b>Smalls</b> · the opening sweep: what is alerting, what changed and what people are saying</span>
            : paragraphs.length || turn.text ? <span className="who"><b>Smalls</b></span> : null}
          {paragraphs.length ? (
            <>
              <p className="narr">{all ? null : paragraphs[0]}</p>
              {all ? paragraphs.map((p, i) => <p key={i} className="narr">{p}</p>) : null}
              {paragraphs.length > 1 ? <button type="button" className="linkbtn" style={{ alignSelf: "flex-start" }} onClick={() => setAll(!all)} aria-expanded={all}>{all ? "Show less" : "Show all of its reasoning"}</button> : null}
            </>
          ) : null}
          {turn.text ? <p className="narr"><Linked text={turn.text} onOpen={onOpen} /></p> : null}
          <GroupView chatId={chat.id} group={turn} rows={state.rows} pending={state.pending} schemas={schemas} onOpen={onOpen} onRefused={onRefused} />
        </div>
      );
    }
    case "answer":
      return (
        <div className="msg-sm">
          <span className="who"><b>Smalls</b> · answered from what it already had; the report stands</span>
          <p className="narr"><Linked text={turn.text} onOpen={onOpen} /></p>
        </div>
      );
    case "report": {
      const lead = turn.report.hypotheses.find((h) => h.status === "leading");
      return (
        <div className="msg-sm">
          <span className="who"><b>Smalls</b> · {turn.status === "completed" ? "report" : turn.status === "paused" ? "the board when it stopped" : "partial report"} · <span className="time">{clock(turn.report.as_of)} UTC</span></span>
          <AnswerCard report={turn.report} status={turn.status} first={lead ? startedAt(state, lead) : undefined} onOpen={onOpen}
            onResolve={turn.status === "completed" && turn.seq === lastReportSeq(state) ? onResolve : undefined} resolved={resolved} />
        </div>
      );
    }
    case "notice":
      return <Notice tone={turn.tone}>{turn.text}</Notice>;
  }
}

/** A note without its "From:" and "Time:" header lines, which the bubble's own header already shows. */
function body(text: string): string {
  const lines = text.split("\n");
  let i = 0;
  while (i < lines.length && /^(From|Time):\s/.test(lines[i]!.trim())) i++;
  return i ? lines.slice(i).join("\n").trim() : text;
}

function lastReportSeq(state: ChatState): number {
  const reports = state.turns.filter((t) => t.kind === "report");
  const last = reports[reports.length - 1];
  return last && last.kind === "report" ? last.seq : -1;
}

/** The incident as ServiceNow pushed it, in place of the commander's message when the run started on its own. */
function Ticket({ card, text }: { card: IncidentCard; text: string }) {
  const inc = card.incident!;
  const fields: [string, React.ReactNode][] = [
    ["State", inc.state], ["Priority", <span key="p" className="tkt-prio">{inc.priority}</span>], ["Impact / Urgency", `${inc.impact} / ${inc.urgency}`],
    ["Business service", inc.service], ["Configuration item", inc.ci], ["Assignment group", inc.group], ["Assigned to", inc.assignee],
    ["Opened", <span key="o" className="time">{clock(inc.opened, true)} UTC</span>],
  ];
  return (
    <article className="tkt" aria-label={`Incident ${inc.number}, pushed from ServiceNow`}>
      <div className="tkt-head">
        <span className="src-pill">Pushed from ServiceNow</span>
        <span className="small muted">Incident updated: work note added</span>
        <span className="grow" />
        <span className="time muted">updated {stamp(inc.updated)} UTC</span>
      </div>
      <div className="tkt-body">
        <div className="tkt-title"><span className="tkt-num">{inc.number}</span><span className="tkt-sd">{inc.short_description}</span></div>
        <dl className="tkt-fields">{fields.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v ?? "–"}</dd></div>)}</dl>
        <div className="tkt-notes">
          <span className="label">Work note that started this run</span>
          <div className="tkt-note trig">
            <div className="tkt-note-head"><b>{card.note.author ?? "Incident commander"}</b>{card.note.time ? <span className="time">{card.note.time}</span> : null}<span className="trig-tag">Triggered this run</span></div>
            <p>{body(text)}</p>
          </div>
        </div>
      </div>
    </article>
  );
}
