// The stakeholder messages (section 4, Messages; 7.5; ruling U8): four audiences, each with a guide and checks whose
// values come from the report, and a draft rendered by code for when no model drafts it. The checks run on whatever
// text is in the box, whoever wrote it: the drafter, code or the commander. Pure.

import { leading } from "@/lib/chat/reduce";
import { clock } from "@/lib/format";
import type { Report } from "@/lib/types";
import { citations, type Resolution } from "@/lib/writeup/render";

export type AudienceKey = "exec" | "support" | "status" | "eng";
export const AUDIENCE_KEYS: AudienceKey[] = ["exec", "support", "status", "eng"];

/** A check's result. `open`: the report holds nothing to check this against, so the commander checks it. */
export type CheckState = "ok" | "bad" | "open";
export interface CheckResult { id: string; label: string; state: CheckState }
interface Rule { id: string; label: string; test: (text: string) => boolean | null }

export interface Audience {
  key: AudienceKey;
  name: string;
  channel: string;
  guide: string;
  words: number;
  rules: Rule[];
}

/** What the API's drafter is given for one audience: the checks as sentences, and code's draft to keep if no model drafts. */
export interface AudienceSpec { key: AudienceKey; name: string; channel: string; guide: string; checks: string[]; fallback: string }

// A handle as the agent writes one (`source:identifier`); a URL's `scheme://` is not one.
const HANDLE = /\b[a-z][a-z0-9_]*:(?!\/\/)[A-Za-z0-9][^\s,;()[\]{}"']*[A-Za-z0-9#\]}]/g;
const MARKDOWN = /(^|\n)\s*(#{1,6}\s|[-*+•]\s)|\*\*|__|`|\[[^\]\n]+\]\([^)\n]+\)/;
const UTC_TIME = /\b\d{1,2}:\d{2}\s?UTC\b/;

const known = (s: string | null | undefined): string => {
  const t = (s ?? "").trim();
  return /^(unknown|n\/a|none|tbd|-)?$/i.test(t) ? "" : t;
};
const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const mentions = (text: string, name: string) => new RegExp(`(^|[^\\w-])${escape(name)}($|[^\\w-])`, "i").test(text);

export function wordCount(text: string): number {
  const t = text.trim();
  return t ? t.split(/\s+/).length : 0;
}

/** A time as HH:MM: from an ISO time, or the first HH:MM in free text. */
export function hhmm(value: string | null | undefined): string | null {
  const v = known(value);
  if (!v) return null;
  const iso = clock(v);
  if (/^\d\d:\d\d$/.test(iso)) return iso;
  return /\b(\d{1,2}:\d{2})\b/.exec(v)?.[1] ?? null;
}

/** Text from the report made fit for a message: no handles, no empty brackets, single spaces, a full stop. */
export function plain(value: string | null | undefined): string {
  const t = known(value).replace(HANDLE, "").replace(/\(\s*[,;]?\s*\)/g, "").replace(/\s+([,.;:])/g, "$1").replace(/\s{2,}/g, " ").trim();
  return t && !/[.!?]$/.test(t) ? `${t}.` : t;
}

/** The values the checks are filled with, all from the report. */
export function facts(report: Report | null) {
  const first = [...(report?.actions ?? [])].sort((a, b) => a.rank - b.rank)[0];
  const owners = [...new Set([first?.owner, report?.impact?.oncall, report?.impact?.assignment_group].map(known).filter(Boolean))];
  const service = known(report?.impact?.business_service);
  const lower = new Set([service, ...owners].map((s) => s.toLowerCase()));
  const internal = [...new Set([
    ...(report?.hypotheses ?? []).flatMap((h) => [h.subject, ...h.entities]), ...(report?.impact?.cis ?? []),
  ].map(known).filter((s) => s.length >= 3 && !lower.has(s.toLowerCase())))];
  return {
    owners, service, internal,
    handles: citations(report),
    next: hhmm(report?.stakeholder_update?.next_update_at),
    started: hhmm(report?.timeline[0]?.t_utc),
    doNot: report?.do_not[0] ?? null,
  };
}

/** Whether a sentence is about what customers notice: it says customers or users, or names the business service. */
function aboutCustomers(sentence: string, service: string): boolean {
  return /\b(customers?|users?|clients?|shoppers?|members?)\b/i.test(sentence) || Boolean(service && mentions(sentence, service));
}

function rules(report: Report | null) {
  const f = facts(report);
  const short = (s: string, n = 48) => (s.length <= n ? s : `${s.slice(0, n - 1).trimEnd()}…`);
  return {
    utc: { id: "utc", label: "Gives a time in UTC (HH:MM UTC)", test: (t: string) => UTC_TIME.test(t) },
    next: { id: "next", label: "Says when the next update is (“next update HH:MM UTC”)", test: (t: string) => /next update\b[^.!?\n]{0,40}?\b\d{1,2}:\d{2}/i.test(t) },
    owner: f.owners.length
      ? { id: "owner", label: `Names who is on it: ${f.owners.join(" or ")}`, test: (t: string) => f.owners.some((o) => mentions(t, o)) }
      : { id: "owner", label: "Names who is on it (the report names no owner)", test: () => null },
    plain: { id: "plain", label: "Plain text: no markdown", test: (t: string) => !MARKDOWN.test(t) },
    handles: { id: "handles", label: "No evidence handles", test: (t: string) => !(t.match(HANDLE)?.length) && !f.handles.some((h) => t.includes(h)) },
    internal: f.internal.length
      ? { id: "internal", label: `Names no host or internal system (${short(f.internal.join(", "), 80)})`, test: (t: string) => !f.internal.some((n) => mentions(t, n)) }
      : { id: "internal", label: "Names no host or internal system (the report names none to check)", test: () => null },
    impact: { id: "impact", label: `Leads with what customers notice: its first sentence names customers or users${f.service ? ` or ${f.service}` : ""}`, test: (t: string) => aboutCustomers(t.trim().split(/(?<=[.!?])\s/)[0] ?? "", f.service) },
    route: { id: "route", label: "Tells agents where to route questions", test: (t: string) => /\b(route|escalate|contact|refer|send)\b/i.test(t) },
    avoid: f.doNot ? { id: "avoid", label: `Says what not to do: ${short(f.doNot.action)}`, test: (t: string) => /\b(do not|don['’]t|avoid|never)\b/i.test(t) } : null,
    words: (n: number) => ({ id: "words", label: `Under ${n} words`, test: (t: string) => wordCount(t) <= n }),
  };
}

/** The four audiences, their channels and guides, and the checks each draft must pass, filled from the report. */
export function audiences(report: Report | null): Audience[] {
  const r = rules(report);
  const avoid = r.avoid ? [r.avoid] : [];
  return [
    { key: "exec", name: "Executives", channel: "Leadership channel", guide: "Impact, the cause in one clause, the owner, the next update.", words: 60,
      rules: [r.impact, r.owner, r.utc, r.next, r.words(60), r.plain, r.handles] },
    { key: "support", name: "Support team", channel: "Support macro", guide: "What customers see, the window, where to route them.", words: 80,
      rules: [r.impact, r.utc, r.route, r.internal, r.words(80), r.plain, r.handles] },
    { key: "status", name: "Status page", channel: "Public status page", guide: "No cause, no vendor, no system names; a time and the next update.", words: 60,
      rules: [r.impact, r.utc, r.next, r.internal, r.words(60), r.plain, r.handles] },
    { key: "eng", name: "Engineering", channel: "Incident channel", guide: "Mechanism, versions, owner, what not to touch.", words: 150,
      rules: [r.utc, r.owner, ...avoid, r.words(150), r.handles] },
  ];
}

const ORDER: Record<CheckState, number> = { bad: 0, open: 1, ok: 2 };

/** Every check of an audience on this text, failing first, then those to check by hand, then those that pass. */
export function check(audience: Audience, text: string): CheckResult[] {
  const out = audience.rules.map((r) => {
    const v = r.test(text);
    return { id: r.id, label: r.label, state: (v === null ? "open" : v ? "ok" : "bad") as CheckState };
  });
  return out.map((c, i) => ({ c, i })).sort((a, b) => ORDER[a.c.state] - ORDER[b.c.state] || a.i - b.i).map(({ c }) => c);
}

/** Drop optional parts, last first, until the message fits its word limit. */
function fit(parts: { text: string; optional?: boolean }[], limit: number): string {
  const kept = parts.filter((p) => p.text);
  const join = () => kept.map((p) => p.text).join(" ");
  for (let i = kept.length - 1; wordCount(join()) > limit && i >= 0; i--) if (kept[i]!.optional) kept.splice(i, 1);
  return join();
}

/** The draft code renders from the report's suggested stakeholder update and the resolution (U8: without a key). */
export function codeDraft(audience: Audience, report: Report | null, resolution: Resolution | null): string {
  const f = facts(report);
  const su = report?.stakeholder_update;
  const lead = leading(report);
  const own = resolution?.path === "own";
  const said = plain(su?.customer_impact) || plain(report?.impact?.customer);
  const impact = said && f.service && !aboutCustomers(said, f.service) ? `${f.service}: ${said}` : said;
  const where = resolution ? `Resolved at ${resolution.resolved_at} UTC.` : plain(su?.status);
  const cause = own ? plain(resolution?.note.split("\n")[0]) : plain(lead?.statement);
  const owner = f.owners[0];
  const next = f.next ? `Next update ${f.next} UTC.` : "";
  const first = [...(report?.actions ?? [])].sort((a, b) => a.rank - b.rank)[0];
  switch (audience.key) {
    case "exec":
      return fit([{ text: impact }, { text: where }, { text: cause ? `Cause: ${cause}` : "", optional: true },
        { text: owner ? `${owner} owns the follow-up.` : "" }, { text: next }], audience.words);
    case "support": {
      const window = f.started ? (resolution ? `between ${f.started} and ${resolution.resolved_at} UTC` : `since ${f.started} UTC`) : "";
      return fit([{ text: impact && window ? `${impact.replace(/\.$/, "")}, ${window}.` : impact }, { text: resolution ? "It is resolved." : where },
        { text: owner ? `Route customer reports about it to ${owner}.` : "Route customer reports about it to the incident team." }], audience.words);
    }
    case "status":
      return fit([{ text: impact }, { text: where }, { text: next }], audience.words);      // public: no cause, no next steps
    case "eng":
      return fit([{ text: own ? `Resolved another way: ${cause}` : cause }, { text: where }, { text: owner ? `Owner: ${owner}.` : "" },
        { text: first ? `Next: ${plain(first.action)}` : "", optional: true },
        { text: f.doNot ? `Do not: ${plain(f.doNot.action).replace(/^do not\s+/i, "")} ${plain(f.doNot.why)}` : "" },
        { text: report?.open_questions.length ? `Open: ${report.open_questions.map(plain).join(" ")}` : "", optional: true }, { text: next }], audience.words);
  }
}

/** What the draft request carries for each audience. */
export function specs(report: Report | null, resolution: Resolution | null, keys: AudienceKey[] = AUDIENCE_KEYS): AudienceSpec[] {
  return audiences(report).filter((a) => keys.includes(a.key)).map((a) => ({
    key: a.key, name: a.name, channel: a.channel, guide: a.guide, checks: a.rules.map((r) => r.label.slice(0, 400)), fallback: codeDraft(a, report, resolution).slice(0, 4000),
  }));
}

/** Who wrote a draft, as the card says it (7.5: "Each draft says who wrote it"). */
export function draftedBy(by: string): string {
  return by === "code" ? "Drafted by code, from the report’s suggested update" : `Drafted by the ${by} profile`;
}
