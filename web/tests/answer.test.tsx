// Row R12: the answer card's two forms, and the suggested follow-ups' answers, which cite only rows the run showed.
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnswerCard } from "@/components/chat/AnswerCard";
import { FROM_REPORT, suggestions } from "@/lib/chat/answers";
import { fold, leading } from "@/lib/chat/reduce";
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

describe("the suggested follow-ups", () => {
  for (const name of ["chat-approved", "chat-asking", "headless"]) {
    it(`${name}: each answer says it is from the report and cites only rows the run showed`, () => {
      const events = fixture(name);
      const s = fold(events);
      const shown = new Set(events.filter((e) => e.type === "tool_result").flatMap((e) => (e.data.shown as string[]) ?? []));
      const offered = suggestions(s.report, s.verifier?.checks ?? [], s.verifier?.disputed ?? []);
      expect(offered.length).toBeGreaterThan(0);
      for (const o of offered.filter((x) => x.kind === "code")) {
        expect(o.answer!.source).toBe(FROM_REPORT);
        for (const h of o.answer!.handles) expect(shown.has(h), `${o.q}: ${h}`).toBe(true);
      }
    });
  }

  it("offers a why-not for each ruled-out claim, and the owner from the first action", () => {
    const s = fold(fixture("chat-approved"));
    const offered = suggestions(s.report);
    const rejected = s.report!.hypotheses.filter((h) => h.status === "rejected");
    expect(offered.filter((o) => o.id.startsWith("why-not-"))).toHaveLength(Math.min(2, rejected.length));
    const owner = offered.find((o) => o.id === "owner")!;
    expect(owner.answer!.text[0]).toContain([...s.report!.actions].sort((a, b) => a.rank - b.rank)[0]!.owner);
    expect(leading(s.report)).not.toBeNull();
  });
});
