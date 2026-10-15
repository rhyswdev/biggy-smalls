"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition } from "react";
import { Prose } from "@/components/chat/Prose";
import { Button, Confidence, StatusTag } from "@/components/ds";
import { moreRun, runAgent, stopRun } from "@/lib/actions/chats";
import { leading, startedAt, steps, type ChatState } from "@/lib/chat/reduce";
import { useChatEvents } from "@/lib/events";
import { elapsed, money, plural, seconds } from "@/lib/format";
import type { RecordedRun } from "@/lib/types";

// The agent chooses its own first reads, so a run goes from orientation straight to investigating. A recorded run
// from before that had an opening sweep, which shows as part of orientation.
const PHASES = ["orient", "investigate", "report", "verify"];
const PHASE_WORDS: Record<string, string> = {
  orient: "Reading what exists", investigate: "Investigating", report: "Writing the report",
  verify: "Checking the report's claims", followup: "Answering a follow-up", done: "Done",
};

interface Props {
  chatId: string | null;
  initial: ChatState | null;
  author: string | null;
  noteTime: string | null;
  recorded: RecordedRun | null;
  profile: string;
  mode: string;
}

/** The dashboard's side panel: idle (two ways to investigate), running (live, from the run's events) and
 * finished (the answer in brief). A reload mid-run folds the stored events again and picks the stream up. */
export function AgentPanel(props: Props) {
  if (!props.chatId || !props.initial) return <Idle {...props} />;
  return <Live key={props.chatId} {...props} chatId={props.chatId} initial={props.initial} />;
}

function Idle({ author, noteTime, recorded, profile, mode }: Props) {
  const [pending, start] = useTransition();
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <span className="label">Investigate</span>
      <div className="choice">
        <Link className="lbtn primary" href="/chats/new?from=incident" style={{ height: 40, padding: "0 16px", fontSize: 14 }}>Investigate in chat</Link>
        <span className="small muted">You approve each read. Starts from {author ? `${author}’s` : "the"} {noteTime ? `${noteTime} ` : ""}note, which you can edit first.</span>
      </div>
      <span className="or">or</span>
      <div className="choice">
        <Button disabled={pending} onClick={() => start(async () => { const r = await runAgent(); setError(r.ok ? null : r.error ?? "Could not start."); })}>
          {pending ? "Starting…" : "Run agent"}
        </Button>
        <span className="small muted">
          Runs without asking for reads; an action it proposes waits for you. {recorded
            ? <>The last recorded run took <span className="time">{seconds(recorded.wall_s)}</span>, {plural(recorded.reads, "read")} and {money(recorded.cost_usd)}. </>
            : null}
          This one uses the {mode === "scripted" ? "scripted development" : profile} profile. Findings open in the same chat, read for read.
        </span>
        {error ? <span role="alert" className="small" style={{ color: "var(--danger)" }}>{error}</span> : null}
      </div>
      <span className="small muted" style={{ marginTop: "auto", paddingTop: 8, borderTop: "1px solid var(--line)" }}>
        Smalls reads logs, alerts, changes, chat, tickets and runbooks, and works out what’s relevant.
      </span>
    </>
  );
}

