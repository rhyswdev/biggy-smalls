// The suggested follow-ups after an answer (ruling U9). Most ask what the report already holds, so code answers
// them from the report, the verifier's verdict and the board, and says so; a model call would be agent theatre.
// A question the report cannot answer is offered as a follow-up that continues the same run. Pure.

import type { Report } from "@/lib/types";

export const FROM_REPORT = "From the report. No new reads.";

export interface CodeAnswer { text: string[]; handles: string[]; source: string }
export interface Suggestion {
  id: string;
  q: string;
  kind: "code" | "run" | "draft";
  answer?: CodeAnswer;
  note?: string;          // for "run": the follow-up sent to the agent
}

type Check = { statement: string; tool: string; args: Record<string, unknown> };

const short = (s: string, n = 60) => (s.length <= n ? s : `${s.slice(0, n - 1).trimEnd()}…`);

export function suggestions(report: Report | null, checks: Check[] = [], disputed: string[] = []): Suggestion[] {
  if (!report) return [];
  const out: Suggestion[] = [];
  for (const h of report.hypotheses.filter((x) => x.status === "rejected").slice(0, 2)) {
    out.push({
      id: `why-not-${h.id}`, q: `Why not ${short(h.subject || h.statement, 40)}?`, kind: "code",
      answer: {
        text: [`Ruled out: ${h.statement} (confidence ${h.confidence.toFixed(2)}).`,
          h.against.length ? "The rows that count against it:" : "The report gives no row against it; the verifier would flag that."],
        handles: h.against, source: FROM_REPORT,
      },
    });
  }
  const opens = report.open_questions;
  const why = [
    ...(opens.length ? [`Still open: ${opens.join(" ")}`] : []),
    ...(disputed.length ? [`The verifier left ${disputed.length} claim${disputed.length === 1 ? "" : "s"} short of supported: ${disputed.join(", ")}.`] : []),
    ...(checks.length ? [`A check that would tell the leading answer from its rival: ${checks.map((c) => c.statement).join(" ")}`] : []),
  ];
  out.push({
    id: "change-mind", q: "What would change your mind?", kind: "code",
    answer: { text: why.length ? why : ["The report lists no open question, and the verifier raised none."], handles: [], source: FROM_REPORT },
  });
  for (const c of checks.slice(0, 2)) {
    out.push({ id: `check-${c.tool}-${short(c.statement, 20)}`, q: `Run the check: ${short(c.statement, 50)}`, kind: "run",
      note: `Run the check the verifier proposed: ${c.statement} (${c.tool} ${JSON.stringify(c.args)}). Say whether it changes the answer.` });
  }
  const first = [...report.actions].sort((a, b) => a.rank - b.rank)[0];
  const owner = first?.owner || report.impact?.oncall || report.impact?.assignment_group;
  if (owner) {
    out.push({
      id: "owner", q: "Who should own it?", kind: "code",
      answer: {
        text: [first ? `${owner} owns the first action: ${first.action}` : `${owner}, by the report's impact.`,
          ...(report.impact ? [`On call: ${report.impact.oncall}. Assignment group: ${report.impact.assignment_group}.`] : [])],
        handles: first?.evidence ?? [], source: FROM_REPORT,
      },
    });
  }
  return out;
}
