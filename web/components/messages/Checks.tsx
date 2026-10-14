import type { CheckResult } from "@/lib/writeup/messages";

const GLYPH = { ok: "✓", bad: "!", open: "?" } as const;
const SPOKEN = { ok: "passes", bad: "fails", open: "check by hand" } as const;

/** A draft's checks, failing first (section 4, Messages). Announced as they change, since they follow each keystroke. */
export function Checks({ results }: { results: CheckResult[] }) {
  const ok = results.filter((c) => c.state === "ok").length;
  const bad = results.some((c) => c.state === "bad");
  return (
    <div className="checks" aria-live="polite">
      <div className="checks-head">
        <span className="label">Checks</span><span className="grow" />
        <span className={`time ${bad ? "ck-bad" : "muted"}`}>{ok} of {results.length} pass</span>
      </div>
      {results.map((c) => (
        <div key={c.id} className={`chk ${c.state}`}>
          <span className="chk-g" aria-hidden="true">{GLYPH[c.state]}</span>
          <span className="chk-t">{c.label}<span className="sm-visually-hidden">: {SPOKEN[c.state]}</span></span>
        </div>
      ))}
    </div>
  );
}

/** The one-line result in the audience list: "All N checks pass", "! k of N checks", or how many are left to check. */
export function score(results: CheckResult[]): { text: string; bad: boolean } {
  const ok = results.filter((c) => c.state === "ok").length;
  const open = results.filter((c) => c.state === "open").length;
  if (results.some((c) => c.state === "bad")) return { text: `! ${ok} of ${results.length} checks`, bad: true };
  if (open) return { text: `${ok} of ${results.length} pass · ${open} to check`, bad: false };
  return { text: `All ${results.length} checks pass`, bad: false };
}
