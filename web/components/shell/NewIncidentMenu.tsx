"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ds";
import type { OpenIncidents } from "@/lib/types";
import { ImportForm } from "./ImportForm";
import { OpenTickets } from "./OpenTickets";

const PLUS = <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>;
const CHAT = <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" /></svg>;
const IMPORT = <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M8 2v8" /><path d="M4.5 6.5L8 10l3.5-3.5" /><path d="M2.5 11v2.5h11V11" /></svg>;
const CLOSE = <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>;

/** New incident, at the top of the sidebar on every screen: a menu with Start a chat and Import from service
 * desk, which opens the import dialog. Escape closes the menu or the dialog and focus goes back to the button. */
export function NewIncidentMenu({ open }: { open: OpenIncidents }) {
  const [show, setShow] = useState<"closed" | "menu" | "import">("closed");
  const button = useRef<HTMLButtonElement>(null);
  const first = useRef<HTMLAnchorElement>(null);
  const close = () => { setShow("closed"); button.current?.focus(); };
  useEffect(() => { if (show === "menu") first.current?.focus(); }, [show]);
  const onKey = (e: React.KeyboardEvent) => { if (e.key === "Escape" && show !== "closed") { e.preventDefault(); e.stopPropagation(); close(); } };
  return (
    <div className="v-ni" onKeyDown={onKey}><div className="ni">
      <button ref={button} type="button" className="ni-btn" aria-haspopup="menu" aria-expanded={show === "menu"} onClick={() => setShow(show === "menu" ? "closed" : "menu")}>
        {PLUS}New incident<span className="ni-caret" aria-hidden="true">▼</span>
      </button>
      {show === "menu" ? (
        <>
          <button type="button" className="ni-away" aria-label="Close menu" tabIndex={-1} onClick={close} />
          <div className="ni-menu" role="menu" aria-label="New incident">
            <Link ref={first} className="ni-item" role="menuitem" href="/chats/new" onClick={() => setShow("closed")}>
              <span className="ni-ic" aria-hidden="true">{CHAT}</span>
              <span className="ni-tx"><b>Start a chat</b><span>Describe what you’re seeing. Smalls asks before each check.</span></span>
            </Link>
            <button type="button" className="ni-item" role="menuitem" onClick={() => setShow("import")}>
              <span className="ni-ic" aria-hidden="true">{IMPORT}</span>
              <span className="ni-tx"><b>Import from service desk</b><span>Pull in a ServiceNow incident. Smalls starts diagnosis straight away.</span></span>
            </button>
          </div>
        </>
      ) : null}
      {show === "import" ? (
        <div className="scrim" onClick={(e) => { if (e.target === e.currentTarget) close(); }}>
          <section className="dialog" role="dialog" aria-modal="true" aria-labelledby="imp-dialog-h">
            <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <h2 id="imp-dialog-h" className="title" style={{ margin: 0 }}>Import from service desk</h2>
                <span className="small muted">Smalls takes the ticket, its work notes and linked changes, writes an incident overview and starts diagnosis. You can stop it at any time.</span>
              </div>
              <button type="button" className="iconbtn" aria-label="Close" onClick={close}>{CLOSE}</button>
            </div>
            <div className="src"><b>ServiceNow</b>{open.connected ? <span className="ok">✓ Connected</span> : <span className="small muted" style={{ marginLeft: "auto" }}>Not connected</span>}</div>
            <ImportForm variant="dialog" label="Ticket number or link" button="Import and investigate" autoFocus
              middle={open.incidents.length ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <span className="label">Open P1 and P2 tickets for your groups</span>
                  <OpenTickets incidents={open.incidents} />
                </div>
              ) : null}
              actions={<Button variant="quiet" onClick={close}>Cancel</Button>} />
          </section>
        </div>
      ) : null}
    </div></div>
  );
}
