"use client";

import Link from "next/link";
import { useState } from "react";
import { Button, Notice } from "@/components/ds";
import { markCopied } from "@/lib/actions/messages";
import type { MessageRow, Report } from "@/lib/types";
import { audiences, check, draftedBy } from "@/lib/writeup/messages";
import { score } from "./Checks";

/** "Draft the exec update" in the chat (U9): the U8 drafter's message for executives, checked like any draft, with
 * Copy. It is the same draft the Messages step opens with, where it can be edited. */
export function DraftAnswer({ chatId, report, row, error, resolved }: {
  chatId: string; report: Report | null; row: MessageRow | null; error: string | null; resolved: boolean;
}) {
  const [copied, setCopied] = useState<string | null>(row?.copied ? row.text : null);
  const [problem, setProblem] = useState<string | null>(null);
  if (error) return <div className="msg-sm"><Notice tone="error">{error}</Notice></div>;
  if (!row) return <div className="msg-sm"><span className="who"><b>Smalls</b> · drafting the exec update…</span></div>;
  const exec = audiences(report).find((a) => a.key === "exec")!;
  const results = check(exec, row.text);
  const s = score(results);
  const failing = results.filter((c) => c.state !== "ok");
  async function copy() {
    setProblem(null);
    try {
      await navigator.clipboard.writeText(row!.text);
    } catch {
      setProblem("The browser did not let the page copy. Select the text and copy it by hand.");
      return;
    }
    setCopied(row!.text);
    const r = await markCopied(chatId, "exec", row!.text);
    if (!r.ok) setProblem(r.error ?? "Copied, but the mark was not kept.");
  }
  return (
    <div className="msg-sm">
      <span className="who"><b>Smalls</b> · {draftedBy(row.drafted_by)}</span>
      {row.why_code ? <Notice tone="clamped">{row.why_code}</Notice> : null}
      <p className="narr" style={{ margin: 0, padding: "10px 14px", border: "1px solid var(--line)", borderRadius: 6, background: "var(--surface-raised)", whiteSpace: "pre-line" }}>{row.text}</p>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "6px 12px" }}>
        <span aria-live="polite"><Button variant="secondary" size="sm" onClick={copy}>{copied === row.text ? "Copied" : "Copy message"}</Button></span>
        <span className="small" style={{ color: s.bad ? "var(--attention)" : "var(--ink-muted)" }}>{s.text}</span>
        <span className="small muted">
          {resolved ? <Link href={`/chats/${chatId}/messages`}>Edit it with the other messages</Link> : "Edit it with the other messages once the incident is marked resolved."}
        </span>
      </div>
      {failing.length ? <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>{failing.map((c) => <li key={c.id}>{c.state === "bad" ? "Fails" : "Check by hand"}: {c.label}</li>)}</ul> : null}
      {problem ? <Notice tone="error">{problem}</Notice> : null}
    </div>
  );
}
