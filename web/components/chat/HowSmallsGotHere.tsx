"use client";

import { useState } from "react";
import { line, note, sentence, type CheckLine } from "@/lib/chat/howWeGotHere";

/** Once there is an answer, the working before it folds into one card: how many checks, what they found in one
 * high-level sentence (the summariser's headline, else a short one from code), a note when some were skipped or
 * rejected, and the steps themselves one click away. */
export function HowSmallsGotHere({ checks, summary, children }: { checks: CheckLine[]; summary?: string | null; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const said = note(checks);
  return (
    <div className="grp steps-card">
      <button type="button" className="grp-sum" aria-expanded={open} onClick={() => setOpen(!open)}>
        <span className="chev" aria-hidden="true">›</span><span style={{ fontWeight: 600 }}>How Smalls got here</span>
        <span className="muted">{line(checks)}</span><span className="grp-more">{open ? "Hide" : "Show the steps"}</span>
      </button>
      <p className="steps-sum">{summary?.trim() || sentence(checks)}{said ? <span className="steps-note">{said}</span> : null}</p>
      {open ? <div style={{ display: "flex", flexDirection: "column", gap: 18, padding: "4px 14px 14px" }}>{children}</div> : null}
    </div>
  );
}
