import { clock, priority } from "@/lib/format";
import type { ImportedIncident, Overview } from "@/lib/types";
import { Fold } from "./Fold";

const GLYPH = { bad: "✕", warn: "◆", ok: "✓", unknown: "○" } as const;

/** The leading number of a ServiceNow choice ("1 - High" is 1). */
const lead = (v: string | null) => /^(\d)/.exec(v ?? "")?.[1] ?? null;

/** What's going on: two sentences, then the key facts. Impact start is the overview's impact row, else when the
 * ticket was logged; the bridge shows only when the data holds one (it never does yet). */
export function WhatsGoingOn({ incident, overview }: { incident: ImportedIncident; overview: Overview }) {
  const [first, ...rest] = overview.whats_going_on.split(/(?<=[.!?])\s+/);
  const p = priority(incident.priority);
  const start = overview.timeline.find((t) => t.impact_start)?.time ?? null;
  const iu = [lead(incident.impact), lead(incident.urgency)];
  const facts: [string, React.ReactNode][] = [
    ["Priority", p ? <>{p.text}{iu[0] && iu[1] ? <span className="sub"> Impact {iu[0]} × Urgency {iu[1]}</span> : null}</> : null],
    ["State", incident.state],
    ["Major Incident Manager", incident.assignee],
    ["Assignment group", incident.group],
    ["Business impact", overview.business_impact || null],
    ["Impact start", start ? <>{start} UTC <span className="sub">Logged {clock(incident.opened)}</span></> : <>{clock(incident.opened)} UTC <span className="sub">when it was logged</span></>],
    ["Next update due", incident.next_update_due ? <span className="due">{clock(incident.next_update_due)} UTC</span> : null],
  ];
  return (
    <section className="card pad" aria-labelledby="go-h">
      <div className="c-head">
        <h2 id="go-h" className="label" style={{ margin: 0 }}>What’s going on</h2>
        <span className="c-sub">{overview.written_by === "code" ? "From the ticket and its latest work note" : "Written by Smalls from the ticket and its work notes"}</span>
      </div>
      {first ? <p className="big">{first}</p> : null}
      {rest.length ? <p className="para" style={{ marginTop: 8 }}>{rest.join(" ")}</p> : null}
      <dl className="kf">
        {facts.filter(([, v]) => v).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
      </dl>
    </section>
  );
}

/** Affected service and CIs: the path a request takes, each item with a plain line and what has been reported. */
export function CiMap({ incident, overview }: { incident: ImportedIncident; overview: Overview }) {
  if (!overview.affected_cis.length) return null;
  return (
    <section className="card pad" aria-labelledby="map-h">
      <div className="c-head">
        <h2 id="map-h" className="c-h">Affected service and CIs</h2>
        <span className="c-sub">{incident.service ? `Business service: ${incident.service}. ` : ""}How a request travels, and what’s been reported at each CI.</span>
      </div>
      <ol className="cimap">
        {overview.affected_cis.map((c, i) => (
          <li key={`${c.name}-${i}`} className="cimap-step">
            {i ? <span className="arr" aria-hidden="true">→</span> : null}
            <div className={`node${c.severity === "bad" ? " bad" : c.severity === "warn" ? " warn" : ""}`}>
              <span className="node-name">{c.name}</span>
              {c.label ? <span className="node-tech">{c.label}</span> : null}
              {c.description ? <span className="node-what">{c.description}</span> : null}
              {c.status ? <span className={`node-st st-${c.severity === "unknown" ? "n" : c.severity}`}><span aria-hidden="true">{GLYPH[c.severity]} </span>{c.status}</span> : null}
            </div>
          </li>
        ))}
      </ol>
      {overview.not_affected.length ? (
        <div className="map-foot"><span><b className="st-ok">✓ Not affected:</b> {overview.not_affected.join(", ")}</span></div>
      ) : null}
    </section>
  );
}

/** Working theories from the bridge: who thinks what, and what has been tried. */
export function Theories({ overview }: { overview: Overview }) {
  if (!overview.working_theories.length) return null;
  return (
    <section className="card pad" aria-labelledby="who-h">
      <div className="c-head"><h2 id="who-h" className="c-h">Working theories from the bridge</h2><span className="c-sub">From the work notes. Smalls tests each one.</span></div>
      <ul className="th">
        {overview.working_theories.map((t, i) => (
          <li key={i}>
            <span className="th-who"><b>{t.person}</b>{t.area ? <span className="small muted">{t.area}</span> : null}</span>
            <span className="th-say"><span>{t.claim}</span>{t.tried ? <span className="th-st">{t.tried}</span> : null}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function Timeline({ overview }: { overview: Overview }) {
  if (!overview.timeline.length) return null;
  const n = overview.timeline.length;
  return (
    <Fold id="tl-h" title="Timeline" sub={`${n === 1 ? "1 event" : `${n} events`}, UTC`}>
      <ol className="tl" style={{ marginTop: 12 }}>
        {overview.timeline.map((t, i) => <li key={i}><span className="time">{t.time}</span><span>{t.event}</span></li>)}
      </ol>
    </Fold>
  );
}

export function Glossary({ overview }: { overview: Overview }) {
  if (!overview.glossary.length) return null;
  return (
    <Fold id="gl-h" title="Terms you’ll hear" sub={overview.glossary.map((g) => g.term).join(", ")}>
      <dl className="gloss" style={{ marginTop: 12 }}>
        {overview.glossary.map((g) => <div key={g.term}><dt>{g.term}</dt><dd>{g.meaning}</dd></div>)}
      </dl>
    </Fold>
  );
}

const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]!.toUpperCase()).join("");

/** Contacts and resolver groups: built by code from the ticket, the on-call schedule, the CMDB and the user
 * records. A Call link only when a record holds a number; the incident channels the notes name, by name. */
export function Contacts({ overview, compact }: { overview: Overview; compact?: boolean }) {
  if (!overview.contacts.length && !overview.channels.length) return null;
  const list = (
    <>
      <ul className="ppl">
        {overview.contacts.map((p) => (
          <li key={p.name} className="p">
            <span className="av" aria-hidden="true">{p.kind === "group" ? "◇" : initials(p.name)}</span>
            <div className="p-main">
              <span className="p-name">{p.name}</span>
              <span className="p-role">{p.role}</span>
              {p.phone ? <div className="p-acts"><a href={`tel:${p.phone.replace(/[^\d+]/g, "")}`} aria-label={`Call ${p.name}`}>{p.phone}</a></div> : null}
            </div>
          </li>
        ))}
      </ul>
      {overview.channels.length ? (
        <div className="rooms">
          {overview.channels.map((c) => <div key={c} className="room"><span className="room-t"><b>#{c}</b><span className="small muted">Incident channel, named in the work notes</span></span></div>)}
        </div>
      ) : null}
    </>
  );
  if (compact) return list;
  return (
    <section className="card pad" aria-labelledby="ct-h">
      <div className="c-head" style={{ marginBottom: 10 }}><h2 id="ct-h" className="c-h">Contacts and resolver groups</h2></div>
      {list}
    </section>
  );
}
