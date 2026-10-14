// Row R12: the answer card's two forms.
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

const render = (r: Report) => renderToStaticMarkup(<AnswerCard report={r} status="completed" first={0.3} onOpen={() => {}} />);

describe("the answer card", () => {
  it("at 0.70 leads with cause, how sure, do next, owner, don't and evidence", () => {
    const html = render(report(0.7));
    expect(html).toContain('class="ans"');
    for (const row of ["Cause", "How sure", "Do next", "Owner", "Don’t", "Evidence"]) expect(html).toContain(`<dt>${row}</dt>`);
    expect(html).toContain("0.70");
  });

  it("at 0.69 gives the lead and a narrative instead", () => {
    const html = render(report(0.69));
    expect(html).not.toContain('class="ans"');
    expect(html).toContain("Leading, not yet sure");
    expect(html).toContain('class="rep"');                       // the findings strip either way
  });

  it("puts the tick where the leading claim started", () => {
    expect(render(report(0.9))).toContain('class="sm-conf-prev" style="left:30%"');
  });
});
