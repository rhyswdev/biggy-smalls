import { StatusTag } from "@/components/ds";
import { clock } from "@/lib/format";
import type { IncidentSummary, TicketView } from "@/lib/types";
import { TicketButton } from "./TicketButton";

/** The incident's header on every screen of an imported incident: priority, the Major incident badge, the
 * title and the ticket's own state ("Resolved" once it is resolved here), then number · business service ·
 * impact start · View ticket, and whatever the screen adds (the diagnosis adds when the next update is due). */
export function IncidentHeader({ incident, ticket, resolved, impactStart, extra, banner, ticketLink, meta, children }: {
  incident: IncidentSummary; ticket: TicketView | null; resolved?: boolean; impactStart?: string | null; extra?: React.ReactNode;
  banner?: React.ReactNode; ticketLink?: React.ReactNode; meta?: string; children?: React.ReactNode;
}) {
  const start = impactStart ?? incident.opened;
  return (
    <header className="top">
      <div className="top-in">
        <div className="inc">
          <div className="inc-line">
            {incident.priority_code ? <span className="prio" title={incident.priority ? `Priority ${incident.priority}` : undefined}>P{incident.priority_code}</span> : null}
            {incident.major ? <span className="mi">Major incident</span> : null}
            <h1 className="inc-title" style={{ margin: 0 }}>{incident.short_description ?? incident.number}</h1>
            {resolved ? <StatusTag status="resolved">Resolved</StatusTag> : incident.state ? <StatusTag status={incident.active ? "running" : "resolved"}>{incident.state}</StatusTag> : null}
          </div>
          <span className="inc-meta">
            {meta ?? [incident.number, incident.service, `impact start ${clock(start)} UTC`].filter(Boolean).join(" · ")}
            {extra ? <> · {extra}</> : null}
            {ticketLink ? <> · {ticketLink}</> : ticket ? <> · <TicketButton ticket={ticket} /></> : null}
          </span>
        </div>
        {children}
      </div>
      {banner}
    </header>
  );
}
