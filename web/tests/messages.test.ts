// Row R14: the messages' checks and the code-rendered drafts. Every check takes its value from the report; a draft
// that breaks a rule lists the failing check first; code's drafts from a full report break none.
import { describe, expect, it } from "vitest";
import { fold } from "@/lib/chat/reduce";
import type { Report } from "@/lib/types";
import { audiences, check, codeDraft, facts, hhmm, plain, specs, wordCount } from "@/lib/writeup/messages";
import type { Resolution } from "@/lib/writeup/render";
import { fixture } from "./load";

const report: Report = fold(fixture("chat-approved")).report!;
const resolved: Resolution = { path: "agent", note: "Rolled the release back.", resolved_at: "02:40", resolved_by: "The commander" };
const byKey = (r: Report | null) => Object.fromEntries(audiences(r).map((a) => [a.key, a]));

describe("the checks", () => {
  it("take their values from the report", () => {
    const f = facts(report);
    expect(f.owners).toContain(report.actions.find((a) => a.rank === 1)!.owner);
    expect(f.internal).toEqual(expect.arrayContaining(report.impact!.cis));
    expect(f.internal).not.toContain(report.impact!.business_service);      // the service is what customers know it by
    expect(f.next).toBe(hhmm(report.stakeholder_update!.next_update_at));
    const exec = byKey(report).exec!;
    expect(exec.rules.find((r) => r.id === "owner")!.label).toContain(f.owners[0]);
  });

  it("list a failing check first, then those to check by hand, then those that pass", () => {
    const status = byKey(report).status!;
    const ok = codeDraft(status, report, resolved);
    expect(check(status, ok).every((c) => c.state === "ok")).toBe(true);
    const host = report.impact!.cis[0]!;
    const broken = `${ok} See ${report.hypotheses[0]!.support[0] ?? "log:a/b"} on ${host}.`;
    const results = check(status, broken);
    expect(results.slice(0, 2).map((c) => c.id).sort()).toEqual(["handles", "internal"]);
    expect(results.slice(0, 2).every((c) => c.state === "bad")).toBe(true);
    expect(results.slice(2).every((c) => c.state === "ok")).toBe(true);
  });

  it("update on every change of the text", () => {
    const exec = byKey(report).exec!;
    const words = Array.from({ length: 61 }, () => "word").join(" ");
    expect(check(exec, words).find((c) => c.id === "words")!.state).toBe("bad");
    expect(check(exec, words.split(" ").slice(1).join(" ")).find((c) => c.id === "words")!.state).toBe("ok");
    expect(check(exec, "**Bold** at 02:40 UTC").find((c) => c.id === "plain")!.state).toBe("bad");
    expect(check(exec, "Next update 03:10 UTC.").find((c) => c.id === "next")!.state).toBe("ok");
    expect(check(exec, "We will update again soon.").find((c) => c.id === "next")!.state).toBe("bad");
  });

  it("are left to the commander when the report has no value for them", () => {
    const bare: Report = { ...report, actions: [], do_not: [], hypotheses: [],
      impact: { ...report.impact!, cis: [], oncall: "unknown", assignment_group: "unknown" } };
    const a = byKey(bare);
    expect(check(a.exec!, "anything").find((c) => c.id === "owner")!.state).toBe("open");
    expect(check(a.status!, "anything").find((c) => c.id === "internal")!.state).toBe("open");
    expect(a.eng!.rules.some((r) => r.id === "avoid")).toBe(false);          // nothing in the report to avoid
    const order = check(a.exec!, "anything").map((c) => c.state);
    expect(order.indexOf("open")).toBeGreaterThan(order.lastIndexOf("bad"));
  });
});

describe("code's drafts", () => {
  it("pass every check of their audience from a full report, resolved or not", () => {
    for (const res of [resolved, null]) {
      for (const a of audiences(report)) {
        const text = codeDraft(a, report, res);
        expect(text.length).toBeGreaterThan(0);
        expect(check(a, text).filter((c) => c.state !== "ok"), `${a.key}: ${text}`).toEqual([]);
        expect(wordCount(text)).toBeLessThanOrEqual(a.words);
      }
    }
  });

  it("say when it was resolved, and the commander's own fix on that path", () => {
    const exec = byKey(report).exec!;
    expect(codeDraft(exec, report, resolved)).toContain("Resolved at 02:40 UTC.");
    const own = codeDraft(byKey(report).eng!, report, { ...resolved, path: "own", note: "Restarted the job by hand.\nMore." });
    expect(own).toContain("Resolved another way: Restarted the job by hand.");
  });

  it("take no handle from the report's text", () => {
    expect(plain("Purge it per conf:RB-X (log:a/1, log:a/2) now")).toBe("Purge it per now.");
    for (const a of audiences(report)) expect(codeDraft(a, report, resolved)).not.toMatch(/\b[a-z]+:[A-Za-z0-9]/);
  });

  it("go to the API with their checks as sentences", () => {
    const sent = specs(report, resolved, ["exec", "eng"]);
    expect(sent.map((s) => s.key)).toEqual(["exec", "eng"]);
    expect(sent[0]!.checks).toEqual(byKey(report).exec!.rules.map((r) => r.label));
    expect(sent[1]!.fallback).toBe(codeDraft(byKey(report).eng!, report, resolved));
  });
});
