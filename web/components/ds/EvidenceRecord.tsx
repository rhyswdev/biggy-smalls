import { Handle } from "./Handle";
import { Marked } from "./Marked";
import { cx, Label } from "./util";

export interface EvidenceRecordData {
  handle: string;
  level?: string | null;
  source?: string | null;
  t_utc?: string | null;
  host?: string | null;
  sampling?: string | null;
  summary?: React.ReactNode;
  raw: string;
  raw_format?: string | null;
}

/** One record: what Smalls normalised beside the raw text the source wrote. The two are never merged. */
export function EvidenceRecord({ record, highlight, className }: { record: EvidenceRecordData; highlight?: string[]; className?: string }) {
  const r = record;
  return (
    <article className={cx("sm-rec", className)}>
      <div className="sm-rec-head">
        <Handle id={r.handle} active />
        {r.level ? <span className={`sm-level sm-level-${r.level}`}>{r.level}</span> : null}
        <span className="sm-muted" style={{ fontSize: 12 }}>{r.source}</span>
      </div>
      <div className="sm-rec-grid">
        <div className="sm-rec-col">
          <Label>Normalised</Label>
          <dl className="sm-params">
            <dt>t_utc</dt><dd>{r.t_utc ?? "–"}</dd>
            {r.host ? <><dt>host</dt><dd>{r.host}</dd></> : null}
            {r.sampling ? <><dt>sampling</dt><dd>{r.sampling}</dd></> : null}
          </dl>
          {r.summary ? <div className="sm-rec-summary">{r.summary}</div> : null}
        </div>
        <div className="sm-rec-col">
          <Label>Raw, as the source wrote it</Label>
          <pre className="sm-pre"><Marked text={r.raw} terms={highlight} /></pre>
          {r.raw_format ? <span className="sm-muted" style={{ fontSize: 12 }}>{r.raw_format}</span> : null}
        </div>
      </div>
    </article>
  );
}
