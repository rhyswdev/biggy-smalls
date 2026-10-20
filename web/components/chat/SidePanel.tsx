"use client";

import { useEffect, useRef } from "react";
import { Contacts } from "@/components/overview/Sections";
import { TicketView } from "@/components/shell/TicketView";
import type { ChatState } from "@/lib/chat/reduce";
import type { Overview, TicketView as Ticket } from "@/lib/types";
import { WorkingPicture } from "./WorkingPicture";

export type Panel = "picture" | "contacts" | "ticket";

const TITLES: Record<Panel, [string, (t: Ticket | null) => string]> = {
  picture: ["Working picture", () => "What Smalls believes right now, and how sure"],
  contacts: ["Contacts and resolver groups", () => "Who to talk to on this incident"],
  ticket: ["Ticket", (t) => (t ? `${t.incident.number}, as imported from ServiceNow` : "")],
};

/** The side panel (ChatV4): one of the working picture or the ticket. It pushes the conversation over at 1100 px
 * and wider, and slides over it below. × or Escape closes it. */
export function SidePanel({ panel, state, ticket, overview, onClose }: { panel: Panel; state: ChatState; ticket: Ticket | null; overview: Overview | null; onClose: () => void }) {
  const box = useRef<HTMLElement>(null);
  useEffect(() => { box.current?.focus(); }, [panel]);
  const [title, sub] = TITLES[panel];
  return (
    <aside ref={box} className="sidepanel" aria-label={title} tabIndex={-1} style={{ outline: "none" }}
      onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); onClose(); } }}>
      <div className="sp-head">
        <div style={{ display: "flex", flexDirection: "column", gap: 1, flex: "1 1 auto", minWidth: 0 }}>
          <h2 className="heading" style={{ margin: 0 }}>{title}</h2>
          <span className="small muted">{sub(ticket)}</span>
        </div>
        <button type="button" className="iconbtn sp-x" aria-label="Close panel" onClick={onClose}>
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
        </button>
      </div>
      {panel === "picture" ? <div className="sp-body" aria-live="polite"><WorkingPicture state={state} /></div> : null}
      {panel === "contacts" && overview ? <div className="sp-body v-shell"><Contacts overview={overview} compact /></div> : null}
      {panel === "ticket" && ticket ? <TicketView ticket={ticket} embedded /> : null}
    </aside>
  );
}
