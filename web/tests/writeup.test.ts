// The write-up's title fits what the knowledge base takes, and the composer names what a waiting gate holds.
import { describe, expect, it } from "vitest";
import { fold } from "@/lib/chat/reduce";
import { waitingText } from "@/lib/format";
import type { Report } from "@/lib/types";
import { clip, render, TITLE_MAX, type Resolution } from "@/lib/writeup/render";
import { fixture } from "./load";

const report: Report = fold(fixture("chat-approved")).report!;
const agent: Resolution = { path: "agent", note: "Rolled the release back.", resolved_at: "02:40", resolved_by: "The commander" };

describe("the write-up's title", () => {
  it("is the leading cause's statement when it fits", () => {
    const w = render({ resolution: agent, report, rows: {}, incident: null, writeup: null }, new Set(), agent.note);
    const lead = report.hypotheses.find((h) => h.status === "leading")!;
    expect(w.title).toBe(lead.statement.length <= TITLE_MAX ? lead.statement : clip(lead.statement, TITLE_MAX));
  });

  it("is cut at a word and marked when the statement is longer than the knowledge base takes", () => {
    const long = Array.from({ length: 80 }, (_, i) => `word${i}`).join(" ");
    const wordy: Report = { ...report, hypotheses: report.hypotheses.map((h) => (h.status === "leading" ? { ...h, statement: long } : h)) };
    const w = render({ resolution: agent, report: wordy, rows: {}, incident: null, writeup: null }, new Set(), agent.note);
    expect(long.length).toBeGreaterThan(TITLE_MAX);
    expect(w.title.length).toBeLessThanOrEqual(TITLE_MAX);
    expect(w.title.endsWith("…")).toBe(true);
    expect(long.startsWith(w.title.slice(0, -1))).toBe(true);
    expect(w.body.startsWith(w.title)).toBe(true);
  });

  it("leaves a short text alone and cuts a word-free one at the limit", () => {
    expect(clip("short", 10)).toBe("short");
    expect(clip("x".repeat(20), 10)).toBe(`${"x".repeat(9)}…`);
  });
});

describe("the composer's hint for a waiting gate", () => {
  it("names reads, actions or both, with the right verb", () => {
    expect(waitingText([{ kind: "read" }])).toBe("1 read is");
    expect(waitingText([{ kind: "read" }, { kind: "read" }, { kind: "read" }])).toBe("3 reads are");
    expect(waitingText([{ kind: "action" }])).toBe("1 action is");
    expect(waitingText([{ kind: "read" }, { kind: "action" }])).toBe("1 read and 1 action are");
  });
});
