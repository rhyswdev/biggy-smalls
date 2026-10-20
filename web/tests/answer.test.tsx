// Row R12, in v4's order (ChatV4): the answer card's two forms.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnswerCard } from "@/components/chat/AnswerCard";
import { fold } from "@/lib/chat/reduce";
import type { Report } from "@/lib/types";
import { fixture } from "./load";

function report(confidence: number): Report {
  const base = fold(fixture("chat-approved")).report!;
  return { ...base, hypotheses: base.hypotheses.map((h) => (h.status === "leading" ? { ...h, confidence } : h)) };
}

const render = (r: Report) => renderToStaticMarkup(<AnswerCard report={r} status="completed" first={0.3} checks={4} onOpen={() => {}} />);
const rows = (html: string) => [...html.matchAll(/<dt>([^<]+)<\/dt>/g)].map((m) => m[1]);

describe("the answer card", () => {
  it("at 0.70 names the probable cause, then why, the action, the group, the confidence and what to avoid, in that order", () => {
    const html = render(report(0.7));
    expect(html).toContain("Probable cause: ");
    expect(rows(html)).toEqual(["Why it fails", "Recommended action", "Assignment group", "Confidence", "Avoid"]);
    expect(html).toContain("High (70%). From 4 checks.");
    expect(html).toContain("Business impact, technical detail and evidence");            // the rest is one click away
  });

  it("at 0.69 says the leading claim is ahead but not settled", () => {
    const html = render(report(0.69));
    expect(html).not.toContain("Probable cause: ");
    expect(html).toContain("Ahead, but not settled: ");
    expect(html).toContain("Medium (69%)");
  });

  it("uses none of the old words", () => {
    const html = render(report(0.9));
    for (const old of ["Owner", "Do next", "Don’t", "Leading", "How sure"]) expect(html).not.toContain(old);
  });
});
