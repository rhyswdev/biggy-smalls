"use client";

import { useState, useTransition } from "react";
import { Button, Toggle } from "@/components/ds";
import { startChat } from "@/lib/actions/chats";
import { sendKey } from "@/lib/keys";

/** The message box under the intro, before the first send. "Start investigation" for the commander's note,
 * "Send" for a new chat. */
export function NewChat({ origin, draft: initial }: { origin: "chat" | "blank"; draft: string }) {
  const [draft, setDraft] = useState(initial);
  const [auto, setAuto] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const send = () => start(async () => {
    const r = await startChat({ origin, trigger: draft, autoApprove: auto });
    if (r && !r.ok) setError(r.error ?? "Could not start.");
  });
  return (
    <div className="composer">
      <textarea className="sm-input" rows={origin === "chat" ? 9 : 3} aria-label="Message Smalls" placeholder="Describe what you’re seeing, or ask Smalls a question"
        value={draft} onChange={(e) => setDraft(e.target.value)} autoFocus={origin === "blank"}
        onKeyDown={(e) => { if (sendKey(e)) { e.preventDefault(); if (!pending && draft.trim()) send(); } }} />
      <div className="acts">
        <Toggle checked={auto} onChange={setAuto} label="Auto-approve reads" />
        <span className="grow" />
        {error ? <span role="alert" className="small" style={{ color: "var(--danger)" }}>{error}</span>
          : <span className="small muted">{auto ? "Reads will run without asking" : "Smalls asks before every read"}</span>}
        <Button variant="primary" onClick={send} disabled={pending || !draft.trim()}>{pending ? "Starting…" : origin === "chat" ? "Start investigation" : "Send"}</Button>
      </div>
    </div>
  );
}