function Live({ chatId, initial }: Props & { chatId: string; initial: ChatState }) {
  const router = useRouter();
  const state = useChatEvents(chatId, initial, initial.status !== "completed" && initial.status !== "incomplete" && initial.status !== "failed");
  const [now, setNow] = useState<number | null>(null);
  const [pending, start] = useTransition();
  const was = useRef(state.status);
  const running = state.status === "running" || state.status === "idle";
  useEffect(() => {
    if (!running) return;
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const t = setInterval(tick, 1000);
    return () => { clearTimeout(first); clearInterval(t); };
  }, [running]);
  useEffect(() => {
    if (was.current !== state.status && !running) router.refresh();       // the chats list catches up
    was.current = state.status;
  }, [state.status, running, router]);
  // The panel scrolls inside the card. While the run goes it follows the newest read, unless the commander has
  // scrolled up to an earlier one; once it has finished, the answer in brief is at the top.
  const anchor = useRef<HTMLSpanElement>(null);
  const lines = steps(state).length;
  useEffect(() => {
    const box = anchor.current?.closest<HTMLElement>(".inc-side-in");
    if (!box) return;
    if (!running) box.scrollTop = 0;
    else if (box.scrollHeight - box.scrollTop - box.clientHeight < 120) box.scrollTop = box.scrollHeight;
  }, [lines, running]);

  if (running || state.status === "waiting") {
    const list = steps(state);
    const phase = !state.phase || state.phase === "sweep" ? "orient" : state.phase;
    const at = PHASES.indexOf(phase);
    return (
      <>
        <span ref={anchor} hidden />
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
          <StatusTag status="running">Agent running</StatusTag>
          <span className="time">{now && state.startedAt ? elapsed(now - Date.parse(state.startedAt)) : "0:00"}</span>
          <span className="small muted" style={{ marginLeft: "auto" }}>{PHASE_WORDS[phase] ?? phase}</span>
        </div>
        <ol className="list" aria-label="Phases" style={{ display: "flex", gap: "4px 14px", flexWrap: "wrap" }}>
          {PHASES.map((p, i) => (
            <li key={p} className="small" style={{ display: "flex", gap: 4, color: i <= at ? "var(--ink)" : "var(--ink-muted)" }}>
              <span className={`g ${i < at ? "g-ok" : i === at ? "g-run" : "g-q"}`} aria-hidden="true">{i < at ? "✓" : i === at ? "›" : "○"}</span>{p}
            </li>
          ))}
        </ol>
        {state.heartbeat ? <span className="small muted">Model working, <span className="time">{state.heartbeat} s</span></span> : null}
        <div>
          {list.length ? list.map((s) => (
            <div className="step" key={s.call_id}>
              <span className={`g ${s.state === "done" ? "g-ok" : s.state === "failed" ? "g-no" : s.state === "running" ? "g-run" : "g-q"}`} aria-hidden="true">
                {s.state === "done" ? "✓" : s.state === "failed" ? "✕" : s.state === "running" ? "›" : "○"}
              </span>
              <span className="tool">{s.tool}</span>
              <span style={{ flex: "1 1 200px", minWidth: 0 }}>{s.text}</span>
            </div>
          )) : <div className="step"><span className="g g-q" aria-hidden="true">○</span><span className="small muted">No reads yet</span></div>}
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
          <Button variant="quiet" size="sm" disabled={pending} onClick={() => start(async () => { await stopRun(chatId); })}>Stop</Button>
          <span className="small muted">You can leave this page. The run carries on and finishes here.</span>
        </div>
      </>
    );
  }

  const report = state.report;
  const lead = leading(report);
  const reads = Object.values(state.rows).filter((r) => r.by === "agent" && r.kind === "read");
  const asked = reads.filter((r) => r.who === "human").length;
  const actions = report?.proposed_actions ?? [];
  const unapproved = actions.filter((a) => a.decision !== "approved").length;
  const hyps = [...(report?.hypotheses ?? [])].sort((a, b) => (a.status === "rejected" ? 1 : 0) - (b.status === "rejected" ? 1 : 0) || b.confidence - a.confidence);
  return (
    <>
      <span ref={anchor} hidden />
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
        <StatusTag status={state.status === "stopped" ? "skipped" : state.status === "completed" ? "ok" : "error"}>
          {state.status === "completed" ? "Finished" : state.status === "stopped" ? "Stopped" : state.status === "incomplete" ? "Incomplete" : "Failed"}
        </StatusTag>
        <span className="time muted">
          {seconds(state.finished?.wall_s)} · {plural(reads.length, "read")}, {asked ? `${asked} needed approval` : "none needed approval"}
        </span>
      </div>
      {lead ? <p className="lead">{lead.subject} is the leading {lead.role} ({lead.confidence.toFixed(2)}).</p>
        : report ? <p className="lead">No hypothesis leads yet: the report is inconclusive.</p> : null}
      {report ? <Prose text={report.summary} /> : null}
      {unapproved ? <span className="small" style={{ color: "var(--attention)" }}>◆ {plural(unapproved, "action")} proposed, not approved</span> : null}
      {hyps.length ? (
        <div style={{ display: "flex", flexDirection: "column" }}>
          {hyps.slice(0, 5).map((h) => (
            <div key={h.id} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "6px 8px", padding: "8px 0", borderTop: "1px solid var(--line)" }}>
              <StatusTag status={h.status} />
              <span style={{ flex: "1 1 160px", minWidth: 0, fontSize: 13, lineHeight: "18px" }}>{h.statement}</span>
              <Confidence value={h.confidence} previous={startedAt(state, h)} muted={h.status === "rejected"} />
            </div>
          ))}
        </div>
      ) : null}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
        <Link className="lbtn primary" href={`/chats/${chatId}`}>Open findings in chat</Link>
        {state.status === "stopped"
          ? <Button variant="quiet" disabled={pending} onClick={() => start(async () => { await moreRun(chatId, true); router.refresh(); })}>Go on</Button>
          : null}
        <Button variant="quiet" disabled={pending} onClick={() => start(async () => { await runAgent(); router.refresh(); })}>Run again</Button>
        <Link className="lbtn" href="/chats/new?from=incident">Investigate in chat</Link>
      </div>
    </>
  );
}
