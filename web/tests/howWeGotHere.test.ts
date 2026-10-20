import { describe, expect, it } from "vitest";
import { line, note, sentence, type CheckLine } from "@/lib/chat/howWeGotHere";

const c = (call_id: string, status: CheckLine["status"], past: string, more: Partial<CheckLine> = {}): CheckLine => ({ call_id, status, past, ...more });

describe("How Smalls got here", () => {
  it("says so when no check ran", () => {
    expect(sentence([])).toBe("Smalls didn’t run any checks.");
    expect(note([])).toBeNull();
    expect(sentence([c("1", "skipped", "searched a")])).toBe("Smalls didn’t run any checks.");
  });

  it("one check: its outcome, else code's clause", () => {
    expect(sentence([c("1", "ran", "searched a", { outcome: "found the job's exit code." })])).toBe("Smalls found the job's exit code.");
    expect(sentence([c("1", "ran", "searched a")])).toBe("Smalls searched a.");
    expect(sentence([c("1", "ran", "searched a", { empty: true })])).toBe("Smalls searched a and found nothing.");
    expect(sentence([c("1", "ran", "searched a", { ruledOut: "the disk" })])).toBe("Smalls ruled out the disk.");
  });

  it("three checks keep their order, and skipped or rejected ones give no clause", () => {
    const checks = [c("1", "ran", "listed b"), c("2", "rejected", "searched x"), c("3", "ran", "opened c"), c("4", "skipped", "looked in d"), c("5", "ran", "charted e")];
    expect(sentence(checks)).toBe("Smalls listed b, opened c and charted e.");
    expect(note(checks)).toBe("You rejected 1 check and skipped 1. The summary will list what wasn’t checked.");
    expect(line(checks)).toBe("5 checks · 3 run, 1 rejected, 1 skipped");
  });

  it("singular and plural notes", () => {
    expect(note([c("1", "skipped", "a")])).toBe("You skipped 1 check. The summary will list what wasn’t checked.");
    expect(note([c("1", "skipped", "a"), c("2", "skipped", "b")])).toBe("You skipped 2 checks. The summary will list what wasn’t checked.");
    expect(note([c("1", "rejected", "a"), c("2", "rejected", "b"), c("3", "skipped", "c")])).toBe("You rejected 2 checks and skipped 1. The summary will list what wasn’t checked.");
    expect(line([c("1", "ran", "a")])).toBe("1 check · 1 run");
  });
});
