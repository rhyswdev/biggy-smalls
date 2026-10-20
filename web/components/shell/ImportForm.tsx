"use client";

import { useId, useState, useTransition } from "react";
import { importIncident } from "@/lib/actions/incidents";

/** The number field and its button, on the first-run card and in the import dialog. Submitting imports the
 * incident and lands on its overview; an unknown number shows the API's sentence under the field. */
export function ImportForm({ label, button, variant, autoFocus, middle, actions }: {
  label: string; button: string; variant: "first" | "dialog"; autoFocus?: boolean; middle?: React.ReactNode; actions?: React.ReactNode;
}) {
  const [num, setNum] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const id = useId();
  const ready = num.trim().length > 0 && !pending;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    setError(null);
    start(async () => {
      const r = await importIncident(num);
      if (r && !r.ok) setError(r.error ?? "The incident could not be imported.");
    });
  };
  const input = (
    <input id={`${id}-n`} className="sm-input" autoComplete="off" spellCheck={false} placeholder="INC0000000" value={num} autoFocus={autoFocus}
      aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-e` : undefined}
      onChange={(e) => { setNum(e.target.value); setError(null); }}
      style={variant === "dialog" ? { fontFamily: "var(--font-mono)", fontSize: 14, minHeight: 40 } : undefined} />
  );
  const message = error ? <span id={`${id}-e`} role="alert" className="small" style={{ color: "var(--danger)", flex: "1 1 100%" }}>{error}</span> : null;
  if (variant === "first") {
    return (
      <form className="field" onSubmit={submit}>
        <label htmlFor={`${id}-n`} className="label" style={{ flex: "1 1 100%" }}>{label}</label>
        {input}
        <button type="submit" className={ready ? "go" : "go off"} aria-disabled={!ready}>{pending ? "Importing…" : button}</button>
        {message}
      </form>
    );
  }
  return (
    <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <label htmlFor={`${id}-n`} className="heading">{label}</label>
        {input}
        {message}
      </div>
      {middle}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
        <button type="submit" className="sm-btn sm-btn-primary" disabled={!ready}>{pending ? "Importing…" : button}</button>
        {actions}
      </div>
    </form>
  );
}
