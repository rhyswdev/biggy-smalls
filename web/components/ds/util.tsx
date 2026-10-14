/** Small helpers the design system's components share, as bundle.js has them. */

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

/** "2026-10-13T14:31:07.221Z" -> "14:31:07"; seconds=false -> "14:31". Anything that is not ISO is returned as given. */
export function utc(iso: string | null | undefined, seconds = true): string {
  const m = /T(\d\d):(\d\d)(?::(\d\d))?/.exec(iso ?? "");
  if (!m) return iso ?? "";
  return !seconds || !m[3] ? `${m[1]}:${m[2]}` : `${m[1]}:${m[2]}:${m[3]}`;
}

/** "2026-10-13T…" -> "13 Oct". */
export function utcDay(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d\d)-(\d\d)/.exec(iso ?? "");
  if (!m) return "";
  const mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m[2]) - 1];
  return `${Number(m[3])} ${mon}`;
}

export function fixed2(n: number): string {
  return (Math.round(n * 100) / 100).toFixed(2);
}

export function Label({ children }: { children: React.ReactNode }) {
  return <span className="sm-label">{children}</span>;
}
