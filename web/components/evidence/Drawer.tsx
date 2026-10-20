"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button, EvidenceRecord, Handle, Notice } from "@/components/ds";
import { readEvidence } from "@/lib/actions/reads";
import { clock, day } from "@/lib/format";
import type { EvidenceView } from "@/lib/types";

const CLAIM: Record<string, string> = { support: "Supports", against: "Against", evidence: "Evidence", cited: "Cited" };

/** The evidence drawer: any handle opens its native record as it stood at the chat's `now`, what the run was
 * shown of it, the claims that cite it, its related records with a back trail, and the native query that
 * reproduces it. Esc closes it and focus returns to the handle that opened it. */
export function EvidenceDrawer({ chatId, handle, onClose }: { chatId: string; handle: string; onClose: () => void }) {
  const [trail, setTrail] = useState<string[]>([handle]);
  const [loaded, setLoaded] = useState<{ handle: string; view: EvidenceView | null; error: string | null } | null>(null);
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const current = trail[trail.length - 1]!;

  useEffect(() => {
    opener.current = document.activeElement as HTMLElement | null;
    panel.current?.focus();
    return () => { opener.current?.focus?.(); };
  }, []);
  useEffect(() => {
    let live = true;
    readEvidence(chatId, current).then((r) => {
      if (!live) return;
      const error = r.evidence ? null : r.status === 404 ? `${current} is not in this chat or in the data at its now.` : r.error ?? "The record could not be loaded.";
      setLoaded({ handle: current, view: r.evidence ?? null, error });
    });
    return () => { live = false; };
  }, [chatId, current]);
  const view = loaded?.handle === current ? loaded.view : null;
  const error = loaded?.handle === current ? loaded.error : null;

  const go = useCallback((h: string) => setTrail((t) => (t[t.length - 1] === h ? t : [...t, h])), []);
  const back = () => setTrail((t) => (t.length > 1 ? t.slice(0, -1) : t));
  const onKey = (e: React.KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); } };

  const rec = view?.record;
  const shown = view?.shown[view.shown.length - 1];
  const related = rec ? [...(rec.parent ? [rec.parent] : []), ...rec.children].filter((h) => h !== current) : [];
  const when = rec?.known_utc ?? shown?.t_known ?? null;
  return (
    <div ref={panel} className="v-ev" role="dialog" aria-modal="false" aria-label={`Evidence ${current}`} tabIndex={-1} onKeyDown={onKey}
      style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: "100%", maxWidth: "var(--drawer-width)", zIndex: 30, overflowY: "auto",
        background: "var(--surface-raised)", boxShadow: "var(--shadow-drawer)", outline: "none" }}>
      <div style={{ position: "sticky", top: 0, zIndex: 1, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8, padding: "12px 16px", background: "var(--surface-raised)", borderBottom: "1px solid var(--line)" }}>
        <span className="label">Evidence</span>
        <span className="time" style={{ color: "var(--ink-muted)" }}>{when ? `known ${day(when)} ${clock(when, true)} UTC` : ""}</span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          {trail.length > 1 ? <Button variant="quiet" size="sm" onClick={back}>← {trail[trail.length - 2]}</Button> : null}
          <Button size="sm" onClick={onClose} aria-label="Close evidence drawer">Close</Button>
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 16, padding: 16 }}>
        {error ? (
          <>
            <Handle id={current} missing />
            <Notice tone="error">{error}</Notice>
          </>
        ) : !view ? <p className="small muted" aria-live="polite">Loading {current}…</p> : (
          <>
            {view.query?.ends_at_now ? <Notice tone="clamped">The call that showed this looked up to now, {clock(view.query.window?.[1], false)} UTC. Nothing after it is visible.</Notice> : null}
            {rec?.withheld.length ? <Notice>Withheld at now, because their served value was not yet true: {rec.withheld.join(", ")}.</Notice> : null}
            {rec ? (
              <EvidenceRecord record={{
                handle: current, source: [rec.source, rec.kind].filter(Boolean).join(" · "),
                t_utc: rec.event_utc ?? (rec.planned_utc ? `planned ${rec.planned_utc}` : null), host: null,
                sampling: rec.vendor_time ? `${rec.vendor_time}${rec.zone ? ` (${rec.zone})` : ""}` : null,
                summary: rec.title ?? undefined, raw: rec.native, raw_format: rec.format === "json" ? "JSON, as served" : "text, as served",
              }} />
            ) : shown ? (
              <EvidenceRecord record={{ handle: current, source: view.class === "aggregate" ? "computed by the tool" : view.class, t_utc: shown.t_known,
                summary: "As the run was shown it: this handle is minted by a call, not stored.", raw: shown.text, raw_format: `shown by ${shown.call_id}` }} />
            ) : view.note ? (
              <EvidenceRecord record={{ handle: current, source: "Major Incident Manager", t_utc: view.note.submitted_at, raw: view.note.text, raw_format: "a note" }} />
            ) : null}
            <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <h2 className="heading" style={{ margin: 0 }}>Cited by</h2>
              {view.claims.length ? view.claims.map((c, i) => (
                <div key={i} style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: "4px 8px", padding: "8px 12px", border: "1px solid var(--line)", borderRadius: 6 }}>
                  <span className="label" style={{ flex: "0 0 64px" }}>{CLAIM[c.as] ?? c.as}</span>
                  <span style={{ flex: "1 1 280px", fontSize: 13, lineHeight: "18px" }}>{c.text}</span>
                </div>
              )) : <span style={{ fontSize: 13, color: "var(--ink-muted)" }}>Not cited in the current report.</span>}
            </section>
            <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <h2 className="heading" style={{ margin: 0 }}>Related records</h2>
              <span style={{ fontSize: 12, lineHeight: "16px", color: "var(--ink-muted)" }}>Its parent and children, as the source links them. Opening one keeps a back trail.</span>
              {related.length ? <div className="sm-handles">{related.map((h) => <Handle key={h} id={h} onOpen={go} />)}</div>
                : <span className="small muted">None.</span>}
              {view.shown.length ? <span className="small muted">Shown in this chat by {[...new Set(view.shown.map((s) => s.call_id))].join(", ")}{view.referenced_by.length ? `; listed by ${view.referenced_by.join(", ")}` : ""}.</span> : null}
            </section>
            {view.query?.native_query ? (
              <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <h2 className="heading" style={{ margin: 0 }}>Reproduce in the source</h2>
                <pre className="sm-pre">{view.query.native_query}</pre>
              </section>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}
