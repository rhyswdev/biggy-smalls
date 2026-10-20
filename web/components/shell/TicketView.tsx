import { clock, zoneNames } from "@/lib/format";
import type { TicketView as Ticket } from "@/lib/types";

const CLOSE = <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>;

/** The ticket as ServiceNow holds it (TicketV4), read-only: its fields, then its work notes newest first. Times
 * are UTC with the ticket source's own local time beside, the only local time on any screen. In a drawer it has
 * a source pill and a close button; embedded in a panel it has neither. */
export function TicketView({ ticket, embedded, onClose, headingId }: { ticket: Ticket; embedded?: boolean; onClose?: () => void; headingId?: string }) {
  const f = ticket.fields;
  const zone = zoneNames(ticket.zone, ticket.opened.utc);
  const local = (at: { utc: string; local: string }) => <><span className="time">{clock(at.utc, true)} UTC</span> <span className="small muted">({at.local} {zoneNames(ticket.zone, at.utc).short})</span></>;
  const fields: [string, React.ReactNode][] = [
    ["State", f.state], ["Priority", f.priority], ["Impact / Urgency", [f.impact, f.urgency].filter(Boolean).join(" / ") || null],
    ["Business service", f.business_service], ["Configuration item", f.cmdb_ci], ["Assignment group", f.assignment_group],
    ["Assigned to", f.assigned_to], ["Opened", local(ticket.opened)],
  ];
  return (
    <div className="v-ticket">
      <div className="tk">
        <div className={`tk-head${embedded ? " flat" : ""}`}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
            {embedded ? null : <span className="src-pill">ServiceNow ticket, as imported</span>}
            <h2 id={headingId} className="title" style={{ margin: 0 }}>
              <span className="time" style={{ fontSize: 15, color: "var(--action)" }}>{ticket.incident.number}</span> {ticket.incident.short_description}
            </h2>
            <span className="small muted">Read-only copy. Times converted from {zone.long} to UTC.</span>
          </div>
          {embedded ? null : <button type="button" className="iconbtn" aria-label="Close ticket" onClick={onClose}>{CLOSE}</button>}
        </div>
        <div className="tk-body">
          <dl className="fields">
            {fields.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v ?? "–"}</dd></div>)}
          </dl>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <span className="label">Work notes · newest first</span>
            {ticket.notes.length ? ticket.notes.map((n) => (
              <div key={n.handle} className="note">
                <div className="note-head"><b>{n.author ?? "Unknown"}</b>{local(n)}</div>
                <p>{n.text}</p>
              </div>
            )) : <span className="small muted">No work notes yet.</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
