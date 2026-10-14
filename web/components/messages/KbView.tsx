import Link from "next/link";
import { Handle, Handles, Notice } from "@/components/ds";
import { day } from "@/lib/format";
import type { KbHit, MessagesView } from "@/lib/types";
import type { Writeup, WriteupData } from "@/lib/writeup/render";

/** The knowledge-base entry, read-only: what was saved, the terms that would bring it up, and how it shows in a
 * later search, which is a real search of the base for those terms (section 4, Messages; 7.5). */
export function KbView({ chatId, data, entry, w, hits, incidentHandle, onOpen }: {
  chatId: string; data: WriteupData; entry: MessagesView["entry"]; w: Writeup | null; hits: KbHit[]; incidentHandle: string | null; onOpen: (h: string) => void;
}) {
  const res = data.resolution;
  const rank = hits.findIndex((h) => h.chat_id === chatId);
  const hit = rank >= 0 ? hits[rank] : null;
  return (
    <section aria-label="Knowledge base entry" style={{ flex: "999 1 560px", minWidth: 0, display: "flex", flexDirection: "column", gap: 12 }}>
      <p className="narr muted" style={{ margin: 0 }}>Not sent to anyone. It sits alongside past incidents and runbooks, so a later incident with similar signals is easier to identify.</p>
      {!entry || !w ? (
        <Notice tone="clamped">
          The write-up is not in the knowledge base: it was never saved, or it changed after saving. <Link href={`/chats/${chatId}/summary`}>Save it from the summary</Link>.
        </Notice>
      ) : (
        <>
          <article className="card" aria-label="The knowledge base entry" style={{ display: "flex", flexDirection: "column", gap: 14, padding: "16px 20px" }}>
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
              {incidentHandle ? <Handle id={incidentHandle} onOpen={onOpen} /> : null}
              <span className="time muted">saved {day(entry.saved_at)}{res ? ` · resolved ${res.resolved_at} UTC` : ""}{data.incident ? ` · ${data.incident}` : ""}</span>
            </div>
            <h2 className="title" style={{ margin: 0 }}>{entry.title}</h2>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="label">How it was resolved · {res?.resolved_by ?? "the commander"}</span>
              <p className="narr kb-text" style={{ margin: 0 }}>{w.note}</p>
            </div>
            {w.signals.some((s) => !s.dropped) ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span className="label">Check these first</span>
                <ol className="kb-list">{w.signals.filter((s) => !s.dropped).map((s) => <li key={s.n}>{s.text}</li>)}</ol>
              </div>
            ) : null}
            {w.ruledOut.length ? (
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <span className="label">Ruled out</span>
                <ul className="kb-list">{w.ruledOut.map((r, i) => <li key={i}>{r.what}</li>)}</ul>
              </div>
            ) : null}
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span className="label">Evidence kept · {w.kept}</span>
              {w.kept ? <Handles ids={w.evidence.filter((e) => e.kept).map((e) => e.handle)} onOpen={onOpen} /> : <span className="small muted">None kept.</span>}
            </div>
          </article>
          <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10, padding: "14px 16px" }}>
            <span className="label">What would bring it up</span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>{w.terms.map((t) => <span key={t} className="term">{t}</span>)}</div>
            <span className="label" style={{ paddingTop: 4 }}>How it shows up in a later search</span>
            {hit ? (
              <>
                <p className="narr" style={{ margin: 0, padding: "10px 14px", background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 6 }}>
                  {data.incident ? <span className="time" style={{ color: "var(--action)" }}>{data.incident} </span> : null}
                  {hit.title} (saved {day(hit.saved_at)}).{hit.snippet ? ` ${hit.snippet}` : ""}
                </p>
                <span className="small muted">{rank === 0 ? `First of ${hits.length} ${hits.length === 1 ? "match" : "matches"}` : `Match ${rank + 1} of ${hits.length}`} for these terms, searched now.</span>
              </>
            ) : <Notice tone="clamped">A search for these terms does not find this entry.</Notice>}
            <span className="small muted">A match points to a similar past case. It doesn’t decide the outcome: each incident still needs its own evidence.</span>
          </div>
        </>
      )}
    </section>
  );
}
