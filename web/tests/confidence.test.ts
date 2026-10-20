import { describe, expect, it } from "vitest";
import { confidenceLabel } from "@/lib/format";

// v4 plan section 3: one confidence form, "High (78%)". High is 0.7 and up, Medium 0.4 and up, Low below.
describe("confidenceLabel", () => {
  it.each([
    [0, "Low (0%)"], [0.39, "Low (39%)"], [0.4, "Medium (40%)"], [0.69, "Medium (69%)"], [0.7, "High (70%)"], [0.78, "High (78%)"],
    [1, "High (100%)"], [0.785, "High (79%)"], [0.394, "Low (39%)"],
  ])("%s gives %s", (value, label) => expect(confidenceLabel(value)).toBe(label));

  it("writes nothing for no value and clamps the range", () => {
    expect(confidenceLabel(null)).toBe("");
    expect(confidenceLabel(Number.NaN)).toBe("");
    expect(confidenceLabel(1.2)).toBe("High (100%)");
    expect(confidenceLabel(-0.1)).toBe("Low (0%)");
  });
});
