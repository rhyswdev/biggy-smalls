import { describe, expect, it } from "vitest";
// @ts-expect-error: a plain script, no types
import { hits } from "../scripts/check-vocab.mjs";

// The vocabulary check finds the old words only where a person reads them.
describe("check-vocab", () => {
  it("flags shown text and plural() words", () => {
    const found = hits(`export const A = () => <p>The agent reads logs</p>; const b = "Owner"; const c = plural(2, "read");`, "a.tsx") as { word: string }[];
    expect(found.map((h) => h.word)).toEqual(["reads", "Owner", "read"]);
  });

  it("skips identifiers, enum values, imports, hidden attributes and Read-only", () => {
    const code = `import r from "./reads"; const k = x.kind === "read"; const s = h.status === "leading";
      const e = <span className="sm-tag-leading" data-x="reads">Read-only. Nothing runs until you choose.</span>; const o = { owner: "Group" };`;
    expect(hits(code, "b.tsx")).toEqual([]);
  });
});
