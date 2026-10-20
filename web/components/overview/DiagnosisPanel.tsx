"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, useTransition } from "react";
import { Button, StatusTag } from "@/components/ds";
import { moreRun, stopRun } from "@/lib/actions/chats";
import { plainLabel } from "@/lib/chat/labels";
import { leading, steps, tally, type ChatState } from "@/lib/chat/reduce";
import { useChatEvents } from "@/lib/events";
import { confidenceLabel, seconds } from "@/lib/format";

const known = (v: string | null | undefined): v is string => Boolean(v && v.trim() && v.trim().toLowerCase() !== "unknown");

/** The overview's "Smalls diagnosis" panel (IncidentV4), following the import's diagnosis live: running, with the
 * check it is on and the rest a click away; waiting on the person; stopped; or the answer in brief. */
export function DiagnosisPanel({ chatId, initial }: { chatId: string; initial: ChatState }) {
  const router = useRouter();
  const state = useChatEvents(chatId, initial, true);
  const [error, setError] = useState<string | null>(null);
  const [busy, start] = useTransition();
  const done = ["completed", "incomplete", "failed"].includes(state.status);
  const wasDone = ["completed", "incomplete", "failed"].includes(initial.status);
  useEffect(() => { if (done && !wasDone) router.refresh(); }, [done, wasDone, router]);       // the banner is the server's
  // The writer's overview landed after the page was drawn: redraw the sections in place.
  useEffect(() => { if (state.overviewSeq && state.overviewSeq !== initial.overviewSeq) router.refresh(); }, [state.overviewSeq, initial.overviewSeq, router]);

  const list = steps(state);
  const label = (id: string) => (state.rows[id] ? plainLabel(state.rows[id]!).now : "");
  const current = list.find((s) => s.state === "running") ?? list[list.length - 1];
  const act = (work: () => Promise<{ ok: boolean; error?: string }>) => start(async () => { const r = await work(); if (!r.ok) setError(r.error ?? "That did not work."); });
  const lead = leading(state.report);
  const report = state.report;
  const action = report ? [...report.actions].sort((a, b) => a.rank - b.rank)[0] : undefined;
  const group = [report?.impact?.assignment_group, action?.owner].find(known);
  const oncall = known(report?.impact?.oncall) ? report!.impact!.oncall : null;
  const why = report?.summary ? report.summary.split(/(?<=[.!?])\s+/)[0] : null;

  let tag: React.ReactNode, body: React.ReactNode;
  if (state.pending) {
    tag = <StatusTag status="awaiting_approval">Waiting on you</StatusTag>;
    body = <><p className="para" style={{ fontSize: 14, lineHeight: "21px" }}>{state.pending.cards.length === 1 ? "1 check needs" : `${state.pending.cards.length} checks need`} your approval before diagnosis can continue.</p>
      <div className="ag-acts" style={{ marginTop: 12 }}><Link className="lbtn primary" href={`/chats/${chatId}#pending`}>Go to approvals</Link></div></>;
  } else if (state.status === "running" || state.status === "idle") {
    tag = <StatusTag status="running"><span className="v-spin" aria-hidden="true" />In progress</StatusTag>;
    const ahead = Object.values(state.board).filter((h) => h.status !== "rejected").sort((x, y) => y.confidence - x.confidence)[0];
    body = (
      <>
        <span className="small muted">Started automatically when the ticket was imported. Usually under a minute.</span>
        <div style={{ fontSize: 14, lineHeight: "20px", fontWeight: 500, marginTop: 10 }}>{current ? `${label(current.call_id)}…` : "Getting started…"}</div>
        <span className="small muted">{list.length === 1 ? "1 check so far" : `${list.length} checks so far`}</span>
        {ahead ? (
          <div style={{ marginTop: 12, display: "flex", flexDirection: "column", gap: 2 }}>
            <span className="label">Ahead so far</span>
            <span style={{ fontSize: 14, lineHeight: "20px" }}>{ahead.statement}</span>
            <span className="small muted">Confidence: {confidenceLabel(ahead.confidence)}</span>
          </div>
        ) : null}
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px 16px", marginTop: 12 }}>
          <Link className="lbtn" href={`/chats/${chatId}`}>Follow it in the diagnosis</Link>
          <Button variant="quiet" size="sm" disabled={busy} onClick={() => act(() => stopRun(chatId))}>Stop</Button>
        </div>
      </>
    );
  } else if (state.status === "stopped" || state.status === "paused") {
    tag = <StatusTag status="open">Stopped</StatusTag>;
    body = <><p className="para" style={{ fontSize: 14, lineHeight: "21px" }}>Stopped before it finished. Nothing it found is lost.</p>
      <div className="ag-acts" style={{ marginTop: 12 }}><Button variant="primary" disabled={busy} onClick={() => act(() => moreRun(chatId, true))}>Start again</Button></div></>;
  } else {
    tag = <StatusTag status={state.status === "failed" ? "error" : "ok"}>{state.status === "failed" ? "Failed" : `Complete${state.finished ? ` · ${seconds(state.finished.wall_s)}` : ""}`}</StatusTag>;
    const sure = Boolean(lead && lead.confidence >= 0.7);
    body = (
      <>
        <span className="label">{sure ? "Probable cause" : "No probable cause yet"}</span>
        {lead ? <p className="ag-lead">{lead.statement}</p> : null}
        <span className="small muted">{lead ? `Confidence: ${confidenceLabel(lead.confidence)} · ` : ""}{tally(state).run === 1 ? "1 check" : `${tally(state).run} checks`}</span>
        <dl className="ag-rows">
          {why ? <div><dt>Why</dt><dd>{why}</dd></div> : null}
          {action ? <div className="do"><dt>Action</dt><dd>{action.action}</dd></div> : null}
          {group || oncall ? <div><dt>Group</dt><dd>{[group ? `${group}.` : null, oncall ? `${oncall} is on call.` : null].filter(Boolean).join(" ")}</dd></div> : null}
          {report?.do_not[0] ? <div className="avoid"><dt>Avoid</dt><dd>{report.do_not[0].action}</dd></div> : null}
        </dl>
        <div className="ag-acts">
          <Link className="lbtn primary" href={`/chats/${chatId}`}>Open diagnosis</Link>
          <Button variant="quiet" disabled={busy} onClick={() => act(() => moreRun(chatId, false))}>Run again</Button>
        </div>
      </>
    );
  }
  return (
    <section className="card pad" aria-labelledby="ag-h" aria-live="polite">
      <div className="ag-head"><h2 id="ag-h" className="c-h" style={{ flex: "1 1 auto" }}>Smalls diagnosis</h2>{tag}</div>
      {body}
      {error ? <p role="alert" className="small" style={{ color: "var(--danger)" }}>{error}</p> : null}
    </section>
  );
}
