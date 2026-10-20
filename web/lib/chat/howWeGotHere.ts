// "How Smalls got here" (docs/v4-plan.md 5.1): the checks of a finished investigation as one sentence, with a
// note when the person skipped or rejected some. Each check that ran gives a clause: the outcome the API's
// summariser wrote for it, else one from code ("ruled out …" when the board rejected a claim after it, "found
// nothing" for an empty result, otherwise what it did, in the past). Skipped and rejected checks give no clause.

import { plainLabel } from "./labels";
import type { Group, Row } from "./reduce";

export interface CheckLine {
  call_id: string;
  status: "ran" | "failed" | "skipped" | "rejected";
  past: string;                 // the plain label in the past ("searched every log for “x”")
  outcome?: string | null;      // the summariser's clause, when there is one
  ruledOut?: string | null;     // what the board rejected after this check, if anything
  empty?: boolean;              // the check came back with nothing
}

export function clause(c: CheckLine): string {
  if (c.outcome && c.outcome.trim()) return c.outcome.trim().replace(/[.\s]+$/, "");
  if (c.ruledOut) return `ruled out ${c.ruledOut}`;
  if (c.empty) return `${c.past} and found nothing`;
  return c.past;
}

function joined(parts: string[]): string {
  return parts.length === 1 ? parts[0]! : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** "Smalls a, b and c." over the checks that ran, in their order. */
export function sentence(checks: CheckLine[]): string {
  const parts = checks.filter((c) => c.status === "ran").map(clause);
  return parts.length ? `Smalls ${joined(parts)}.` : "Smalls didn’t run any checks.";
}

/** "You skipped 1 check." / "You rejected 2 checks and skipped 1." with what follows, or null. */
export function note(checks: CheckLine[]): string | null {
  const rejected = checks.filter((c) => c.status === "rejected").length;
  const skipped = checks.filter((c) => c.status === "skipped").length;
  if (!rejected && !skipped) return null;
  const noun = (n: number) => (n === 1 ? "check" : "checks");
  const said = rejected && skipped ? `You rejected ${rejected} ${noun(rejected)} and skipped ${skipped}.`
    : rejected ? `You rejected ${rejected} ${noun(rejected)}.` : `You skipped ${skipped} ${noun(skipped)}.`;
  return `${said} The summary will list what wasn’t checked.`;
}

/** "5 checks · 4 run, 1 rejected" */
export function line(checks: CheckLine[]): string {
  const n = (s: CheckLine["status"]) => checks.filter((c) => c.status === s).length;
  const parts = [`${n("ran")} run`, n("failed") ? `${n("failed")} failed` : "", n("rejected") ? `${n("rejected")} rejected` : "", n("skipped") ? `${n("skipped")} skipped` : ""];
  return `${checks.length === 1 ? "1 check" : `${checks.length} checks`} · ${parts.filter(Boolean).join(", ")}`;
}

// -- from the folded state -------------------------------------------------------------------------------------

const lowerFirst = (t: string) => t.charAt(0).toLowerCase() + t.slice(1);

/** What the board ruled out, in words: its subject when it names one, else "that <statement>". */
function ruled(subject: string, statement: string): string {
  if (subject && subject.toLowerCase() !== "unknown") return subject;
  return `that ${lowerFirst(statement.trim().replace(/[.\s]+$/, ""))}`;
}

/** The agent's checks in these rounds, in order, each with how it went: what feeds the sentence, the note and the
 * line. A check refused for its arguments before it ran counts as failed; one still waiting or running is left out. */
export function linesFor(groups: Group[], rows: Record<string, Row>, outcomes: Record<string, string>, subjects: Record<string, string> = {}): CheckLine[] {
  const out: CheckLine[] = [];
  for (const g of groups) {
    const mine: CheckLine[] = [];
    for (const id of g.calls) {
      const r = rows[id];
      if (!r || r.kind !== "read" || r.by !== "agent") continue;
      const status: CheckLine["status"] | null = r.state === "rejected" ? "rejected" : r.state === "skipped" ? "skipped" : r.state === "refused" ? "failed"
        : r.result ? (r.result.status === "ok" ? "ran" : "failed") : null;
      if (!status) continue;
      mine.push({ call_id: id, status, past: plainLabel(r).past, outcome: outcomes[id] ?? null, empty: status === "ran" && r.result!.shown.length === 0 });
    }
    const dropped = g.board.filter((c) => c.to.status === "rejected" && c.from?.status !== "rejected");
    const last = [...mine].reverse().find((c) => c.status === "ran");
    if (last && dropped.length) last.ruledOut = dropped.map((c) => ruled(subjects[c.id] ?? "", c.statement)).join(" and ");
    out.push(...mine);
  }
  return out;
}
