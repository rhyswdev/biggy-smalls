"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { Button } from "@/components/ds";
import { resolveChat } from "@/lib/actions/chats";
import { leading } from "@/lib/chat/reduce";
import { clock } from "@/lib/format";
import type { ChatSummary, IncidentSummary, Report } from "@/lib/types";

/** "Mark it resolved": who found the fix, how it was resolved (required), when and by whom. Continue stays
 * disabled until there is a note (section 4, Resolve dialog). */
export function ResolveDialog({ chat, incident, report, onClose }: { chat: ChatSummary; incident: IncidentSummary | null; report: Report | null; onClose: () => void }) {
  const lead = leading(report);
  const first = report ? [...report.actions].sort((a, b) => a.rank - b.rank)[0] : undefined;
  const [path, setPath] = useState<"agent" | "own">(lead ? "agent" : "own");
  const [note, setNote] = useState("");
  const [at, setAt] = useState(clock(chat.now));
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const box = useRef<HTMLElement>(null);
  const by = incident?.assignee ?? "Major Incident Manager";
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    box.current?.querySelector<HTMLElement>("input:checked")?.focus();
    return () => opener?.focus?.();
  }, []);
  const conclusion = lead ? `${lead.statement}${first ? ` First action: ${first.action}` : ""}` : "Smalls has no leading conclusion yet.";
  function submit() {
    setError(null);
    start(async () => {
      const r = await resolveChat(chat.id, { path, note, resolvedAt: at, resolvedBy: by });
      if (r && !r.ok) setError(r.error ?? "Not saved.");
    });
  }
  return (
    <div className="scrim" onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); onClose(); } }}>
      <section ref={box} className="dialog" role="dialog" aria-modal="true" aria-labelledby="rs-title">
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span className="label">{[incident?.number, chat.title].filter(Boolean).join(" · ")}</span>
            <h2 id="rs-title" className="title" style={{ margin: 0 }}>Mark it resolved</h2>
            <span className="small muted">Your answer becomes this incident’s knowledge base write-up, so similar issues are easier to identify.</span>
          </div>
          <button type="button" className="iconbtn" aria-label="Close" onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
          </button>
        </div>
        <fieldset style={{ border: 0, margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          <legend className="heading" style={{ padding: 0, marginBottom: 8 }}>Who found the fix?</legend>
          <label className="opt">
            <input type="radio" name="rs-path" checked={path === "agent"} onChange={() => setPath("agent")} disabled={!lead} />
            <span className="opt-text"><b>Smalls’ conclusion was right</b><span>{lead ? "The write-up keeps all the evidence Smalls cited; you can untick what did not matter." : "Smalls has no leading conclusion in this chat."}</span></span>
          </label>
          <label className="opt">
            <input type="radio" name="rs-path" checked={path === "own"} onChange={() => setPath("own")} />
            <span className="opt-text"><b>I resolved it another way</b><span>Maybe you fixed it outside Smalls. On the next screen, tick only the evidence that helped your decision.</span></span>
          </label>
        </fieldset>
        {path === "agent" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 4, padding: "10px 14px", background: "var(--surface-sunken)", borderRadius: 6 }}>
            <span className="label">Smalls’ conclusion</span>
            <span style={{ fontSize: 13, lineHeight: "19px" }}>{conclusion}</span>
          </div>
        ) : null}
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label htmlFor="rs-note" className="heading">How was it resolved?</label>
          <span className="small muted">Required, in your own words. Write it for whoever meets this next: what you did, and what told you it worked.</span>
          <textarea id="rs-note" className="sm-input" rows={4} style={{ fontSize: 14, lineHeight: "21px", padding: "8px 10px" }}
            placeholder="What you changed, when, and what showed it worked." value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <div className="acts" style={{ gap: 12 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="small muted">Resolved at (UTC)</span>
            <input className="sm-input" style={{ width: 110, fontFamily: "var(--font-mono)" }} value={at} onChange={(e) => setAt(e.target.value)} /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="small muted">Resolved by</span>
            <input className="sm-input" style={{ width: 180 }} value={by} disabled /></label>
        </div>
        {error ? <p role="alert" className="small" style={{ color: "var(--danger)", margin: 0 }}>{error}</p> : null}
        <div className="acts" style={{ gap: 8, paddingTop: 4 }}>
          <Button variant="primary" disabled={!note.trim() || pending} onClick={submit}>Continue to summary</Button>
          <Button variant="quiet" onClick={onClose}>Cancel</Button>
          <span className="small muted" style={{ flex: "1 1 200px" }}>Nothing is sent. The knowledge base entry is saved only from the summary.</span>
        </div>
      </section>
    </div>
  );
}
