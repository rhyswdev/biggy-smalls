"use client";

import { useState } from "react";

const PANEL = <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.5" /><path d="M6 2.75v10.5" /></svg>;

/** The shell with its sidebar hidden until asked for (ChatV4's new chat): a button at the top left shows it and
 * hides it again. */
export function Collapsible({ side, children }: { side: React.ReactNode; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const label = open ? "Hide menu" : "Show menu";
  return (
    <div className="shell">
      {open ? <div className="side">{side}</div> : null}
      <div className="page">
        <div className="v-diag" style={{ height: "auto", flex: "none" }}>
          <header className="mini-top">
            <button type="button" className="menu-btn" aria-label={label} aria-expanded={open} title={label} onClick={() => setOpen(!open)}>{PANEL}</button>
            {open ? null : <span className="mini-brand">Smalls</span>}
          </header>
        </div>
        {children}
      </div>
    </div>
  );
}
