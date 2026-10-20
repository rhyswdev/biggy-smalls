// Times are shown in UTC, in mono (section 4). These helpers only reformat what the API sent.

export function clock(iso: string | null | undefined, seconds = false): string {
  const m = /T(\d\d):(\d\d)(?::(\d\d))?/.exec(iso ?? "");
  if (!m) return iso ?? "";
  return seconds && m[3] ? `${m[1]}:${m[2]}:${m[3]}` : `${m[1]}:${m[2]}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function day(iso: string | null | undefined): string {
  const m = /^(\d{4})-(\d\d)-(\d\d)/.exec(iso ?? "");
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1]}` : "";
}

/** "13 Oct 14:52" */
export function stamp(iso: string | null | undefined): string {
  return `${day(iso)} ${clock(iso)}`.trim();
}

export function seconds(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "–";
  const s = Math.round(n);
  return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${String(s % 60).padStart(2, "0")} s`;
}

export function elapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function money(usd: number | null | undefined): string {
  return usd == null ? "–" : `$${usd.toFixed(2)}`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** What a waiting gate holds, as the composer's hint names it: reads, actions or both, with the verb to match. */
export function waitingText(cards: { kind: string }[]): string {
  const actions = cards.filter((c) => c.kind === "action").length;
  const reads = cards.length - actions;
  const what = [reads ? plural(reads, "read") : "", actions ? plural(actions, "action") : ""].filter(Boolean).join(" and ");
  return `${what} ${cards.length === 1 ? "is" : "are"}`;
}

/** "P1 · Critical" from "1 - Critical". */
export function priority(value: string | null | undefined): { code: string; label: string; text: string } | null {
  const m = /^(\d)\s*-\s*(.+)$/.exec(value ?? "");
  if (!m) return value ? { code: "", label: value, text: value } : null;
  return { code: `P${m[1]}`, label: m[2]!, text: `P${m[1]} · ${m[2]}` };
}

/** One confidence form everywhere a confidence is written as text: "High (78%)". High is 0.7 and up, Medium
 * 0.4 and up, Low below. */
export function confidenceLabel(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "";
  const v = Math.min(1, Math.max(0, value));
  const word = v >= 0.7 ? "High" : v >= 0.4 ? "Medium" : "Low";
  return `${word} (${Math.round(v * 100)}%)`;
}

/** A time zone's everyday name, such as "Eastern Time", and its short form for a time ("EDT"), from the zone's
 * IANA name and an instant. Falls back to the IANA name. */
export function zoneNames(zone: string, iso: string): { long: string; short: string } {
  const at = new Date(iso);
  const part = (style: "longGeneric" | "short") => {
    try {
      return new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: style }).formatToParts(at).find((p) => p.type === "timeZoneName")?.value;
    } catch {
      return undefined;
    }
  };
  return { long: part("longGeneric") ?? zone, short: part("short") ?? zone };
}
