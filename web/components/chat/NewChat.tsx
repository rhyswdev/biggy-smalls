"use client";

import { useState, useTransition } from "react";
import { Button, Toggle } from "@/components/ds";
import { startChat } from "@/lib/actions/chats";
import { sendKey } from "@/lib/keys";

const PLUS = <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true"><path d="M8 3v10M3 8h10" /></svg>;

/** A new chat (ChatV4, blank start): one centred question and the composer, with linking to an incident and
 * auto-approve under it. Once sent it is the diagnosis. Attaching files is not built yet, so its button is off. */
export function NewChat({ draft: initial, incidents, linked }: { draft: string; incidents: { number: string; title: string }[]; linked: string | null }) {
  const [draft, setDraft] = useState(initial);
  const [auto, setAuto] = useState(false);
  const [incident, setIncident] = useState<string>(linked ?? "");
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const send = () => start(async () => {
    const r = await startChat({ origin: initial ? "chat" : "blank", trigger: draft, autoApprove: auto, incident: incident || null });
    if (r && !r.ok) setError(r.error ?? "Could not start.");
  });
  return (
    <section className="hero" aria-label="Start a new chat">
      <h1 className="hero-t">
        <svg width="26" height="26" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true" style={{ color: "var(--action)", flex: "none" }}><circle cx="8" cy="8" r="6" /><circle cx="8" cy="8" r="2.5" /></svg>
        What’s going on?
      </h1>
      <div className="composer hero-box">
        <textarea className="sm-input" rows={initial ? 8 : 2} aria-label="Message Smalls" placeholder="Describe what you’re seeing, or paste a ticket number"
          value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus
          onKeyDown={(e) => { if (sendKey(e)) { e.preventDefault(); if (!pending && draft.trim()) send(); } }} />
        <div className="acts">
          <button type="button" className="hero-ic" aria-label="Attach logs or a screenshot" title="Attaching files isn’t available yet" disabled aria-disabled="true" style={{ cursor: "not-allowed", opacity: 0.5 }}>{PLUS}</button>
          <span className="grow" />
          {error ? <span role="alert" className="small" style={{ color: "var(--danger)" }}>{error}</span> : null}
          <Button variant="primary" onClick={send} disabled={pending || !draft.trim()}>{pending ? "Starting…" : "Send"}</Button>
        </div>
      </div>
      <div className="hero-under">
        <label className="hero-sel" style={{ cursor: "pointer" }}>
          <span className="sm-visually-hidden">Link this chat to an incident</span>
          <select value={incident} onChange={(e) => setIncident(e.target.value)} title="Link this chat to an open incident"
            style={{ font: "inherit", fontSize: 13, color: "inherit", background: "transparent", border: 0, cursor: "pointer", maxWidth: 320 }}>
            <option value="">Not linked to an incident</option>
            {incidents.map((i) => <option key={i.number} value={i.number}>{i.number} · {i.title}</option>)}
          </select>
        </label>
        <span className="grow" />
        <Toggle checked={auto} onChange={setAuto} label="Auto-approve checks" />
      </div>
    </section>
  );
}
