"use client";

import { useState } from "react";
import { Confidence, Handles, StatusTag } from "@/components/ds";
import { leading } from "@/lib/chat/reduce";
import { leadSentence } from "@/lib/chat/prose";
import { clock, confidenceLabel } from "@/lib/format";
import type { Report } from "@/lib/types";
import { Inline, Prose } from "./Prose";

export const SURE = 0.7;      // at or above this, a probable cause; below, the leading claim is ahead but not settled

/** A value the report left unknown is not shown. */
const known = (v: string | null | undefined): v is string => Boolean(v && v.trim() && v.trim().toLowerCase() !== "unknown");

/** The answer (ChatV4): the cause in one line, then why it fails, the recommended action, the assignment group,
 * the confidence and what to avoid; business impact, the technical detail, the confidence bar and the evidence
 * one click away. `checks` is how many checks ran before it. */
export function AnswerCard({ report, status, first, checks, onOpen }: {
  report: Report; status: string; first: number | undefined; checks: number; onOpen: (h: string) => void;
}) {
  const [more, setMore] = useState(false);
  const lead = leading(report);
  const [sentence, rest] = leadSentence(report.summary);
  const action = [...report.actions].sort((a, b) => a.rank - b.rank)[0];
  const group = [report.impact?.assignment_group, action?.owner].find(known) ?? null;
  const oncall = known(report.impact?.oncall) ? report.impact!.oncall : null;
  const avoid = report.do_not[0];
  const impact = known(report.impact?.customer) ? report.impact!.customer : null;
  const sure = Boolean(lead && lead.confidence >= SURE);
  const from = checks ? ` From ${checks === 1 ? "1 check" : `${checks} checks`}.` : "";
  const rows: { k: string; v: React.ReactNode; cls?: string }[] = [];
  if (sentence) rows.push({ k: "Why it fails", v: <Inline text={sentence} onOpen={onOpen} /> });
  if (action) rows.push({ k: "Recommended action", v: action.action, cls: "do" });
  if (group || oncall) rows.push({ k: "Assignment group", v: [group ? `${group}.` : null, oncall ? `${oncall} is on call.` : null].filter(Boolean).join(" ") });
  if (lead) rows.push({ k: "Confidence", v: `${confidenceLabel(lead.confidence)}.${from}` });
  if (avoid) rows.push({ k: "Avoid", v: <>{avoid.action}{avoid.why ? <span className="muted"> {avoid.why}</span> : null}</>, cls: "avoid" });
  const extra: { k: string; v: React.ReactNode }[] = [];
  if (impact) extra.push({ k: "Business impact", v: impact });
  if (rest) extra.push({ k: "Technical detail", v: <Prose text={rest} onOpen={onOpen} /> });
  if (lead) extra.push({ k: "Confidence", v: (
    <span className="ans-conf">
      <StatusTag status={lead.status}>{sure ? "Probable cause" : "Ahead"}</StatusTag>
      <Confidence value={lead.confidence} previous={first} />
      {typeof first === "number" ? <span className="small muted">up from {Math.round(first * 100)}% before these checks</span> : null}
    </span>
  ) });
  if (lead) extra.push({ k: "Evidence", v: <Handles ids={lead.support} onOpen={onOpen} none="No evidence cited yet" /> });
  if (report.timeline.length) extra.push({ k: "Timeline", v: (
    <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 4 }}>
      {report.timeline.map((t, i) => <li key={i}><span className="time muted">{clock(t.t_utc)}</span> {t.event} <Handles ids={t.evidence} onOpen={onOpen} /></li>)}
    </ol>
  ) });
  const checked = report.validation?.verifier === "pass" || report.validation?.verifier === "revised";
  return (
    <>
      <p className="lead">
        {lead && sure ? <>Probable cause: {lead.statement}</>
          : lead ? <>Ahead, but not settled: {lead.statement} <span className="small muted">({confidenceLabel(lead.confidence)})</span></>
          : <>No probable cause yet.</>}
      </p>
      {status !== "completed" ? <span className="small muted">{status === "paused" ? "The working picture when it stopped." : "A partial report: the run stopped before a full one."}</span> : null}
      {rows.length ? (
        <dl className="ans" aria-label="Answer">
          {[...rows, ...(more ? extra : [])].map((r, i) => <div key={`${r.k}-${i}`} className={`ans-row${"cls" in r && r.cls ? ` ${r.cls}` : ""}`}><dt>{r.k}</dt><dd>{r.v}</dd></div>)}
        </dl>
      ) : null}
      {extra.length ? (
        <button type="button" className="disc" aria-expanded={more} onClick={() => setMore(!more)}>
          <span className="chev" aria-hidden="true">›</span>{more ? "Show less" : "Business impact, technical detail and evidence"}
        </button>
      ) : null}
      <span className="small muted">
        Findings {status === "incomplete" ? "so far" : "updated"} at <span className="time">{clock(report.as_of)} UTC</span>. {checked ? "Checked by the verifier" : "Not checked by a verifier"}
        {report.validation?.disputed.length ? `, ${report.validation.disputed.length} claims disputed` : ""}.
      </span>
    </>
  );
}
