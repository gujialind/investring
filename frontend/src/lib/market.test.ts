import { describe, it, expect } from "vitest";
import { MARKET_OPTIONS } from "@/lib/market";
import { formatMarketName } from "@/lib/utils";

// #484：MARKET_OPTIONS 被产品筛选弹窗 / 产品管理页 / 交易产品选择器 / 产品表单下拉
// 共用（#502 起表单也直接消费），顺序与中文名是展示契约；label 由 formatMarketName
// 单一派生，故既钉字面量（防手滑改文案）也钉派生（防与其它展示处口径漂移）。
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
