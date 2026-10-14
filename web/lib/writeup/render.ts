// The write-up (section 4, Summary; 7.5): rendered by code from the report, the commander's resolution and the
// evidence they kept, so it is a record of that evidence, exact. Pure.

import { leading } from "@/lib/chat/reduce";
import type { Report } from "@/lib/types";

export interface Resolution { path: "agent" | "own"; note: string; resolved_at: string; resolved_by: string }
export interface WriteupData {
  resolution: Resolution | null;
  report: Report | null;
  rows: Record<string, string>;
  incident: string | null;
  cited?: string[];             // every handle the report cites, as the API's validator counts them
  writeup: { version: number; kept: string[]; saved_at: string | null } | null;
}
export interface EvidenceRow { handle: string; text: string; source: string; kept: boolean }
export interface Signal { n: number; text: string; where: string; handles: string[]; dropped: boolean }
export interface ChainStep { step: "Cause" | "Trigger" | "Contributing" | "Impact"; text: string; handles: string[] }
export interface Writeup {
  title: string;
  pathLine: string;
  note: string;
  narrative: string;
  chainTitle: string;
  chain: ChainStep[];
  signals: Signal[];
  ruledOut: { what: string; why: string; handles: string[] }[];
  evidence: EvidenceRow[];
  kept: number;
  terms: string[];
  body: string;
}

/** Every handle a report cites, in the order it first cites them: the summary, the hypotheses, the timeline, the
 * actions, the do-not list and the alert verdicts. */
export function citations(report: Report | null): string[] {
  if (!report) return [];
  const out: string[] = [];
  const inText = report.summary.match(/\b[a-z][a-z0-9_]*:(?!\/\/)[A-Za-z0-9][^\s,;()[\]{}"']*[A-Za-z0-9#\]}]/g) ?? [];
  out.push(...inText);
  for (const h of report.hypotheses) out.push(...h.support, ...h.against);
  for (const t of report.timeline) out.push(...t.evidence);
  for (const a of report.actions) out.push(...a.evidence);
  for (const d of report.do_not) out.push(...d.evidence);
  out.push(...report.alerts.map((a) => a.handle));
  return [...new Set(out)];
}

const source = (h: string) => h.slice(0, Math.max(0, h.indexOf(":"))) || "row";

export function render(data: WriteupData, kept: ReadonlySet<string>, note: string): Writeup {
  const report = data.report;
  const res = data.resolution;
  const own = res?.path === "own";
  const lead = leading(report);
  const live = (role: string) => (report?.hypotheses ?? []).filter((h) => h.role === role && h.status !== "rejected");
  const cause = lead && (lead.role === "cause" || !live("cause").length) ? lead : live("cause")[0] ?? lead;
  const chain: ChainStep[] = [];
  if (cause) chain.push({ step: "Cause", text: cause.statement, handles: cause.support });
  for (const h of live("trigger").filter((x) => x !== cause)) chain.push({ step: "Trigger", text: h.statement, handles: h.support });
  for (const h of live("contributing").filter((x) => x !== cause)) chain.push({ step: "Contributing", text: h.statement, handles: h.support });
  if (report?.impact) chain.push({ step: "Impact", text: [report.impact.customer, report.impact.business_service].filter(Boolean).join(" · "), handles: [] });
  const rawSignals = [
    ...(report?.alerts ?? []).filter((a) => a.verdict === "this_incident").map((a) => ({ text: data.rows[a.handle] ?? a.handle, where: "an alert of this incident", handles: [a.handle] })),
    ...(report?.timeline ?? []).map((t) => ({ text: t.event, where: t.t_utc, handles: t.evidence })),
  ];
  const signals = rawSignals.map((s, i) => ({ ...s, n: i + 1, dropped: s.handles.length > 0 && !s.handles.some((h) => kept.has(h)) }));
  const ruledOut = (report?.hypotheses ?? []).filter((h) => h.status === "rejected").map((h) => ({
    what: h.statement, why: h.against.length ? h.against.map((x) => data.rows[x] ?? x).join(" · ") : "No row against it was recorded.", handles: h.against,
  }));
  // The API's list of what the report cites is the one the kept set is drawn from; the report's own order puts it in
  // reading order, and anything only the API counts (a handle inline in prose) comes after.
  const order = citations(report);
  const all = data.cited ?? order;
  const listed = [...order.filter((h) => all.includes(h)), ...all.filter((h) => !order.includes(h))];
  const evidence = listed.map((h) => ({ handle: h, text: data.rows[h] ?? h, source: source(h), kept: kept.has(h) }));
  const title = own ? (note.trim().split("\n")[0] ?? "").slice(0, 120) || "Resolved another way" : cause?.statement ?? report?.summary.split(/(?<=[.!?])\s/)[0] ?? "Write-up";
  const pathLine = res ? `${own ? "Resolved another way" : "Smalls’ conclusion was right"} · resolved ${res.resolved_at} UTC by ${res.resolved_by}` : "";
  const chainTitle = own ? "Smalls’ last picture, not what fixed it" : "Cause → Trigger → Contributing → Impact";
  const terms = [...new Set([lead?.subject, ...(lead?.entities ?? []), report?.impact?.business_service,
    ...signals.filter((s) => s.where === "an alert of this incident" && !s.dropped).map((s) => s.text.slice(0, 80)), data.incident]
    .filter((x): x is string => Boolean(x && x !== "unknown")))];
  const keptRows = evidence.filter((e) => e.kept);
  const body = [
    title, "", `How it was resolved (${res?.resolved_by ?? "the commander"}, ${res?.resolved_at ?? ""} UTC): ${note.trim()}`, "",
    `What happened: ${report?.summary ?? ""}`, ...chain.map((c) => `${c.step}: ${c.text}`), "",
    "Signals that mark this issue:", ...signals.filter((s) => !s.dropped).map((s) => `- ${s.text}`), "",
    "Ruled out:", ...ruledOut.map((r) => `- ${r.what}`), "",
    "Evidence kept:", ...keptRows.map((e) => `- ${e.handle} ${e.text}`),
  ].join("\n");
  return { title, pathLine, note, narrative: report?.summary ?? "", chainTitle, chain, signals, ruledOut, evidence, kept: keptRows.length, terms, body };
}
