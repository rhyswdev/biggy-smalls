"use client";

import { Button, Confidence, Handles, StatusTag } from "@/components/ds";
import { leading } from "@/lib/chat/reduce";
import { clock } from "@/lib/format";
import type { Report } from "@/lib/types";
import { firstSentence, Linked } from "./Linked";

export const SURE = 0.7;      // at or above this, the answer leads with the four asks; below, a narrative

/** The answer (section 4 and 7.5): cause, how sure, do next, owner, don't, evidence, when the leading confidence
 * is 0.7 or more; the lead and a narrative below it. Then the findings strip. */
export function AnswerCard({ report, status, first, onOpen, onResolve, resolved }: {
  report: Report; status: string; first: number | undefined; onOpen: (h: string) => void; onResolve?: () => void; resolved?: boolean;
}) {
  const lead = leading(report);
  const [opening, rest] = firstSentence(report.summary);
  const action = [...report.actions].sort((a, b) => a.rank - b.rank)[0];
  const owner = action?.owner || report.impact?.oncall || null;
  const dont = report.do_not[0];
  const sure = lead && lead.confidence >= SURE;
  return (
    <>
      <p className="lead"><Linked text={opening} onOpen={onOpen} /></p>
      {sure ? (
        <dl className="ans" aria-label="Answer">
          <div className="ans-row"><dt>Cause</dt><dd>{lead.statement}</dd></div>
          <div className="ans-row"><dt>How sure</dt><dd className="ans-conf">
            <StatusTag status="leading" /><Confidence value={lead.confidence} previous={first} />
            {typeof first === "number" ? <span className="small muted">from {first.toFixed(2)} when it was first written</span> : null}
          </dd></div>
          {action ? <div className="ans-row do"><dt>Do next</dt><dd>{action.action}</dd></div> : null}
          {owner ? <div className="ans-row"><dt>Owner</dt><dd>{owner}</dd></div> : null}
          {dont ? <div className="ans-row avoid"><dt>Don’t</dt><dd>{dont.action}{dont.why ? <span className="muted"> {dont.why}</span> : null}</dd></div> : null}
          <div className="ans-row"><dt>Evidence</dt><dd><Handles ids={lead.support} onOpen={onOpen} /></dd></div>
        </dl>
      ) : (
        <>
          {rest ? <p className="narr"><Linked text={rest} onOpen={onOpen} /></p> : null}
          {lead ? <p className="narr">Leading, not yet sure: {lead.statement} <span className="time">({lead.confidence.toFixed(2)})</span></p> : null}
          <Handles ids={lead?.support ?? []} onOpen={onOpen} none="No evidence cited yet" />
        </>
      )}
      {report.timeline.length ? (
        <details className="sm-details">
          <summary>Timeline, {report.timeline.length} events</summary>
          <ol className="sm-tl">
            {report.timeline.map((t, i) => (
              <li key={i} className="sm-tl-row">
                <span className="sm-tl-time">{clock(t.t_utc, true)}</span>
                <span className="sm-tl-rail" aria-hidden="true"><span className="sm-tl-dot" /></span>
                <div className="sm-tl-body"><span className="sm-tl-event">{t.event}</span><Handles ids={t.evidence} onOpen={onOpen} /></div>
              </li>
            ))}
          </ol>
        </details>
      ) : null}
      <div className="rep">
        <div className="rep-text">
          <strong>Findings {status === "incomplete" ? "so far" : "updated"} · <span className="time">{clock(report.as_of)} UTC</span></strong>
          <span>{report.validation?.verifier === "pass" || report.validation?.verifier === "revised" ? "Checked by the verifier" : "Not checked by a verifier"}
            {report.validation?.disputed.length ? `, ${report.validation.disputed.length} claims disputed` : ""}.</span>
          <span className="muted">{resolved ? "Marked resolved. The summary and the messages are open." : "When it’s fixed, mark it resolved. Smalls drafts the write-up, then the stakeholder messages."}</span>
        </div>
        {onResolve && !resolved ? <Button variant="primary" onClick={onResolve}>Mark resolved…</Button> : null}
      </div>
    </>
  );
}
