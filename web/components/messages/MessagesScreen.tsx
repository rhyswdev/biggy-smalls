"use client";

import { useMemo, useRef, useState, useTransition } from "react";
import { Button, Notice } from "@/components/ds";
import { IncidentHeader } from "@/components/shell/IncidentHeader";
import { StatusBanner } from "@/components/shell/StatusBanner";
import { EvidenceDrawer } from "@/components/evidence/Drawer";
import { draftMessages, editMessage, markCopied } from "@/lib/actions/messages";
import type { IncidentSummary, KbHit, MessageRow, MessagesView, TicketView } from "@/lib/types";
import { AUDIENCE_KEYS, audiences, check, draftedBy, specs, wordCount, type AudienceKey } from "@/lib/writeup/messages";
import { render, type WriteupData } from "@/lib/writeup/render";
import { Checks } from "./Checks";
import { KbView } from "./KbView";

type Pick = AudienceKey | "kb";
const SAVE_AFTER_MS = 600;

export interface CommsHead { incident: IncidentSummary | null; ticket: TicketView | null; meta: string; title: string }

/** The communications (MessagesV4): an audience per tab, each draft checked as it is edited, with only what fails
 * shown, and copied into its channel from the bottom of its card; the knowledge article is the last tab and is
 * not sent. "Copied" belongs to the exact text copied, so any edit clears it. */
