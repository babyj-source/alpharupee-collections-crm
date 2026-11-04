import { describe, expect, it } from "vitest";
import { inr, num } from "./api.js";

describe("formatters", () => {
  it("formats rupees in the Indian grouping", () => {
    expect(inr(1234567)).toContain("12,34,567");
  });
  it("treats empty as zero", () => {
    expect(num(null)).toBe("0");
  });
});
