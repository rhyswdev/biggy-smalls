"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState, useTransition } from "react";
import { Button, Notice, Toggle } from "@/components/ds";
import { EvidenceDrawer } from "@/components/evidence/Drawer";
import { IncidentHeader } from "@/components/shell/IncidentHeader";
import { StatusBanner } from "@/components/shell/StatusBanner";
import { moreRun, sendNote, setAutoApprove, stopRun } from "@/lib/actions/chats";
import { linesFor } from "@/lib/chat/howWeGotHere";
import { noteHead } from "@/lib/chat/note";
import { leading, startedAt, tally, type ChatState, type Group, type Turn } from "@/lib/chat/reduce";
import { useChatEvents } from "@/lib/events";
import { clock, confidenceLabel, day, seconds, waitingText } from "@/lib/format";
import { sendKey } from "@/lib/keys";
import type { ChatSummary, IncidentSummary, Overview, TicketView } from "@/lib/types";
import { AnswerCard, SURE } from "./AnswerCard";
import { answerRestFirst, ApprovalTray, GroupView, refocus, type ToolSchema } from "./Gate";
import { HowSmallsGotHere } from "./HowSmallsGotHere";
import { Prose } from "./Prose";
import { ResolveDialog } from "./ResolveDialog";
import { SidePanel, type Panel } from "./SidePanel";

interface Props {
  chat: ChatSummary;
  initial: ChatState;
  schemas: ToolSchema[];
  incident: IncidentSummary | null;       // the ServiceNow incident the chat is about, when it names one
  ticket: TicketView | null;
  imported: boolean;                      // the chat is an imported incident's diagnosis
  nextUpdate: string | null;
  resolvable: boolean;
  overview: Overview | null;
}

type Local = { key: string; q: string; pending: { handle: string | null; text: string; delivery: string } };

const DELIVERY: Record<string, string> = {
  "next round": "Sent. Smalls sees it at its next round.",
  "with the answer": "Held. It goes to Smalls with your answer to the checks waiting.",
  "follow-up": "Sent. Smalls picks the run up where it stopped.",
  resume: "Sent. The run goes on from where it stopped.",
};

const PICTURE = <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="8" cy="8" r="5.5" /><circle cx="8" cy="8" r="2.5" /></svg>;
const CONTACTS = <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><circle cx="6" cy="5.5" r="2.5" /><path d="M1.5 13.5c.6-2.4 2.3-3.6 4.5-3.6s3.9 1.2 4.5 3.6" /><path d="M10.5 3.2a2.4 2.4 0 0 1 0 4.6" /><path d="M12 10.2c1.2.5 2 1.6 2.5 3.3" /></svg>;
const TICKET = <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 1.5h5.5L12.5 4.5v10h-8.5z" /><path d="M9.5 1.5v3h3" /><path d="M6 8h4.5M6 10.5h4.5" /></svg>;

/** The diagnosis (ChatV4): the incident's header with the panel toggles and a one-line status, the conversation,
 * the approval tray floating above the composer, and a side panel that is closed by default. */
