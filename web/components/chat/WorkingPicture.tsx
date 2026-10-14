"use client";

import { Confidence, StatusTag } from "@/components/ds";
import { tally, type ChatState } from "@/lib/chat/reduce";

/** The working picture beside the conversation: claims in play, claims ruled out, and the reads tally. */
export function WorkingPicture({ state }: { state: ChatState }) {
  const all = Object.values(state.board);
  const live = all.filter((h) => h.status !== "rejected").sort((a, b) => (a.status === "leading" ? -1 : b.status === "leading" ? 1 : b.confidence - a.confidence));
  const dead = all.filter((h) => h.status === "rejected");
  const t = tally(state);
  return (
    <aside aria-label="Working picture" style={{ flex: "1 1 320px", maxWidth: 400, minWidth: 0, display: "flex", flexDirection: "column", gap: 12, padding: "24px 24px 24px 0", boxSizing: "border-box", position: "sticky", top: 0 }}>
      <section className="panel" aria-live="polite">
        <div className="panel-head"><h2 className="heading" style={{ margin: 0 }}>Working picture</h2></div>
        {!all.length ? <p className="small muted" style={{ margin: 0 }}>Builds here as Smalls reads. Each claim gets a status and a confidence that moves as evidence comes in.</p> : (
          <>
            <div className="claims">
              <span className="label" style={{ paddingBottom: 2 }}>In play</span>
              {live.length ? live.map((h) => (
                <div className="claim" key={h.id}>
                  <span className="claim-text">{h.statement}</span>
                  <div className="claim-meta">
                    <StatusTag status={h.status} /><span className="label">{h.role}</span><span className="grow" />
                    <Confidence value={h.confidence} previous={h.first} />
                  </div>
                </div>
              )) : <span className="small muted">Nothing in play.</span>}
            </div>
            {dead.length ? (
              <div className="claims">
                <span className="label" style={{ padding: "6px 0 2px" }}>Ruled out</span>
                {dead.map((h) => (
                  <div className="claim out" key={h.id}>
                    <span className="claim-text">{h.statement}</span>
                    <div className="claim-meta"><span className="label">{h.role}</span><span className="grow" /><Confidence value={h.confidence} previous={h.first} muted /></div>
                  </div>
                ))}
              </div>
            ) : null}
            <span className="small muted">The tick on each bar is where the claim started.</span>
          </>
        )}
      </section>
      <section className="panel">
        <h2 className="heading" style={{ margin: 0 }}>Reads</h2>
        <div className="tally">
          {([["proposed", t.proposed], ["run", t.run], ["rejected", t.rejected], ["skipped", t.skipped]] as const).map(([label, n]) => (
            <div key={label}><b>{n}</b><span>{label}</span></div>
          ))}
        </div>
        <span className="small muted">Rejections and their reasons go into the summary.</span>
      </section>
    </aside>
  );
}
