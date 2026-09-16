import { describe, it, expect } from "vitest";
import { assetClassColor, CHART_COLORS } from "@/lib/colors";

describe("assetClassColor", () => {
  it("按 sort_order 序位取四大类色（1-4）", () => {
    expect(assetClassColor(1)).toBe(CHART_COLORS[0]);
    expect(assetClassColor(4)).toBe(CHART_COLORS[6]);
  });

  it("序位超出现有 4 大类（未来扩展）兜底深灰蓝 C8", () => {
    expect(assetClassColor(5)).toBe(CHART_COLORS[7]);
    expect(assetClassColor(0)).toBe(CHART_COLORS[7]);
  });
});
