"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { Prose } from "@/components/chat/Prose";
import { Button, Handle, Handles } from "@/components/ds";
import { IncidentHeader } from "@/components/shell/IncidentHeader";
import { StatusBanner } from "@/components/shell/StatusBanner";
import type { IncidentSummary, TicketView } from "@/lib/types";
import { EvidenceDrawer } from "@/components/evidence/Drawer";
import { editWriteup, regenerateWriteup, saveWriteup } from "@/lib/actions/chats";
import { draftMessages } from "@/lib/actions/messages";
import { specs } from "@/lib/writeup/messages";
import { render, type WriteupData } from "@/lib/writeup/render";

/** The resolution review (SummaryV4): "In short", the resolution notes, then the detail in four folded sections,
 * and a bar that stays at the bottom. Untick a record and a signal that rested on it drops out; saving makes it a
 * knowledge article; any edit unsaves it. */
export interface ReviewHead { incident: IncidentSummary | null; ticket: TicketView | null; meta: string; title: string }

export function SummaryScreen({ chatId, data, head }: { chatId: string; data: WriteupData; head: ReviewHead }) {
  const [kept, setKept] = useState<Set<string>>(new Set(data.writeup?.kept ?? []));
  const [note, setNote] = useState(data.resolution?.note ?? "");
  const [editing, setEditing] = useState(false);
  const [version, setVersion] = useState(data.writeup?.version ?? 1);
  const [saved, setSaved] = useState<string | null>(data.writeup?.saved_at ?? null);
  const [drawer, setDrawer] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const [drafting, setDrafting] = useState(false);
  const [open, setOpen] = useState<Record<string, boolean>>({ ev: data.resolution?.path === "own" });
  const router = useRouter();
  const w = useMemo(() => render(data, kept, note), [data, kept, note]);
  const own = data.resolution?.path === "own";

  function change(next: Set<string>, nextNote?: string) {
    setKept(next);
    setSaved(null);
    start(async () => {
      const r = await editWriteup(chatId, [...next], nextNote);
      if (!r.ok) setError(r.error ?? "The change was not kept.");
    });
  }
  const toggle = (h: string) => { const n = new Set(kept); if (n.has(h)) n.delete(h); else n.add(h); change(n); };
  const res = data.resolution;
  const dropped = w.signals.filter((g) => g.dropped).length;
  const sec = (key: string, title: string, sub: React.ReactNode, children: React.ReactNode, warn = false) => (
    <section className="card sec" aria-labelledby={`${key}-h`}>
      <button type="button" className="sec-btn" aria-expanded={Boolean(open[key])} onClick={() => setOpen((o) => ({ ...o, [key]: !o[key] }))}>
        <span className="chev" aria-hidden="true">›</span><span id={`${key}-h`} className="sec-t" role="heading" aria-level={3}>{title}</span>
        <span className={`sec-sub${warn ? " warn" : ""}`}>{sub}</span>
      </button>
      {open[key] ? <div className="sec-body">{children}</div> : null}
    </section>
  );
  const banner = saved
    ? <StatusBanner tone="ok" title="Saved as a knowledge article." text="Next: draft the resolution communications, from the button at the bottom." />
    : <StatusBanner tone="ok" title={`Incident resolved${res ? ` at ${res.resolved_at} UTC` : ""}${res?.code && /Work Around/.test(res.code) ? " by workaround" : ""}.`}
        text="Review the resolution record below, then save it as a knowledge article at the bottom of the page." />;
  return (
    <div className="v-res">
      {head.incident ? <IncidentHeader incident={head.incident} ticket={head.ticket} resolved banner={banner} meta={head.meta} /> : (
        <header className="top"><div className="top-in"><div className="inc"><span className="inc-title">{head.title}</span><span className="inc-meta">{head.meta}</span></div></div>{banner}</header>
      )}
      <main className="main">
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="label">Resolution record · {saved ? "saved" : `draft ${version}`}</span>
          <h1 className="title" style={{ margin: 0 }}>{w.title}</h1>
          <span className="small muted">{w.pathLine}</span>
        </div>
        <section className="blk" aria-labelledby="short-h">
          <h2 id="short-h" className="label" style={{ margin: 0 }}>In short</h2>
          <dl className="card short">
            {w.short.map((r) => <div key={r.k} className="short-row"><dt>{r.k}</dt><dd>{r.v}</dd></div>)}
          </dl>
        </section>
        <section className="blk" aria-labelledby="res-h">
          <div className="blk-head">
            <h2 id="res-h" className="label" style={{ margin: 0 }}>Resolution notes{res?.resolved_by ? ` · ${res.resolved_by}` : ""}</h2>
            <span style={{ marginLeft: "auto" }}>
              <Button variant="quiet" size="sm" onClick={() => { if (editing) change(kept, note); setEditing(!editing); }}>{editing ? "Done" : "Edit"}</Button>
            </span>
          </div>
          {editing ? (
            <textarea className="sm-input" rows={4} aria-label="Resolution notes" style={{ fontSize: 15, lineHeight: "23px", padding: "10px 12px" }}
              value={note} onChange={(e) => { setNote(e.target.value); setSaved(null); }} />
          ) : <p className="card" style={{ margin: 0, padding: "14px 18px", fontSize: 15, lineHeight: "23px", whiteSpace: "pre-line" }}>{note || "No notes yet."}</p>}
        </section>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <h2 className="label" style={{ margin: 0 }}>The detail</h2>
          {sec("what", "Cause and timeline", own ? "Smalls’ last picture, before you resolved it another way" : "Probable cause, trigger, mechanism and business impact, with the evidence for each", (
            <>
              <Prose text={w.narrative} className="narr" />
              <span className="label">{w.chainTitle}</span>
              <ol className="chain">
                {w.chain.map((c, i) => (
                  <li key={i}>
                    <span className="chain-k"><span className="n">{i + 1}</span>{c.step}</span>
                    <div className="chain-v"><span>{c.text}</span>{c.handles.length ? <Handles ids={c.handles} onOpen={setDrawer} /> : null}</div>
                  </li>
                ))}
              </ol>
            </>
          ))}
          {sec("sig", "How to recognise it next time",
            dropped ? `${w.signals.length - dropped} of ${w.signals.length} kept · ${dropped} left out for lack of evidence` : !w.signals.length ? "None yet: the report marks no alert as this incident’s and gives no timeline" : `${w.signals.length === 1 ? "1 pattern" : `${w.signals.length} patterns`} that would make a later incident look like this one`, (
            <>
              <span className="small muted">What would make a later incident look like this one.</span>
              <ol className="list">
                {w.signals.length ? w.signals.map((g) => (
                  <li className="li" key={g.n} data-signal={g.n}>
                    <span className="time" style={{ flex: "none", width: 16, color: "var(--action)" }}>{g.n}</span>
                    <div style={{ flex: "1 1 auto", minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
                      {g.dropped ? (
                        <>
                          <span style={{ color: "var(--ink-muted)", textDecoration: "line-through" }}>{g.text}</span>
                          <span className="small" style={{ color: "var(--attention)" }}>Left out: none of its evidence is kept. Tick a record under Evidence to bring it back.</span>
                        </>
                      ) : (
                        <>
                          <span style={{ fontWeight: 600 }}>{g.text}</span>
                          <span className="muted" style={{ fontSize: 13, lineHeight: "18px" }}>{g.where}</span>
                          <Handles ids={g.handles} onOpen={setDrawer} />
                        </>
                      )}
                    </div>
                  </li>
                )) : <li className="li"><span className="small muted">The report marks no alert as this incident’s and gives no timeline.</span></li>}
              </ol>
            </>
          ), dropped > 0)}
          {sec("out", "Ruled out", w.ruledOut.length ? `${w.ruledOut.length === 1 ? "1 thing" : `${w.ruledOut.length} things`}, so the next investigation doesn’t chase them` : "Nothing was ruled out", (
            <ul className="list">
              {w.ruledOut.length ? w.ruledOut.map((r, i) => (
                <li className="li" key={i} style={{ flexWrap: "wrap", alignItems: "baseline", gap: "4px 12px" }}>
                  <span style={{ flex: "0 1 230px", fontWeight: 500 }}><span className="time" style={{ color: "var(--ink-muted)" }} aria-hidden="true">✕ </span>{r.what}</span>
                  <span style={{ flex: "1 1 280px", minWidth: 0, fontSize: 13, lineHeight: "18px" }}>{r.why}</span>
                </li>
              )) : <li className="li"><span className="small muted">Nothing was ruled out.</span></li>}
            </ul>
          ))}
          {sec("ev", "Evidence", <><span aria-live="polite">{w.kept} of {w.evidence.length} records kept</span> · choose what the write-up keeps</>, (
            <>
              <div className="ev-tools">
                <span className="small muted" style={{ flex: "1 1 280px" }}>
                  {own ? "You resolved it another way, so nothing is kept yet. Tick the evidence that helped your decision." : "Everything Smalls gathered is kept. Untick what didn’t matter; a signal with no kept evidence drops out."}
                </span>
                <Button variant="quiet" size="sm" onClick={() => change(new Set(w.evidence.map((e) => e.handle)))}>Keep all</Button>
                <Button variant="quiet" size="sm" onClick={() => change(new Set())}>Clear</Button>
              </div>
              <div className="rail">
                {w.evidence.map((e, i) => (
                  <div key={e.handle} className={`evrow${e.kept ? "" : " off"}`}>
                    <input type="checkbox" id={`ev-${i}`} checked={e.kept} onChange={() => toggle(e.handle)} />
                    <div className="ev-body">
                      <label htmlFor={`ev-${i}`} className="evtext">{e.text}</label>
                      <div className="ev-meta"><Handle id={e.handle} onOpen={setDrawer} /><span className="small muted">{e.source}</span></div>
                    </div>
                  </div>
                ))}
              </div>
            </>
          ))}
        </div>
        <div className="actbar" aria-live="polite">
          {saved ? (
            <>
              <span className="saved-mark" aria-hidden="true">✓</span>
              <div style={{ flex: "1 1 320px", minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
                <strong style={{ color: "var(--ok)" }}>Saved as a knowledge article</strong>
                <span className="small muted">Searchable alongside past incidents and runbooks. Any edit here unsaves it until you save again.</span>
              </div>
              <Button variant="primary" disabled={drafting} onClick={() => { setDrafting(true); setError(null); start(async () => {
                // Drafts only the audiences not drafted yet, so coming back here never replaces an edited message.
                const r = await draftMessages(chatId, specs(data.report, data.resolution), true);
                if (r.ok) router.push(`/chats/${chatId}/messages`); else { setError(r.error ?? "The communications were not drafted."); setDrafting(false); }
              }); }}>{drafting ? "Drafting the communications…" : "Draft communications →"}</Button>
            </>
          ) : (
            <>
              <Button variant="primary" disabled={!note.trim() || pending} onClick={() => start(async () => {
                setError(null);
                const r = await saveWriteup(chatId, { version, title: w.title, body: w.body, terms: w.terms });
                if (r.ok && r.writeup) { setSaved(r.writeup.saved_at); setEditing(false); router.refresh(); } else setError(r.error ?? "Not saved.");
              })}>Save as knowledge article</Button>
              <Link className="lbtn" href={`/chats/${chatId}`}>Refine in chat</Link>
              <Button variant="quiet" disabled={pending} onClick={() => start(async () => {
                const r = await regenerateWriteup(chatId);
                if (r.ok && r.writeup) { setVersion(r.writeup.version); setSaved(null); } else setError(r.error ?? "Not regenerated.");
              })}>Regenerate</Button>
              <span className="small muted" style={{ flex: "1 1 260px", minWidth: 0 }}>
                {version === 1 ? `Draft 1 · written by Smalls from the diagnosis and ${res?.resolved_by ? `${res.resolved_by}’s` : "your"} resolution notes` : `Draft ${version}, regenerated from the latest conversation. Your notes and evidence choices were kept.`}
              </span>
            </>
          )}
          {error ? <span role="alert" className="small" style={{ color: "var(--danger)" }}>{error}</span> : null}
        </div>
      </main>
      {drawer ? <EvidenceDrawer key={drawer} chatId={chatId} handle={drawer} onClose={() => setDrawer(null)} /> : null}
    </div>
  );
}
