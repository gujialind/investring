import { describe, it, expect } from "vitest";
import { MARKET_OPTIONS } from "@/lib/market";
import { formatMarketName } from "@/lib/utils";

// #484：MARKET_OPTIONS 被产品筛选弹窗 / 产品管理页 / 交易产品选择器三处共用，
// 顺序与中文名是展示契约；label 又声明「经 formatMarketName 与产品表单下拉一致」，
// 故既钉字面量（防手滑改文案）也钉同源（防两处口径漂移）。
describe("MARKET_OPTIONS", () => {
  it("三市场顺序与中文名固定", () => {
    expect(MARKET_OPTIONS).toEqual([
      { value: "CN_EXCHANGE", label: "A股场内" },
      { value: "CN_OTC", label: "内地场外" },
      { value: "HK_MUTUAL", label: "香港互认" },
    ]);
  });

  it("label 与 formatMarketName 同源", () => {
    for (const option of MARKET_OPTIONS) {
      expect(option.label).toBe(formatMarketName(option.value));
    }
  });
});