export function MessagesScreen({ chatId, data, view, hits, incidentHandle, head }: {
  chatId: string; data: WriteupData; view: MessagesView; hits: KbHit[]; incidentHandle: string | null; head: CommsHead;
}) {
  const auds = useMemo(() => audiences(data.report), [data.report]);
  const [rows, setRows] = useState<Partial<Record<AudienceKey, MessageRow>>>(() => Object.fromEntries(view.messages.map((m) => [m.audience, m])));
  const [texts, setTexts] = useState<Partial<Record<AudienceKey, string>>>(() => Object.fromEntries(view.messages.map((m) => [m.audience, m.text])));
  const [copied, setCopied] = useState<Partial<Record<AudienceKey, string>>>(() => Object.fromEntries(view.messages.filter((m) => m.copied).map((m) => [m.audience, m.text])));
  const [pick, setPick] = useState<Pick>("exec");
  const [drafting, setDrafting] = useState<AudienceKey[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<string | null>(null);
  const [allChecks, setAllChecks] = useState(false);
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

  const all = copiedN === AUDIENCE_KEYS.length;
  const banner = (
    <StatusBanner tone={all ? "ok" : "neutral"} glyph={all ? "✓" : "›"} title={`${all ? `All ${AUDIENCE_KEYS.length}` : `${copiedN} of ${AUDIENCE_KEYS.length}`} messages copied.`}
      text={all ? `Each is in its channel. The knowledge article is ${view.entry ? "already saved" : "not saved yet"}.` : "Pick an audience, check the draft is right, then copy it into its channel."}>
      <div className="prog-bar" aria-hidden="true">{AUDIENCE_KEYS.map((k) => <span key={k} className={isCopied(k) ? "on" : ""} />)}</div>
    </StatusBanner>
  );
  const bad = results.filter((c) => c.state === "bad");
  const shown = allChecks ? results : results.filter((c) => c.state !== "ok");
  return (
    <div className="v-comms">
      {head.incident ? <IncidentHeader incident={head.incident} ticket={head.ticket} resolved banner={banner} meta={head.meta} /> : (
        <header className="top"><div className="top-in"><div className="inc"><span className="inc-title">{head.title}</span><span className="inc-meta">{head.meta}</span></div></div>{banner}</header>
      )}
      <main className="main">
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span className="label">Messages</span>
          <h1 className="title" style={{ margin: 0 }}>Major incident communications</h1>
          <span className="small muted">Resolution updates drafted from the resolution record, one per audience, checked against each audience’s rules.</span>
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
        <nav aria-label="Audiences" className="tabs">
          {auds.map((a) => {
            const r = rows[a.key];
            const t = texts[a.key] ?? "";
            const res = r ? check(a, t) : [];
            const fails = res.filter((c) => c.state === "bad").length;
            const state = drafting.includes(a.key) ? ["Drafting…", "st-draft"] : !r ? ["Not drafted", "st-draft"] : isCopied(a.key) ? ["✓ Copied", "st-copied"] : t !== r.draft ? ["Edited", "st-edited"] : ["Draft", "st-draft"];
            return (
              <button key={a.key} type="button" className="tab" aria-pressed={pick === a.key} onClick={() => setPick(a.key)}>
                <span className="tab-name">{a.name}</span>
                <span className="tab-meta"><span className={state[1]}>{state[0]}</span>{r ? <span className={fails ? "ck-bad" : "muted"}>{fails ? ` · ${fails} to fix` : " · checks pass"}</span> : null}</span>
              </button>
            );
          })}
          <button type="button" className="tab" aria-pressed={pick === "kb"} onClick={() => setPick("kb")}>
            <span className="tab-name">Knowledge article</span>
            <span className="tab-meta">{view.entry ? <span className="st-saved">✓ Saved</span> : <span className="st-draft">Not saved</span>}<span className="muted"> · not sent</span></span>
          </button>
        </nav>

        {cur ? (
          <section aria-label="Message editor" className="card msg">
            <div className="msg-head">
              <label htmlFor={`msg-${cur.key}`} className="heading">{cur.name}</label>
              <span className="time muted" style={{ fontWeight: 400 }}>{cur.channel}</span>
              {row ? <span style={{ marginLeft: "auto" }}><Button variant="quiet" size="sm" disabled={drafting.length > 0} onClick={() => draft([cur.key])}>{drafting.includes(cur.key) ? "Drafting…" : "Redraft"}</Button></span> : null}
              <span className="small muted" style={{ flex: "1 1 100%" }}>{cur.guide}{row ? ` ${draftedBy(row.drafted_by)}.` : ""}</span>
            </div>
            {row?.why_code ? <div style={{ padding: "12px 16px 0" }}><Notice tone="clamped">{row.why_code}</Notice></div> : null}
            {row ? (
              <>
                <div className="msg-edit">
                  <textarea id={`msg-${cur.key}`} className="sm-input" rows={6} style={{ fontSize: 15, lineHeight: "23px", padding: "10px 12px" }}
                    value={text} onChange={(e) => edit(cur.key, e.target.value)} />
                </div>
                <div className="checks" aria-live="polite">
                  <div className="checks-line">
                    <span className={bad.length ? "ck-bad" : "muted"}>{bad.length ? `! ${bad.length} ${bad.length === 1 ? "check" : "checks"} to fix before copying` : `✓ All ${results.length} checks pass for this audience`}</span>
                    <span className="grow" />
                    <button type="button" className="disc" aria-expanded={allChecks} onClick={() => setAllChecks(!allChecks)}>
                      <span className="chev" aria-hidden="true">›</span>{allChecks ? (bad.length ? "Show only what to fix" : "Hide checks") : `Show all ${results.length} checks`}
                    </button>
                  </div>
                  <Checks results={shown} />
                </div>
                <div className="msg-foot">
                  <span className="time muted" style={{ fontWeight: 400, flex: "1 1 200px" }}>{wordCount(text)} words · plain text, pastes exactly as written</span>
                  <Button variant="quiet" disabled={text === row.draft} onClick={() => edit(cur.key, row.draft)}>Restore draft</Button>
                  <span aria-live="polite"><Button variant="primary" onClick={() => copy(cur.key)}>{isCopied(cur.key) ? "Copied" : "Copy message"}</Button></span>
                </div>
              </>
            ) : (
              <div className="msg-foot">
                <span className="small muted" style={{ flex: "1 1 200px" }}>Not drafted yet.</span>
                <Button variant="primary" disabled={drafting.length > 0} onClick={() => draft([cur.key])}>{drafting.includes(cur.key) ? "Drafting…" : "Draft this message"}</Button>
              </div>
            )}
          </section>
        ) : (
          <div className="v-msg"><KbView chatId={chatId} data={data} entry={view.entry} w={w} hits={hits} incidentHandle={incidentHandle} onOpen={setDrawer} /></div>
        )}
      </main>
      {drawer ? <EvidenceDrawer key={drawer} chatId={chatId} handle={drawer} onClose={() => setDrawer(null)} /> : null}
    </div>
  );
}
