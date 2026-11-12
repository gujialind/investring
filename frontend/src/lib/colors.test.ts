import { describe, it, expect } from "vitest";
import {
  assetClassColor,
  CHART_COLORS,
  CHART_OTHER,
  DARK_CHART_COLORS,
  IN_TRANSIT_COLOR,
  NAV_LINE,
  OTHER_COLOR,
  TRADE_DIRECTION_COLORS,
  chartPalette,
  chartOtherColor,
  inTransitColor,
  navLineColor,
  otherColor,
  tradeDirectionColors,
} from "@/lib/colors";

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

describe("theme 访问器（#872 PR1 就绪，默认 light 行为不变）", () => {
  it("默认 light：返回值与既有常量全等", () => {
    expect(chartPalette()).toBe(CHART_COLORS);
    expect(navLineColor()).toBe(NAV_LINE);
    expect(chartOtherColor()).toBe(CHART_OTHER);
    expect(inTransitColor()).toBe(IN_TRANSIT_COLOR);
    expect(otherColor()).toBe(OTHER_COLOR);
    expect(tradeDirectionColors()).toEqual(TRADE_DIRECTION_COLORS);
  });

  it("dark 色板 golden 值（visual-spec §2，对暗卡实测 ≥3:1）", () => {
    expect(DARK_CHART_COLORS).toEqual([
      "#5C82E0", "#E8A33D", "#56B4E9", "#9C8AE8",
      "#CC79A7", "#C9762E", "#8A97AC", "#7A87AD",
    ]);
    expect(chartPalette("dark")).toBe(DARK_CHART_COLORS);
  });

  it("dark 访问器按序位取色（语义与 light 常量逐一对应）", () => {
    expect(navLineColor("dark")).toBe(DARK_CHART_COLORS[0]);
    expect(chartOtherColor("dark")).toBe(DARK_CHART_COLORS[6]);
    expect(inTransitColor("dark")).toBe(DARK_CHART_COLORS[2]);
    expect(otherColor("dark")).toBe(DARK_CHART_COLORS[7]);
    expect(tradeDirectionColors("dark")).toEqual({
      buy: DARK_CHART_COLORS[0],
      sell: DARK_CHART_COLORS[5],
    });
  });

  it("dark 大类序位与 light 一致，超出序位兜底暗 C8", () => {
    expect(assetClassColor(1, "dark")).toBe(DARK_CHART_COLORS[0]);
    expect(assetClassColor(2, "dark")).toBe(DARK_CHART_COLORS[3]);
    expect(assetClassColor(3, "dark")).toBe(DARK_CHART_COLORS[1]);
    expect(assetClassColor(4, "dark")).toBe(DARK_CHART_COLORS[6]);
    expect(assetClassColor(5, "dark")).toBe(DARK_CHART_COLORS[7]);
    expect(assetClassColor(0, "dark")).toBe(DARK_CHART_COLORS[7]);
  });

  it("沿用的 5 色双主题同 hex（最小化双套差异）", () => {
    for (const i of [1, 2, 4, 5, 6]) {
      expect(DARK_CHART_COLORS[i]).toBe(CHART_COLORS[i]);
    }
  });
});
