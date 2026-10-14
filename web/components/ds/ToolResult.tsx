import { Handles } from "./Handle";
import { Marked } from "./Marked";
import { Notice } from "./Notice";
import { StatusTag } from "./StatusTag";
import { cx, Label, utc } from "./util";

export interface ToolResultData {
  id?: string;
  status?: "ok" | "error" | string;
  text: string;
  window?: { since?: string; until?: string; clamped?: boolean } | null;
  sampling?: { keep_1_in: number; per?: string } | null;
  handles?: string[];
  native_query?: string | null;
}

/** What a call returned, shown as the tool wrote it. */
export function ToolResult({ result, tool, highlight, onOpenHandle, activeHandle, className }: {
  result: ToolResultData; tool?: string; highlight?: string[]; onOpenHandle?: (id: string) => void; activeHandle?: string | null; className?: string;
}) {
  const w = result.window ?? {};
  const s = result.sampling;
  const isErr = result.status === "error";
  return (
    <article className={cx("sm-call", isErr ? "sm-call-error" : "sm-call-ok", className)}>
      <div className="sm-call-head">
        <StatusTag status={isErr ? "error" : "ok"} />
        {tool ? <span className="sm-call-tool">{tool}</span> : null}
        <span className="sm-call-id">{result.id}</span>
      </div>
      <pre className="sm-pre"><Marked text={result.text} terms={highlight} /></pre>
      {w.clamped ? <Notice tone="clamped">Window clamped to the checkpoint ({utc(w.until, false)} UTC). Nothing after it is visible.</Notice> : null}
      <div className="sm-call-meta">
        {w.since ? <span>Window <span className="sm-mono">{`${utc(w.since, false)}–${utc(w.until, false)} UTC`}</span></span> : null}
        {s ? <span>Sampled <span className="sm-mono">{`1 in ${s.keep_1_in}${s.per ? ` per ${s.per}` : ""}`}</span>; counts are estimates</span> : null}
      </div>
      {result.handles?.length ? <div className="sm-hyp-ev"><Label>Handles</Label><Handles ids={result.handles} onOpen={onOpenHandle} active={activeHandle} /></div> : null}
      {result.native_query ? <details className="sm-details"><summary>Native query</summary><pre className="sm-pre">{result.native_query}</pre></details> : null}
    </article>
  );
}
