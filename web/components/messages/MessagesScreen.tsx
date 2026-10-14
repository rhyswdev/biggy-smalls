"use client";

import { useMemo, useRef, useState, useTransition } from "react";
import { Button, Notice } from "@/components/ds";
import { EvidenceDrawer } from "@/components/evidence/Drawer";
import { draftMessages, editMessage, markCopied } from "@/lib/actions/messages";
import type { KbHit, MessageRow, MessagesView } from "@/lib/types";
import { AUDIENCE_KEYS, audiences, check, draftedBy, specs, wordCount, type AudienceKey } from "@/lib/writeup/messages";
import { render, type WriteupData } from "@/lib/writeup/render";
import { Checks, score } from "./Checks";
import { KbView } from "./KbView";

type Pick = AudienceKey | "kb";
const SAVE_AFTER_MS = 600;

/** The stakeholder messages (section 4, Messages): four audiences, each draft checked as it is edited and copied
 * into its channel, and the knowledge-base entry beside them, which is not sent. "Copied" belongs to the exact
 * text copied, so any edit clears it. */
export function MessagesScreen({ chatId, data, view, hits, incidentHandle, children }: {
  chatId: string; data: WriteupData; view: MessagesView; hits: KbHit[]; incidentHandle: string | null; children?: React.ReactNode;
}) {
  const auds = useMemo(() => audiences(data.report), [data.report]);
  const [rows, setRows] = useState<Partial<Record<AudienceKey, MessageRow>>>(() => Object.fromEntries(view.messages.map((m) => [m.audience, m])));
  const [texts, setTexts] = useState<Partial<Record<AudienceKey, string>>>(() => Object.fromEntries(view.messages.map((m) => [m.audience, m.text])));
  const [copied, setCopied] = useState<Partial<Record<AudienceKey, string>>>(() => Object.fromEntries(view.messages.filter((m) => m.copied).map((m) => [m.audience, m.text])));
  const [pick, setPick] = useState<Pick>("exec");
  const [drafting, setDrafting] = useState<AudienceKey[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<string | null>(null);
  const [, start] = useTransition();
  const timers = useRef<Partial<Record<AudienceKey, ReturnType<typeof setTimeout>>>>({});
  const w = useMemo(() => (data.writeup && data.resolution ? render(data, new Set(data.writeup.kept), data.resolution.note) : null), [data]);

  const isCopied = (k: AudienceKey) => copied[k] !== undefined && copied[k] === texts[k];
  const copiedN = AUDIENCE_KEYS.filter(isCopied).length;
  const missing = AUDIENCE_KEYS.filter((k) => !rows[k]);
  const cur = pick === "kb" ? null : auds.find((a) => a.key === pick)!;
  const row = cur ? rows[cur.key] : undefined;
  const text = cur ? texts[cur.key] ?? "" : "";
  const results = cur && row ? check(cur, text) : [];

  function take(v: MessagesView, keys: AudienceKey[]) {
    const got = Object.fromEntries(v.messages.filter((m) => keys.includes(m.audience)).map((m) => [m.audience, m]));
    setRows((r) => ({ ...r, ...got }));
    setTexts((t) => ({ ...t, ...Object.fromEntries(Object.values(got).map((m) => [m.audience, m.text])) }));
    setCopied((c) => Object.fromEntries(Object.entries(c).filter(([k]) => !keys.includes(k as AudienceKey))));
  }
  function draft(keys: AudienceKey[]) {
    setError(null);
    setDrafting(keys);
    for (const k of keys) clearTimeout(timers.current[k]);
    start(async () => {
      const r = await draftMessages(chatId, specs(data.report, data.resolution, keys));
      if (r.ok && r.view) take(r.view, keys); else setError(r.error ?? "The drafts were not made.");
      setDrafting([]);
    });
  }
  function edit(k: AudienceKey, value: string) {
    setTexts((t) => ({ ...t, [k]: value }));
    clearTimeout(timers.current[k]);
    timers.current[k] = setTimeout(() => {
      editMessage(chatId, k, value).then((r) => { if (!r.ok) setError(r.error ?? "The edit was not kept."); });
    }, SAVE_AFTER_MS);
  }
  async function copy(k: AudienceKey) {
    const value = texts[k] ?? "";
    setError(null);
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      setError("The browser did not let the page copy. Select the text and copy it by hand.");
      return;
    }
    clearTimeout(timers.current[k]);
    setCopied((c) => ({ ...c, [k]: value }));
    const r = await markCopied(chatId, k, value);
    if (!r.ok) setError(r.error ?? "Copied, but the mark was not kept.");
  }

  return (
    <div className="v-msg" style={{ minHeight: "100vh", background: "var(--surface)" }}>
      {children}
      <main style={{ maxWidth: 1160, margin: "0 auto", padding: "24px 24px 80px", display: "flex", flexDirection: "column", gap: 20, boxSizing: "border-box" }}>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "flex-end", gap: "12px 24px" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: "1 1 320px" }}>
            <span className="label">Messages</span>
            <h1 className="title" style={{ margin: 0 }}>Stakeholder messages</h1>
            <span style={{ fontSize: 13, lineHeight: "18px", color: "var(--ink-muted)" }}>
              Drafted from the {data.writeup?.saved_at ? "saved write-up" : "report and your resolution"}. Edit, check against each audience’s rules, then copy into its channel.
            </span>
          </div>
          <div className="prog" role="status" aria-label={`${copiedN} of ${AUDIENCE_KEYS.length} copied`}>
            <span className="prog-n" aria-hidden="true">{copiedN} of {AUDIENCE_KEYS.length} copied</span>
            <div className="prog-bar" aria-hidden="true">{AUDIENCE_KEYS.map((k) => <span key={k} className={isCopied(k) ? "on" : ""} />)}</div>
          </div>
        </div>
        {error ? <Notice tone="error">{error}</Notice> : null}
        {missing.length ? (
          <div className="card" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px 16px", padding: "12px 16px" }}>
            <span style={{ flex: "1 1 320px", minWidth: 0 }}>
              {missing.length === AUDIENCE_KEYS.length ? "No messages drafted yet." : `${missing.length} of ${AUDIENCE_KEYS.length} messages not drafted yet.`}{" "}
              <span className="small muted">{view.drafter === "code" ? "Code drafts them from the report’s suggested update; no model is called." : `The ${view.drafter} profile drafts each one: a model call per audience.`}</span>
            </span>
            <Button variant="primary" disabled={drafting.length > 0} onClick={() => draft(missing)}>
              {drafting.length ? "Drafting…" : missing.length === AUDIENCE_KEYS.length ? "Draft the four messages" : `Draft the other ${missing.length}`}
            </Button>
          </div>
        ) : null}
        <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-start" }}>
          <nav aria-label="Audiences" className="card auds">
            <div className="label" style={{ padding: "10px 14px" }}>Send to</div>
            {auds.map((a) => {
              const r = rows[a.key];
              const t = texts[a.key] ?? "";
              const s = r ? score(check(a, t)) : null;
              const state = !r ? ["Not drafted", "st-none"] : isCopied(a.key) ? ["✓ Copied", "st-copied"] : t !== r.draft ? ["Edited", "st-edited"] : ["Draft", "st-draft"];
              return (
                <button key={a.key} type="button" className="aud" aria-pressed={pick === a.key} onClick={() => setPick(a.key)}>
                  <span className="aud-top"><span className="aud-name">{a.name}</span><span className={`st ${state[1]}`}>{drafting.includes(a.key) ? "Drafting…" : state[0]}</span></span>
                  <span className="aud-meta"><span className="time" style={{ fontWeight: 400 }}>{a.channel}</span>{s ? <span className={s.bad ? "ck-bad" : "ck-ok"}>{s.text}</span> : null}</span>
                </button>
              );
            })}
            <div className="label" style={{ padding: "14px 14px 8px", borderTop: "1px solid var(--line)" }}>Not sent</div>
            <button type="button" className="aud" aria-pressed={pick === "kb"} onClick={() => setPick("kb")}>
              <span className="aud-top"><span className="aud-name">Knowledge base entry</span><span className={`st ${view.entry ? "st-copied" : "st-none"}`}>{view.entry ? "Saved" : "Not saved"}</span></span>
              <span className="aud-meta">Searchable with past incidents</span>
            </button>
          </nav>

          {cur ? (
            <section aria-label="Message editor" className="card msg">
              <div className="msg-head">
                <div className="msg-id">
                  <div className="msg-line"><label htmlFor={`msg-${cur.key}`} className="heading">{cur.name}</label><span className="time muted" style={{ fontWeight: 400 }}>{cur.channel}</span></div>
                  <span className="small muted">{cur.guide}</span>
                  {row ? <span className="small muted">{draftedBy(row.drafted_by)}.</span> : null}
                </div>
                {row ? (
                  <>
                    <Button variant="quiet" size="sm" disabled={drafting.length > 0} onClick={() => draft([cur.key])}>{drafting.includes(cur.key) ? "Drafting…" : "Redraft"}</Button>
                    <Button variant="quiet" size="sm" disabled={text === row.draft} onClick={() => edit(cur.key, row.draft)}>Restore draft</Button>
                    <span aria-live="polite"><Button variant="primary" onClick={() => copy(cur.key)}>{isCopied(cur.key) ? "Copied" : "Copy message"}</Button></span>
                  </>
                ) : (
                  <Button variant="primary" disabled={drafting.length > 0} onClick={() => draft([cur.key])}>{drafting.includes(cur.key) ? "Drafting…" : "Draft this message"}</Button>
                )}
              </div>
              {row?.why_code ? <div style={{ padding: "12px 16px 0" }}><Notice tone="clamped">{row.why_code}</Notice></div> : null}
              {row ? (
                <div className="msg-body">
                  <div className="msg-edit">
                    <textarea id={`msg-${cur.key}`} className="sm-input" rows={8} style={{ fontSize: 15, lineHeight: "23px", padding: "10px 12px" }}
                      value={text} onChange={(e) => edit(cur.key, e.target.value)} />
                    <span className="time muted" style={{ fontWeight: 400 }}>{wordCount(text)} words · plain text, pastes exactly as written</span>
                  </div>
                  <Checks results={results} />
                </div>
              ) : <p className="small muted" style={{ margin: 0, padding: "14px 16px" }}>Not drafted yet.</p>}
            </section>
          ) : (
            <KbView chatId={chatId} data={data} entry={view.entry} w={w} hits={hits} incidentHandle={incidentHandle} onOpen={setDrawer} />
          )}
        </div>
      </main>
      {drawer ? <EvidenceDrawer key={drawer} chatId={chatId} handle={drawer} onClose={() => setDrawer(null)} /> : null}
    </div>
  );
}
