"use client";

import { useState } from "react";
import { Confidence, StatusTag } from "@/components/ds";
import { tally, type ChatState } from "@/lib/chat/reduce";

/** The working picture, in the side panel: the claims in play, the ones ruled out folded away, and the checks so
 * far. Each claim's bar has a tick where it started. */
export function WorkingPicture({ state }: { state: ChatState }) {
  const [ruled, setRuled] = useState(false);
  const all = Object.values(state.board);
  const live = all.filter((h) => h.status !== "rejected").sort((a, b) => (a.status === "leading" ? -1 : b.status === "leading" ? 1 : b.confidence - a.confidence));
  const dead = all.filter((h) => h.status === "rejected");
  const t = tally(state);
  if (!all.length) return <p className="small muted" style={{ margin: 0 }}>Builds here as Smalls checks. Each claim gets a status and a confidence that moves as evidence comes in.</p>;
  return (
    <>
      <div className="claims">
        {live.length ? live.map((h) => (
          <div className="claim" key={h.id}>
            <span className="claim-text">{h.statement}</span>
            <div className="claim-meta">
              <StatusTag status={h.status}>{h.status === "leading" ? "Probable cause" : undefined}</StatusTag><span className="label">{h.role}</span><span className="grow" />
              <Confidence value={h.confidence} previous={h.first} />
            </div>
          </div>
        )) : <span className="small muted">Nothing in play.</span>}
      </div>
      {dead.length ? (
        <>
          <button type="button" className="disc" aria-expanded={ruled} onClick={() => setRuled(!ruled)}><span className="chev" aria-hidden="true">›</span>{ruled ? "Hide ruled out" : `Ruled out · ${dead.length}`}</button>
          {ruled ? (
            <div className="claims">
              {dead.map((h) => (
                <div className="claim out" key={h.id}>
                  <span className="claim-text">{h.statement}</span>
                  <div className="claim-meta"><span className="label">{h.role}</span><span className="grow" /><Confidence value={h.confidence} previous={h.first} muted /></div>
                </div>
              ))}
            </div>
          ) : null}
        </>
      ) : null}
      <span className="small muted">The tick on each bar is where the claim started, before these checks.</span>
      <div className="sp-sec">
        <span className="label">Checks so far</span>
        <div className="tally">
          {([["proposed", t.proposed], ["run", t.run], ["rejected", t.rejected], ["skipped", t.skipped]] as const).map(([label, n]) => (
            <div key={label}><b>{n}</b><span>{label}</span></div>
          ))}
        </div>
        <span className="small muted">Rejections and their reasons go into the summary.</span>
      </div>
    </>
  );
}
