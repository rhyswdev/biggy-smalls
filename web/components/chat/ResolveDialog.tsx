"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { Button } from "@/components/ds";
import { resolveChat } from "@/lib/actions/chats";
import { leading } from "@/lib/chat/reduce";
import { clock } from "@/lib/format";
import { confidenceLabel } from "@/lib/format";
import type { ChatSummary, IncidentSummary, Report } from "@/lib/types";
import { CODES, type ResolutionCode } from "@/lib/writeup/render";

/** "Resolve incident" (ChatV4): who found the fix, the resolution code, the resolution notes (required), when and
 * by whom. Continue stays disabled until there are notes; nothing is saved as a knowledge article until the review. */
export function ResolveDialog({ chat, incident, report, onClose }: { chat: ChatSummary; incident: IncidentSummary | null; report: Report | null; onClose: () => void }) {
  const lead = leading(report);
  const first = report ? [...report.actions].sort((a, b) => a.rank - b.rank)[0] : undefined;
  const [path, setPath] = useState<"agent" | "own">(lead ? "agent" : "own");
  const [note, setNote] = useState("");
  const [code, setCode] = useState<ResolutionCode>(CODES[0]);
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
  const conclusion = lead ? `${lead.statement} Confidence: ${confidenceLabel(lead.confidence)}.${first ? ` Recommended action: ${first.action}` : ""}` : "No probable cause yet.";
  const cited = report ? new Set(lead?.support ?? []).size : 0;
  function submit() {
    setError(null);
    start(async () => {
      const r = await resolveChat(chat.id, { path, code, note, resolvedAt: at, resolvedBy: by });
      if (r && !r.ok) setError(r.error ?? "Not saved.");
    });
  }
  return (
    <div className="scrim" onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); onClose(); } }}>
      <section ref={box} className="dialog" role="dialog" aria-modal="true" aria-labelledby="rs-title">
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <span className="label">{[incident?.number, chat.title].filter(Boolean).join(" · ")}</span>
            <h2 id="rs-title" className="title" style={{ margin: 0 }}>Resolve incident</h2>
            <span className="small muted">Your resolution notes become this incident’s knowledge article, so similar incidents are quicker to diagnose.</span>
          </div>
          <button type="button" className="iconbtn" aria-label="Close" onClick={onClose}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" /></svg>
          </button>
        </div>
        <fieldset style={{ border: 0, margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          <legend className="heading" style={{ padding: 0, marginBottom: 8 }}>Who found the fix?</legend>
          <label className="opt">
            <input type="radio" name="rs-path" checked={path === "agent"} onChange={() => setPath("agent")} disabled={!lead} />
            <span className="opt-text"><b>Smalls’ diagnosis was right</b><span>{lead ? `The evidence Smalls gathered${cited ? `, ${cited} records for its probable cause,` : ""} goes into the write-up. You can remove any of it on the next screen.` : "Smalls has no probable cause in this chat."}</span></span>
          </label>
          <label className="opt">
            <input type="radio" name="rs-path" checked={path === "own"} onChange={() => setPath("own")} />
            <span className="opt-text"><b>I resolved it another way</b><span>Maybe you fixed it outside Smalls. On the next screen, tick only the evidence that helped your decision.</span></span>
          </label>
        </fieldset>
        {path === "agent" ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 4, padding: "10px 14px", background: "var(--surface-sunken)", borderRadius: 6 }}>
            <span className="label">Smalls’ diagnosis</span>
            <span style={{ fontSize: 13, lineHeight: "19px" }}>{conclusion}</span>
          </div>
        ) : null}
        <label style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <span className="heading">Resolution code</span>
          <select className="sm-input" style={{ maxWidth: 320, minHeight: 36 }} value={code} onChange={(e) => setCode(e.target.value as ResolutionCode)}>
            {CODES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <span className="small muted">Backing out a change restores service, so it’s usually a workaround. The permanent fix goes through a Problem record.</span>
        </label>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <label htmlFor="rs-note" className="heading">Resolution notes</label>
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
          <Button variant="primary" disabled={!note.trim() || pending} onClick={submit}>Continue to resolution review</Button>
          <Button variant="quiet" onClick={onClose}>Cancel</Button>
          <span className="small muted" style={{ flex: "1 1 200px" }}>Nothing is saved or sent yet. You review the summary first.</span>
        </div>
      </section>
    </div>
  );
}