export function ChatScreen({ chat, initial, schemas, incident, ticket, imported, nextUpdate, resolvable, overview }: Props) {
  const router = useRouter();
  const params = useSearchParams();
  const past = chat.origin === "past";
  const finishedAtStart = ["completed", "incomplete", "failed"].includes(initial.status);
  const state = useChatEvents(chat.id, initial, !past);
  const [drawer, setDrawer] = useState<string | null>(params.get("evidence"));      // ?evidence=<handle> opens a record directly
  const [panel, setPanel] = useState<Panel | null>(params.get("panel") === "ticket" && ticket ? "ticket" : params.get("panel") === "picture" ? "picture" : null);
  const [local, setLocal] = useState<Local[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [auto, setAuto] = useState(chat.auto_approve);
  const [busy, start] = useTransition();
  const resolving = params.get("resolve") !== null;
  const open = useCallback((h: string) => setDrawer(h), []);
  const toggle = (p: Panel) => setPanel((x) => (x === p ? null : p));

  const running = state.status === "running" || state.status === "idle";
  const finished = ["completed", "incomplete", "failed"].includes(state.status);
  const waiting = state.pending ? state.pending.cards.length : 0;
  const delivered = new Set(state.turns.flatMap((t) => (t.kind === "note" ? [t.handle] : [])));
  const shownLocal = local.filter((l) => !l.pending.handle || !delivered.has(l.pending.handle));

  const main = useRef<HTMLElement>(null);
  const { atEnd } = useFollow(running, main);
  const pin = useRef(false);      // the person just sent something: bring it into view, wherever they were

  useEffect(() => { if (finishedAtStart !== finished && finished) router.refresh(); }, [finished, finishedAtStart, router]);
  // The resolve button needs a report; the first one to arrive live asks the server for the page again.
  const reported = state.report !== null;
  useEffect(() => { if (reported && !initial.report) router.refresh(); }, [reported, initial.report, router]);
  useEffect(() => { if (state.pending) refocus(); }, [state.pending]);
  useEffect(() => { if (pin.current || atEnd.current) toEnd(main.current); pin.current = false; }, [local, atEnd]);

  function send(text: string) {
    const note = text.trim();
    if (!note) return;
    setError(null);
    start(async () => {
      const r = await sendNote(chat.id, note);
      if (!r.ok) { setError(r.error ?? "The note was not sent."); return; }
      setDraft("");
      pin.current = true;
      setLocal((l) => [...l, { key: `note:${l.length}`, q: note, pending: { handle: r.handle ?? null, text: note, delivery: r.delivery ?? "next round" } }]);
      refocus();
    });
  }
  function switchAuto(on: boolean) {
    setAuto(on);
    start(async () => {
      if (on) await answerRestFirst(chat.id);         // the decisions already made on a waiting gate stand
      const r = await setAutoApprove(chat.id, on);
      if (!r.ok) { setAuto(!on); setError(r.error ?? "The switch did not change."); }
    });
  }

  const resolved = chat.status === "resolved";
  const lead = leading(state.report);
  const sure = Boolean(lead && lead.confidence >= SURE);
  const action = state.report ? [...state.report.actions].sort((a, b) => a.rank - b.rank)[0] : undefined;
  const due = nextUpdate ? ` Next update due ${clock(nextUpdate)} UTC.` : "";
  const ran = tally(state).run;
  const inPlay = Object.values(state.board).filter((h) => h.status !== "rejected").length;
  const canResolve = resolvable && reported && !resolved;
  const sub = chat.origin === "headless" && state.finished
    ? `Diagnosis started automatically${imported && incident ? ` when ${incident.number} was imported` : ""} · ${seconds(state.finished.wall_s)} · ${ran === 1 ? "1 check" : `${ran} checks`}${auto ? "" : " · follow-up checks need your approval"}`
    : undefined;
  const resolveButton = (quiet: boolean) => canResolve
    ? <Link className={quiet ? "lbtn" : "lbtn primary"} href={`/chats/${chat.id}?resolve`} scroll={false}>Resolve incident…</Link> : null;
  const banner = resolved ? (
    <StatusBanner tone="ok" title="Resolved." text="The resolution review and the communications are open.">
      <Link className="lbtn" href={`/chats/${chat.id}/resolution`}>Open resolution</Link>
    </StatusBanner>
  ) : past ? (
    <StatusBanner tone="neutral" title={`Recorded run${chat.recorded ? ` · made ${day(chat.recorded)}` : ""}.`} text="A live run, kept as it was recorded. Nothing here calls a model, and it is read-only." />
  ) : waiting ? (
    <StatusBanner tone="attention" title="Waiting on you." text={`${waiting === 1 ? "1 check needs" : `${waiting} checks need`} your approval before diagnosis can continue.`} sub={sub}>
      <a className="now-link" href="#pending" onClick={(e) => { e.preventDefault(); refocus(true); }}>Go to approvals</a>
    </StatusBanner>
  ) : running ? (
    <StatusBanner tone="neutral" title="Diagnosis in progress." text={state.heartbeat ? `Smalls is working, ${state.heartbeat} s.` : "Smalls is running checks."}>{resolveButton(true)}</StatusBanner>
  ) : state.status === "stopped" ? (
    <StatusBanner tone="neutral" title="Diagnosis stopped." text="Go on from the composer, or ask a follow-up.">{resolveButton(true)}</StatusBanner>
  ) : state.status === "failed" ? (
    <StatusBanner tone="neutral" title="The run failed." text="Ask a follow-up or start a new chat." />
  ) : lead && sure ? (
    <StatusBanner tone="action" glyph="●" title="Probable cause identified." sub={sub}
      text={`${lead.statement} Confidence: ${confidenceLabel(lead.confidence)}.${action ? ` Recommended action: ${action.action}` : ""}${due}`}>{resolveButton(false)}</StatusBanner>
  ) : (
    <StatusBanner tone="neutral" glyph="○" title="No probable cause yet." sub={sub}
      text={`${lead ? `${lead.statement} is ahead at ${confidenceLabel(lead.confidence)}.` : "The checks so far did not settle it."}${due}`}>{resolveButton(false)}</StatusBanner>
  );
  const tools = (
    <div className="tools" role="group" aria-label="Side panel">
      <button type="button" className="tool-btn" aria-pressed={panel === "picture"} onClick={() => toggle("picture")} title="What Smalls believes right now">
        {PICTURE}Working picture{inPlay ? <span className="tool-n">{inPlay} in play</span> : null}
      </button>
      {overview?.contacts.length ? (
        <button type="button" className="tool-btn" aria-pressed={panel === "contacts"} onClick={() => toggle("contacts")} title="Who to talk to">
          {CONTACTS}Contacts<span className="tool-n">{overview.contacts.length}</span>
        </button>
      ) : null}
      {ticket ? <button type="button" className="tool-btn" aria-pressed={panel === "ticket"} onClick={() => toggle("ticket")} title="The ServiceNow ticket, as imported">{TICKET}Ticket</button> : null}
    </div>
  );

  // Once there is an answer, the working before it folds into "How Smalls got here".
  const firstReport = state.turns.findIndex((t) => t.kind === "report");
  const folded = firstReport >= 0 ? state.turns.slice(0, firstReport).filter((t): t is Group => t.kind === "group") : [];
  const checks = linesFor(folded, state.rows, state.outcomes, Object.fromEntries(Object.values(state.board).map((h) => [h.id, h.subject])));
  const turn = (t: Turn) => <TurnView key={t.key} turn={t} chat={chat} incident={incident} imported={imported} state={state} onOpen={open}
    checks={checks.filter((c) => c.status === "ran").length} onTicket={ticket ? () => setPanel("ticket") : undefined} />;
  const pendingRows = state.pending ? Object.values(state.rows).filter((r) => state.pending!.cards.some((c) => c.call_id === r.call_id)) : [];

  return (
    <div className="v-diag">
      {incident ? (
        <IncidentHeader incident={incident} ticket={null} resolved={resolved} extra={nextUpdate && !resolved ? `next update due ${clock(nextUpdate)} UTC` : undefined} banner={banner}
          ticketLink={ticket ? <button type="button" className="tlink" onClick={() => setPanel("ticket")}>View ticket</button> : undefined}>
          {tools}
        </IncidentHeader>
      ) : (
        <header className="top">
          <div className="top-in">
            <div className="inc">
              <h1 className="inc-title" style={{ margin: 0 }}>{chat.origin === "blank" ? chat.title || "New chat" : chat.title}</h1>
              <span className="inc-meta">{past ? "A recorded run" : "Not linked to an incident yet"}</span>
            </div>
            {tools}
          </div>
          {banner}
        </header>
      )}
      <div className="body">
        <main ref={main} aria-label="Conversation" className="mainc">
          <div className={`convo${state.pending ? " with-tray" : ""}`}>
            {state.turns.map((t, i) => {
              if (firstReport >= 0 && i < firstReport && t.kind === "group") return null;
              if (i === firstReport && t.kind === "report") {
                const lead = t.report.hypotheses.find((h) => h.status === "leading");
                return (
                  <div key={t.key} className="msg-sm">
                    <span className="who"><b>Smalls</b> · <span className="time">{clock(t.report.as_of)} UTC</span></span>
                    <HowSmallsGotHere checks={checks}>{folded.map((g) => <GroupTurn key={g.key} group={g} chatId={chat.id} state={state} onOpen={open} />)}</HowSmallsGotHere>
                    <AnswerCard report={t.report} status={t.status} first={lead ? startedAt(state, lead) : undefined} checks={checks.filter((c) => c.status === "ran").length} onOpen={open} />
                  </div>
                );
              }
              return turn(t);
            })}
            {shownLocal.map((l) => (
              <div key={l.key} className="msg-ic"><span className="who"><b>You</b> · Major Incident Manager</span><p className="ic-text">{l.q}</p>
                <span className="small muted" aria-live="polite">{DELIVERY[l.pending.delivery] ?? DELIVERY["next round"]}</span></div>
            ))}
            {running && !state.pending ? (
              <p className="small muted" aria-live="polite">{state.heartbeat ? `Smalls is working, ${state.heartbeat} s` : "Smalls is working…"}</p>
            ) : null}
          </div>
          <div className="dock">
            {state.pending && !past ? (
              <div className="tray-float"><div className="tray-in">
                <ApprovalTray key={state.pending.interrupt_id} chatId={chat.id} rows={pendingRows} pending={state.pending} schemas={schemas} onOpen={open} onRefused={setError} />
              </div></div>
            ) : null}
            <div className="dock-in">
              {error ? <Notice tone="error">{error}</Notice> : null}
              {past ? (
                <p className="small muted" style={{ margin: 0 }}>A recorded run is read-only. To diagnose again, start a new chat.</p>
              ) : (
                <div className="composer">
                  <textarea className="sm-input" rows={2} aria-label="Message Smalls" placeholder={finished ? "Ask Smalls a follow-up" : "Add a note for Smalls"} value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => { if (sendKey(e)) { e.preventDefault(); if (!waiting && !busy) send(draft); } }} />
                  <div className="acts">
                    <Toggle checked={auto} onChange={switchAuto} label="Auto-approve checks" disabled={busy} />
                    <span className="grow" />
                    {waiting ? (
                      <a className="wait-link" href="#pending" onClick={(e) => { e.preventDefault(); refocus(true); }}>
                        <span aria-hidden="true">◆ </span>{waitingText(state.pending?.cards ?? [])} waiting for you above
                      </a>
                    ) : <span className="small muted">{auto ? "Checks run without asking" : "Smalls asks before every check"}</span>}
                    {running ? <Button variant="quiet" onClick={() => start(async () => { await stopRun(chat.id); })}>Stop</Button> : null}
                    {state.status === "stopped" ? <Button onClick={() => start(async () => { await moreRun(chat.id, true); })}>Go on</Button> : null}
                    <Button variant="primary" onClick={() => send(draft)} disabled={Boolean(waiting) || busy || !draft.trim()}>Send</Button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </main>
        {panel ? <SidePanel panel={panel} state={state} ticket={ticket} overview={overview} onClose={() => setPanel(null)} /> : null}
      </div>
      {drawer ? <EvidenceDrawer key={drawer} chatId={chat.id} handle={drawer} onClose={() => setDrawer(null)} /> : null}
      {resolving && canResolve ? <ResolveDialog chat={chat} incident={incident} report={state.report} onClose={() => router.replace(`/chats/${chat.id}`, { scroll: false })} /> : null}
    </div>
  );
}

/** The element that scrolls the conversation: the page's own column at desktop widths, else the window. */
function scroller(el: HTMLElement | null): HTMLElement {
  if (el) {
    const y = getComputedStyle(el).overflowY;
    if ((y === "auto" || y === "scroll") && el.scrollHeight > el.clientHeight + 1) return el;
  }
  return document.scrollingElement as HTMLElement;
}

const endGap = (s: HTMLElement) => s.scrollHeight - s.clientHeight - s.scrollTop;
const toEnd = (el: HTMLElement | null) => { const s = scroller(el); s.scrollTo({ top: s.scrollHeight, behavior: "instant" }); };

/** Keeps the end of the conversation in view while Smalls works, as long as the person is at the end: scrolling up
 * lets go, and scrolling back to the end takes hold again. A chat that opens while it runs opens at its end. */
function useFollow(live: boolean, main: React.RefObject<HTMLElement | null>) {
  const atEnd = useRef(live);
  useEffect(() => {
    const el = main.current;
    let lastY = scroller(el).scrollTop;
    atEnd.current ||= endGap(scroller(el)) < 64;
    const check = () => {
      const s = scroller(el);
      const y = s.scrollTop, gap = endGap(s);
      atEnd.current = gap < 4 || (y >= lastY && (atEnd.current || gap < 64));
      lastY = y;
    };
    el?.addEventListener("scroll", check, { passive: true });
    window.addEventListener("scroll", check, { passive: true });
    return () => { el?.removeEventListener("scroll", check); window.removeEventListener("scroll", check); };
  }, [main]);
  useEffect(() => {
    const el = main.current;
    if (!live || !el) { if (atEnd.current) toEnd(el); return; }
    const grows = new ResizeObserver(() => { if (atEnd.current) toEnd(el); });
    grows.observe(el.firstElementChild ?? el);
    return () => grows.disconnect();
  }, [live, main]);
  return { atEnd };
}

/** Reasoning that grows while it streams, shown as it grows. A long piece arriving at once is written out over a
 * moment. Text present at first paint, a replay and reduced motion are shown whole. */
function useReveal(text: string): string {
  const [shown, setShown] = useState(text.length);
  const latest = useRef(shown);
  useEffect(() => { latest.current = shown; });
  useEffect(() => {
    const whole = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    let at = Math.min(latest.current, text.length);
    if (at >= text.length) return;
    let frame = requestAnimationFrame(function step() {
      at = whole ? text.length : Math.min(text.length, at + Math.max(8, Math.ceil((text.length - at) / 12)));
      setShown(at);
      if (at < text.length) frame = requestAnimationFrame(step);
    });
    return () => cancelAnimationFrame(frame);
  }, [text]);
  return text.slice(0, Math.min(shown, text.length));
}

/** One round of the agent: its narrative (the first paragraph, the rest a click away), any visible text, then its
 * checks as one line. */
function GroupTurn({ group, chatId, state, onOpen }: { group: Group; chatId: string; state: ChatState; onOpen: (h: string) => void }) {
  const [all, setAll] = useState(false);
  const narrative = useReveal(group.narrative ?? "");
  const paragraphs = narrative.split(/\n\s*\n/).filter(Boolean);
  return (
    <div className="msg-sm">
      {group.by === "harness" ? <span className="who"><b>Smalls</b> · the opening sweep: what is alerting, what changed and what people are saying</span>
        : paragraphs.length || group.text ? <span className="who"><b>Smalls</b></span> : null}
      {paragraphs.length ? (
        <>
          {(all ? paragraphs : paragraphs.slice(0, 1)).map((p, i) => <Prose key={i} text={p} />)}
          {paragraphs.length > 1 ? <button type="button" className="linkbtn" style={{ alignSelf: "flex-start" }} onClick={() => setAll(!all)} aria-expanded={all}>{all ? "Show less" : "Show all of its reasoning"}</button> : null}
        </>
      ) : null}
      {group.text ? <Prose text={group.text} onOpen={onOpen} /> : null}
      <GroupView chatId={chatId} group={group} rows={state.rows} pending={state.pending} onOpen={onOpen} />
    </div>
  );
}

function TurnView({ turn, chat, incident, imported, state, onOpen, checks, onTicket }: {
  turn: Turn; chat: ChatSummary; incident: IncidentSummary | null; imported: boolean; state: ChatState; onOpen: (h: string) => void; checks: number;
  onTicket?: () => void;
}) {
  switch (turn.kind) {
    case "ic": {
      const head = noteHead(turn.text);
      if (chat.origin === "headless" || chat.origin === "past") return <TicketTurn head={head} incident={incident} imported={imported} past={chat.origin === "past"} onTicket={onTicket} />;
      const who = chat.origin === "blank" ? "You" : head.author ?? "You";
      return <IcMessage who={who} at={head.time ?? clock(turn.at)} text={head.body || turn.text} />;
    }
    case "note":
      return (
        <div className="msg-ic">
          <span className="who"><b>You</b> · {turn.followup ? "follow-up" : "note while it ran"}</span>
          <p className="ic-text">{turn.text}</p>
        </div>
      );
    case "group":
      return <GroupTurn group={turn} chatId={chat.id} state={state} onOpen={onOpen} />;
    case "answer":
      return (
        <div className="msg-sm">
          <span className="who"><b>Smalls</b> · answered from what it already had; the findings stand</span>
          <Prose text={turn.text} onOpen={onOpen} />
        </div>
      );
    case "report": {
      const lead = turn.report.hypotheses.find((h) => h.status === "leading");
      return (
        <div className="msg-sm">
          <span className="who"><b>Smalls</b> · <span className="time">{clock(turn.report.as_of)} UTC</span></span>
          <AnswerCard report={turn.report} status={turn.status} first={lead ? startedAt(state, lead) : undefined} checks={checks} onOpen={onOpen} />
        </div>
      );
    }
    case "notice":
      return <Notice tone={turn.tone}>{turn.text}</Notice>;
  }
}

function IcMessage({ who, at, text }: { who: string; at: string | null; text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 280 || text.split("\n").length > 3;
  return (
    <div className="msg-ic">
      <span className="who"><b>{who}</b> · Major Incident Manager{at ? <> · <span className="time">{at} UTC</span></> : null}</span>
      <p className={`ic-text${long && !open ? " clamp" : ""}`}>{text}</p>
      {long ? <button type="button" className="disc" aria-expanded={open} onClick={() => setOpen(!open)}><span className="chev" aria-hidden="true">›</span>{open ? "Show less" : "Show the full message"}</button> : null}
    </div>
  );
}

/** How an unattended diagnosis started: one line, the note it started from clamped to three lines, and the ticket. */
function TicketTurn({ head, incident, imported, past, onTicket }: {
  head: ReturnType<typeof noteHead>; incident: IncidentSummary | null; imported: boolean; past: boolean; onTicket?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const who = head.author ?? "The Major Incident Manager";
  const line = past ? `A recorded run. It started from ${head.author ? `${head.author}’s` : "the latest"} work note${head.time ? ` at ${head.time} UTC` : ""}.`
    : imported && incident ? `Smalls started when ${incident.number} was imported from ServiceNow, from ${head.author ? `${head.author}’s` : "the latest"} work note${head.time ? ` at ${head.time} UTC` : ""}.`
    : `Smalls started on its own from ${head.author ? `${head.author}’s` : "the latest"} work note${head.time ? ` at ${head.time} UTC` : ""}.`;
  return (
    <article className="tkt" aria-label="How this diagnosis started">
      <div className="tkt-head">
        <span className="src-pill">Started automatically</span>
        <span className="small muted" style={{ flex: "1 1 260px" }}>{line}</span>
        {onTicket ? <button type="button" className="disc" onClick={onTicket}>View ticket</button> : null}
      </div>
      <div className="tkt-body">
        <span className="who"><b>{who}</b> · Major Incident Manager · work note</span>
        <p className={`ic-text${open ? "" : " clamp"}`}>{head.body}</p>
        <button type="button" className="disc" aria-expanded={open} onClick={() => setOpen(!open)}><span className="chev" aria-hidden="true">›</span>{open ? "Show less" : "Show the full message"}</button>
      </div>
    </article>
  );
}
