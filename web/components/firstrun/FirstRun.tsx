import Link from "next/link";
import { ImportForm } from "@/components/shell/ImportForm";
import { OpenTickets } from "@/components/shell/OpenTickets";
import { ThemeSwitch } from "@/components/shell/ThemeSwitch";
import type { OpenIncidents } from "@/lib/types";

const IMPORT = <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M8 2v8" /><path d="M4.5 6.5L8 10l3.5-3.5" /><path d="M2.5 11v2.5h11V11" /></svg>;
const CHAT = <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round"><path d="M2.5 3.5h11v7h-6l-3 2.5v-2.5h-2z" /></svg>;

/** The first-run screen (EmptyV4): what `/` shows until an incident is imported. Two ways in, import (the
 * recommended one) or a chat, then how it works and whether ServiceNow is connected. No sidebar. */
export function FirstRun({ open, theme }: { open: OpenIncidents; theme: string }) {
  const fresh = open.incidents.filter((i) => !i.imported);
  return (
    <div className="v-first" style={{ minHeight: "100vh", display: "flex", flexDirection: "column", background: "var(--surface)", color: "var(--ink)" }}>
      <header className="top">
        <span className="brand">Smalls</span>
        <ThemeSwitch theme={theme} />
      </header>
      <main className="wrap">
        <section className="hello" aria-labelledby="hello-h">
          <h1 id="hello-h" className="hello-t">
            <svg width="26" height="26" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true" style={{ color: "var(--action)", flex: "none" }}><circle cx="8" cy="8" r="6" /><circle cx="8" cy="8" r="2.5" /></svg>
            It’s quiet in here
          </h1>
          <p className="hello-s">No open incidents in Smalls right now.</p>
          <p className="blurb">Smalls diagnoses incidents with you. Bring in a ticket and it checks logs, alerts, changes and the incident channel, then tells you the probable cause, the recommended action and which group should own it. It asks before running anything you haven’t approved.</p>
        </section>
        <div className="opts">
          <section className="opt main" aria-labelledby="imp-h">
            <div className="opt-top">
              <span className="ic" aria-hidden="true">{IMPORT}</span>
              <h2 id="imp-h" className="opt-h">Import from service desk</h2>
              <span className="rec">Start here</span>
            </div>
            <p className="opt-d">Enter a ServiceNow incident. Smalls writes an overview and starts diagnosis straight away.</p>
            <ImportForm variant="first" label="Incident number" button="Import and diagnose" />
            {fresh.length ? (
              <>
                <span className="label" style={{ paddingTop: 4 }}>Open in ServiceNow for your groups</span>
                <OpenTickets incidents={fresh} />
              </>
            ) : null}
          </section>
          <section className="opt" aria-labelledby="chat-h">
            <div className="opt-top">
              <span className="ic" aria-hidden="true">{CHAT}</span>
              <h2 id="chat-h" className="opt-h">Start a chat</h2>
            </div>
            <p className="opt-d">No ticket yet? Describe what you’re seeing. Smalls asks before each check, and you can link a ticket later.</p>
            <div style={{ marginTop: "auto" }}><Link className="go sec" href="/chats/new">Start a chat</Link></div>
          </section>
        </div>
        <ol className="steps" aria-label="How it works">
          <li><span className="n">1</span><span><b>Bring in the incident</b><span className="d">Import the ticket, or describe it in a chat.</span></span></li>
          <li><span className="n">2</span><span><b>Smalls diagnoses</b><span className="d">Probable cause, recommended action and assignment group.</span></span></li>
          <li><span className="n">3</span><span><b>Resolve and record</b><span className="d">Saved as a knowledge article for next time.</span></span></li>
        </ol>
        <div className="conn">
          {open.connected ? <span><span className="ok">✓</span> ServiceNow connected</span> : null}
          <span>New P1 and P2 incidents for your groups will appear here automatically.</span>
        </div>
      </main>
    </div>
  );
}
