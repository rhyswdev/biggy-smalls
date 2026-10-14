"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { Prose } from "@/components/chat/Prose";
import { Button, Handle, Handles } from "@/components/ds";
import { EvidenceDrawer } from "@/components/evidence/Drawer";
import { editWriteup, regenerateWriteup, saveWriteup } from "@/lib/actions/chats";
import { draftMessages } from "@/lib/actions/messages";
import { specs } from "@/lib/writeup/messages";
import { render, type WriteupData } from "@/lib/writeup/render";

/** The write-up beside its evidence (section 4, Summary): untick a record and a signal that rested on it drops
 * out; saving puts it in the knowledge base; any edit unsaves it. */
export function SummaryScreen({ chatId, data, children }: { chatId: string; data: WriteupData; children?: React.ReactNode }) {
  const [kept, setKept] = useState<Set<string>>(new Set(data.writeup?.kept ?? []));
  const [note, setNote] = useState(data.resolution?.note ?? "");
  const [editing, setEditing] = useState(false);
  const [version, setVersion] = useState(data.writeup?.version ?? 1);
  const [saved, setSaved] = useState<string | null>(data.writeup?.saved_at ?? null);
  const [drawer, setDrawer] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, start] = useTransition();
  const [drafting, setDrafting] = useState(false);
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
  return (
    <div className="v-sum" style={{ minHeight: "100vh" }}>
      {children}
      <main style={{ maxWidth: 1160, margin: "0 auto", padding: "24px 24px 0", display: "flex", flexDirection: "column", gap: 24, boxSizing: "border-box" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, maxWidth: 760 }}>
          <span className="label">Knowledge base write-up · {saved ? "saved" : `draft ${version}`}</span>
          <h1 className="title" style={{ margin: 0 }}>{w.title}</h1>
          <span className="time muted">{w.pathLine}</span>
          <p className="narr muted" style={{ paddingTop: 4 }}>Saved as {data.incident ?? "this incident"}’s write-up, where it helps identify similar issues later. Keep what mattered and remove what didn’t, so it doesn’t point anyone the wrong way.</p>
        </div>
        <div className="cols">
          <div className="doc">
            <section className="sec" aria-labelledby="res-h">
              <div className="sec-head">
                <h2 id="res-h" className="heading" style={{ margin: 0 }}>How it was resolved</h2>
                <span className="small muted">In {data.resolution?.resolved_by ?? "the commander"}’s words</span>
                <span style={{ marginLeft: "auto" }}>
                  <Button variant="quiet" size="sm" onClick={() => { if (editing) change(kept, note); setEditing(!editing); }}>{editing ? "Done" : "Edit"}</Button>
                </span>
              </div>
              {editing ? (
                <textarea className="sm-input" rows={4} aria-label="How it was resolved" style={{ fontSize: 15, lineHeight: "23px", padding: "10px 12px" }}
                  value={note} onChange={(e) => { setNote(e.target.value); setSaved(null); }} />
              ) : <p className="card" style={{ margin: 0, padding: "14px 16px", fontSize: 15, lineHeight: "23px", whiteSpace: "pre-line" }}>{note || "No note yet."}</p>}
            </section>
            <section className="sec" aria-labelledby="what-h">
              <div className="sec-head">
                <h2 id="what-h" className="heading" style={{ margin: 0 }}>What happened</h2>
                <span className="small muted">{own ? "Smalls’ last picture, before you resolved it another way" : "Smalls’ account, from the evidence"}</span>
              </div>
              <div className="card" style={{ display: "flex", flexDirection: "column", gap: 12, padding: "16px 20px" }}>
                <Prose text={w.narrative} style={{ fontSize: 15, lineHeight: "23px" }} />
                <span className="label">{w.chainTitle}</span>
                <ol className="chain">
                  {w.chain.map((c, i) => (
                    <li key={i}>
                      <span className="chain-k"><span className="n">{i + 1}</span>{c.step}</span>
                      <div className="chain-v"><span>{c.text}</span>{c.handles.length ? <Handles ids={c.handles} onOpen={setDrawer} /> : null}</div>
                    </li>
                  ))}
                </ol>
              </div>
            </section>
            <section className="sec" aria-labelledby="sig-h">
              <div className="sec-head">
                <h2 id="sig-h" className="heading" style={{ margin: 0 }}>Signals that mark this issue</h2>
                <span className="small muted">What would make a later incident look like this one</span>
              </div>
              <ol className="list card">
                {w.signals.length ? w.signals.map((g) => (
                  <li className="li" key={g.n} data-signal={g.n}>
                    <span className="time" style={{ flex: "none", width: 16, color: "var(--action)" }}>{g.n}</span>
                    <div style={{ flex: "1 1 auto", minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
                      {g.dropped ? (
                        <>
                          <span style={{ color: "var(--ink-muted)", textDecoration: "line-through" }}>{g.text}</span>
                          <span className="small" style={{ color: "var(--attention)" }}>Left out: none of its evidence is kept. Tick a record on the right to bring it back.</span>
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
            </section>
            <section className="sec" aria-labelledby="out-h">
              <div className="sec-head">
                <h2 id="out-h" className="heading" style={{ margin: 0 }}>Ruled out</h2>
                <span className="small muted">So the next investigation doesn’t chase them</span>
              </div>
              <ul className="list card">
                {w.ruledOut.length ? w.ruledOut.map((r, i) => (
                  <li className="li" key={i} style={{ flexWrap: "wrap", alignItems: "baseline", gap: "4px 12px" }}>
                    <span style={{ flex: "0 1 240px", fontWeight: 500 }}><span className="time" style={{ color: "var(--ink-muted)" }} aria-hidden="true">✕ </span>{r.what}</span>
                    <span style={{ flex: "1 1 300px", minWidth: 0, fontSize: 13, lineHeight: "18px" }}>{r.why}</span>
                  </li>
                )) : <li className="li"><span className="small muted">Nothing was ruled out.</span></li>}
              </ul>
            </section>
          </div>
          <aside className="rail" aria-labelledby="ev-h">
            <div className="card">
              <div className="ev-head">
                <h2 id="ev-h" className="heading" style={{ margin: 0 }}>Evidence</h2>
                <span className="time muted" aria-live="polite">{w.kept} of {w.evidence.length} kept</span>
                <span className="grow" />
                <Button variant="quiet" size="sm" onClick={() => change(new Set(w.evidence.map((e) => e.handle)))}>Keep all</Button>
                <Button variant="quiet" size="sm" onClick={() => change(new Set())}>Clear</Button>
              </div>
              <p className="small muted" style={{ margin: 0, padding: "0 14px 12px" }}>
                {own ? "You resolved it another way, so nothing is kept to start with: tick only the records that helped your decision." : "Everything Smalls cited is kept to start with. Untick what didn’t matter."}
              </p>
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
          </aside>
        </div>
        <div className="actbar" aria-live="polite">
          {saved ? (
            <>
              <span className="saved-mark" aria-hidden="true">✓</span>
              <div style={{ flex: "1 1 320px", minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
                <strong style={{ color: "var(--ok)" }}>✓ Saved to the knowledge base</strong>
                <span className="small muted">Searchable alongside past incidents and runbooks. Any edit here unsaves it until you save again.</span>
              </div>
              <Button variant="primary" disabled={drafting} onClick={() => { setDrafting(true); setError(null); start(async () => {
                // Drafts only the audiences not drafted yet, so coming back here never replaces an edited message.
                const r = await draftMessages(chatId, specs(data.report, data.resolution), true);
                if (r.ok) router.push(`/chats/${chatId}/messages`); else { setError(r.error ?? "The messages were not drafted."); setDrafting(false); }
              }); }}>{drafting ? "Drafting the messages…" : "Draft stakeholder messages"}</Button>
            </>
          ) : (
            <>
              <Button variant="primary" disabled={!note.trim() || pending} onClick={() => start(async () => {
                setError(null);
                const r = await saveWriteup(chatId, { version, title: w.title, body: w.body, terms: w.terms });
                if (r.ok && r.writeup) { setSaved(r.writeup.saved_at); setEditing(false); } else setError(r.error ?? "Not saved.");
              })}>Save to knowledge base</Button>
              <Link className="lbtn" href={`/chats/${chatId}`}>Refine in chat</Link>
              <Button variant="quiet" disabled={pending} onClick={() => start(async () => {
                const r = await regenerateWriteup(chatId);
                if (r.ok && r.writeup) { setVersion(r.writeup.version); setSaved(null); } else setError(r.error ?? "Not regenerated.");
              })}>Regenerate</Button>
              <span className="small muted" style={{ flex: "1 1 260px", minWidth: 0 }}>
                {version === 1 ? "Draft 1, from the conversation as it stands." : `Draft ${version}, regenerated from the latest conversation. Your note and evidence choices were kept.`}
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
