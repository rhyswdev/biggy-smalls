import type { CheckResult } from "@/lib/writeup/messages";

const GLYPH = { ok: "✓", bad: "!", open: "?" } as const;
const SPOKEN = { ok: "passes", bad: "fails", open: "check by hand" } as const;

/** A draft's checks, as given (failing first); the card around them announces changes, since they follow each keystroke. */
export function Checks({ results }: { results: CheckResult[] }) {
  return (
    <>
      {results.map((c) => (
        <div key={c.id} className={`chk ${c.state}`}>
          <span className="chk-g" aria-hidden="true">{GLYPH[c.state]}</span>
          <span className="chk-t">{c.label}<span className="sm-visually-hidden">: {SPOKEN[c.state]}</span></span>
        </div>
      ))}
    </>
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
