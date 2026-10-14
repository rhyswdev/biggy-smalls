import { StatusTag } from "@/components/ds";
import { clock, priority } from "@/lib/format";
import type { IncidentCard as Card } from "@/lib/types";

/** The incident as it stands at `now`, and the commander's note in full. Nothing derived (section 6.4). */
export function IncidentCard({ card, resolved, children }: { card: Card; resolved: boolean; children: React.ReactNode }) {
  const inc = card.incident;
  const prio = priority(inc?.priority);
  const facts: [string, React.ReactNode][] = inc ? [
    ["Opened", <><span className="time">{clock(inc.opened)} UTC</span></>],
    ["IC", card.note.author ?? "–"],
    ["State", inc.state ?? "–"],
    ["Service", inc.service ?? "–"],
    ["Group", inc.group ?? "–"],
    ["CI", inc.ci ?? "–"],
    ["Assigned to", inc.assignee ?? "–"],
  ] : [];
  return (
    <article className="card inc" aria-label={inc ? `${inc.number} ${inc.short_description}` : "Incident"}>
      <div className="inc-main">
        <div className="inc-tags">
          {prio ? <span className="prio">{prio.text}</span> : null}
          {inc?.number ? <span className="time">{inc.number}</span> : null}
          {resolved ? <StatusTag status="resolved" /> : <StatusTag status="running">Investigating</StatusTag>}
          <span className="grow" />
          <span className="src-pill">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M8 2v8" /><path d="M4.5 6.5L8 10l3.5-3.5" /><path d="M2.5 11v2.5h11V11" /></svg>
            Pushed from ServiceNow
          </span>
          <span className="demo">Take-home dataset</span>
        </div>
        <h3 className="inc-h">{inc?.short_description ?? "No incident in the served data"}</h3>
        {facts.length ? (
          <dl className="facts">
            {facts.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
          </dl>
        ) : null}
        <div className="note">
          <span className="label">Latest work note{card.note.author ? ` · ${card.note.author}` : ""}{card.note.time ? ` · ${card.note.time}` : ""}</span>
          <p className="note-text">{card.note.text}</p>
        </div>
      </div>
      <div className="inc-side" aria-live="polite">{children}</div>
    </article>
  );
}
