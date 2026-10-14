import { cx, fixed2 } from "./util";

export interface ConfidenceProps {
  value: number;
  previous?: number | null;
  since?: string;
  muted?: boolean;
  className?: string;
}

/** A value from 0 to 1, the earlier value as a tick on the bar, and the change since it. */
export function Confidence({ value, previous, since, muted, className }: ConfidenceProps) {
  const v = Math.max(0, Math.min(1, Number(value) || 0));
  const hasPrev = typeof previous === "number";
  const d = hasPrev ? v - (previous as number) : 0;
  const delta = hasPrev ? `${d === 0 ? "±0.00" : `${d > 0 ? "+" : "−"}${fixed2(Math.abs(d))}`}${since ? ` since ${since}` : ""}` : null;
  return (
    <span className={cx("sm-conf", muted && "sm-conf-muted", className)} title={`Confidence ${fixed2(v)}${hasPrev ? ` (was ${fixed2(previous as number)})` : ""}`}>
      <span className="sm-conf-num">{fixed2(v)}</span>
      <span className="sm-conf-bar" aria-hidden="true">
        <span className="sm-conf-fill" style={{ width: `${v * 100}%` }} />
        {hasPrev ? <span className="sm-conf-prev" style={{ left: `${(previous as number) * 100}%` }} /> : null}
      </span>
      {delta ? <span className="sm-conf-delta">{delta}</span> : <span />}
    </span>
  );
}
