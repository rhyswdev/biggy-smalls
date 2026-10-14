// Row R9: the reducer over event logs from scripted runs on the fixture registry (make api-fixtures). The server's
// first paint is fold(events); the browser starts from it and folds each streamed event, so any split of the log,
// and any overlap a reconnect brings, must fold to the same state.
import { describe, expect, it } from "vitest";
import { apply, fold, initial, steps, tally } from "@/lib/chat/reduce";
import { fixture, frozen } from "./load";

const NAMES = ["chat-approved", "chat-asking", "headless", "followup", "followup-answered"];

describe("the first paint equals the browser's fold", () => {
  for (const name of NAMES) {
    it(`${name}: every split, and an overlapping replay, folds to the same state`, () => {
      const events = fixture(name);
      const whole = fold(events);
      for (let k = 0; k <= events.length; k += 1) {
        const paint = fold(events.slice(0, k));
        expect(fold(events.slice(k), paint)).toEqual(whole);
        if (k % 10 === 0) expect(fold(events, paint)).toEqual(whole);      // a reconnect that sends from the start again
      }
    });
  }

  it("a fold never changes the state it is given", () => {
    const events = fixture("chat-asking");
    let state = initial();
    for (const ev of events) state = apply(frozen(state), ev);
    expect(state).toEqual(fold(events));
  });

  it("a recorded run renders the same as the live chat it was exported from", () => {
    expect(fold(fixture("past-of-chat-approved"))).toEqual(fold(fixture("chat-approved")));
  });
});

describe("the transcript", () => {
  it("opens with the commander's turn, then the sweep, then one group per round, and ends with the report", () => {
    const s = fold(fixture("chat-approved"));
    const kinds = s.turns.map((t) => t.kind);
    expect(kinds[0]).toBe("ic");
    expect(s.turns[1]).toMatchObject({ kind: "group", by: "harness" });
    expect(kinds.filter((k) => k === "report")).toHaveLength(1);
    expect(kinds[kinds.length - 1]).toBe("report");
    const rounds = s.turns.filter((t) => t.kind === "group" && t.by === "agent");
    expect(rounds.length).toBeGreaterThanOrEqual(4);
    expect(rounds.every((g) => g.kind === "group" && g.narrative)).toBe(true);        // a narrative per round
    expect(s.status).toBe("completed");
    expect(s.pending).toBeNull();
  });

  it("records each decision on its row, with the person's reason, and counts the tally", () => {
    const s = fold(fixture("chat-asking"));
    const rows = Object.values(s.rows);
    expect(rows.some((r) => r.state === "approved" && r.who === "human")).toBe(true);
    expect(rows.some((r) => r.state === "skipped" && r.who === "human")).toBe(true);
    const rejected = rows.filter((r) => r.state === "rejected");
    expect(rejected.length).toBeGreaterThan(0);
    expect(rejected.every((r) => r.reason && r.note?.startsWith("ic:note/"))).toBe(true);
    const t = tally(s);
    expect(t.rejected).toBe(rejected.length);
    expect(t.proposed).toBeGreaterThanOrEqual(t.run + t.skipped);
  });

  it("an unattended run's reads ran on the switch and its action was deferred by policy", () => {
    const s = fold(fixture("headless"));
    const reads = Object.values(s.rows).filter((r) => r.kind === "read" && r.by === "agent");
    expect(reads.length).toBeGreaterThan(0);
    expect(reads.every((r) => r.state === "auto")).toBe(true);
    const [action] = Object.values(s.rows).filter((r) => r.kind === "action");
    expect(action).toMatchObject({ state: "skipped", who: "policy" });
    expect(steps(s).every((x) => x.state === "done")).toBe(true);
  });

  it("a read whose result is an error is a failed step, not a done one", () => {
    const s = fold(fixture("headless"));
    const [first] = Object.values(s.rows).filter((r) => r.kind === "read" && r.by === "agent" && r.result);
    const failed = { ...s, rows: { ...s.rows, [first!.call_id]: { ...first!, result: { ...first!.result!, status: "error", first_line: "Invalid arguments" } } } };
    const step = steps(failed).find((x) => x.call_id === first!.call_id)!;
    expect(step.state).toBe("failed");
    expect(step.text).toBe("Error: Invalid arguments");
  });

  it("the working picture keeps where each claim started, and rules out rejected ones", () => {
    const s = fold(fixture("chat-approved"));
    const board = Object.values(s.board);
    expect(board.some((h) => h.status === "leading")).toBe(true);
    expect(board.some((h) => h.status === "rejected")).toBe(true);
    for (const h of board) expect(typeof h.first).toBe("number");
  });

  it("a run waiting at a gate shows its cards as pending and is waiting", () => {
    const events = fixture("chat-asking");
    const firstPause = events.findIndex((e) => e.type === "run_paused");
    const s = fold(events.slice(0, firstPause + 1));
    expect(s.status).toBe("waiting");
    expect(s.pending?.cards.map((c) => c.tool)).toEqual(["list_alerts", "list_changes", "read_chat"]);
    expect(s.pending?.cards.every((c) => s.rows[c.call_id]?.state === "awaiting")).toBe(true);
  });

  it("a follow-up answered in text joins the same transcript, and the report it leaves standing is not shown twice", () => {
    const s = fold(fixture("followup-answered"));
    const notes = s.turns.filter((t) => t.kind === "note");
    expect(notes).toHaveLength(1);
    expect(notes[0]).toMatchObject({ followup: true });
    expect(s.turns.filter((t) => t.kind === "answer")).toHaveLength(1);
    expect(s.turns.filter((t) => t.kind === "report")).toHaveLength(1);
    expect(s.followups).toEqual([expect.objectContaining({ n: 1, outcome: "answered" })]);
    expect(s.runs).toHaveLength(1);
    expect(s.status).toBe("completed");
  });

  it("a follow-up that resubmits the report continues the same run", () => {
    const s = fold(fixture("followup"));
    expect(s.turns.filter((t) => t.kind === "note")).toHaveLength(1);
    expect(s.followups).toEqual([expect.objectContaining({ outcome: "report" })]);
    expect(s.runs).toHaveLength(1);
    expect(s.turns.filter((t) => t.kind === "group" && t.by === "agent").length).toBeGreaterThan(fold(fixture("headless")).turns.filter((t) => t.kind === "group" && t.by === "agent").length);
  });
});
