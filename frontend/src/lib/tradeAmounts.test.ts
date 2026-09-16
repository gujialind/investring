import { describe, it, expect } from "vitest";
import {
  quantizeAmount2,
  formatAmount2,
  netFromActual,
  actualFromNet,
  sellDerivedAmounts,
  applyBuyAmountLinkage,
} from "@/lib/tradeAmounts";

describe("quantizeAmount2（对齐后端 quantize_amount：2 位 HALF_UP、负数对称）", () => {
  it("常规两位小数舍入", () => {
    expect(quantizeAmount2(1.004)).toBe(1.0);
    expect(quantizeAmount2(1.006)).toBe(1.01);
    expect(quantizeAmount2(999.999)).toBe(1000.0);
  });

  it("边界值 0.5 分远离零进位（字符串位移口径，避免 toFixed 误判）", () => {
    expect(quantizeAmount2(1.005)).toBe(1.01);
    expect(quantizeAmount2(2.675)).toBe(2.68);
    expect(quantizeAmount2("1.005")).toBe(1.01);
  });

  it("负数按绝对值对称（远离零进位）", () => {
    expect(quantizeAmount2(-1.005)).toBe(-1.01);
    expect(quantizeAmount2(-1.004)).toBe(-1.0);
  });

  it("浮点噪声不漂移（如 1000.1 − 5.05 的 JS 结果）", () => {
    expect(quantizeAmount2(1000.1 - 5.05)).toBe(995.05);
  });

  it("非法输入返回 null", () => {
    expect(quantizeAmount2("")).toBeNull();
    expect(quantizeAmount2("  ")).toBeNull();
    expect(quantizeAmount2("abc")).toBeNull();
    expect(quantizeAmount2(NaN)).toBeNull();
    expect(quantizeAmount2(Infinity)).toBeNull();
  });
});

describe("formatAmount2", () => {
  it("输出固定 2 位小数字符串", () => {
    expect(formatAmount2(995.05)).toBe("995.05");
    expect(formatAmount2(1000)).toBe("1000.00");
    expect(formatAmount2(0)).toBe("0.00");
  });

  it("非有限入参回填空串（非法输入不落屏）", () => {
    expect(formatAmount2(NaN)).toBe("");
    expect(formatAmount2(Infinity)).toBe("");
  });
});

describe("netFromActual / actualFromNet（买入双字段联动公式）", () => {
  it("净额 = 实付 − 手续费", () => {
    expect(netFromActual("1000", "5")).toBe(995.0);
    expect(netFromActual(1000, 5)).toBe(995.0);
    expect(netFromActual("1000.10", "5.05")).toBe(995.05);
  });

  it("实付 = 净额 + 手续费", () => {
    expect(actualFromNet("995", "5")).toBe(1000.0);
    expect(actualFromNet(995.05, 5.05)).toBe(1000.1);
  });

  it("fee 空串/0 时两口径相等", () => {
    expect(netFromActual("1000", "")).toBe(1000.0);
    expect(netFromActual("1000", "0")).toBe(1000.0);
    expect(actualFromNet("1000", "")).toBe(1000.0);
  });

  it("主字段缺失/非法返回 null（fee 非法同样 null）", () => {
    expect(netFromActual("", "5")).toBeNull();
    expect(netFromActual("abc", "5")).toBeNull();
    expect(netFromActual("1000", "abc")).toBeNull();
    expect(actualFromNet("", "5")).toBeNull();
  });

  it("净额可为负（由组件侧防护阻断，函数只如实计算）", () => {
    expect(netFromActual("3", "5")).toBe(-2.0);
  });
});

describe("sellDerivedAmounts（镜像后端 _derive_sell_amounts 有价分支）", () => {
  it("毛额 = quantize(份额×价格)、到手 = 毛额 − 手续费", () => {
    expect(sellDerivedAmounts("100", "10", "5")).toEqual({ gross: 1000.0, actualReceived: 995.0 });
    expect(sellDerivedAmounts(100, 10, 0)).toEqual({ gross: 1000.0, actualReceived: 1000.0 });
    expect(sellDerivedAmounts(100, 10, "")).toEqual({ gross: 1000.0, actualReceived: 1000.0 });
  });

  it("价格保持全精度参与乘法（净值 4 位），毛额才量化", () => {
    // 100.5 × 1.2345 = 124.06725 → 毛额 124.07 → 到手 124.07 − 0.01
    expect(sellDerivedAmounts("100.5", "1.2345", "0.01")).toEqual({
      gross: 124.07,
      actualReceived: 124.06,
    });
  });

  it("到手可为非正（由组件侧防护阻断，函数只如实计算）", () => {
    expect(sellDerivedAmounts("100", "10", "1000")).toEqual({ gross: 1000.0, actualReceived: 0 });
    expect(sellDerivedAmounts("100", "10", "1200")).toEqual({ gross: 1000.0, actualReceived: -200.0 });
  });

  it("份额或价格缺失/非法返回 null（场外未传价不展示）", () => {
    expect(sellDerivedAmounts("", "10", "5")).toBeNull();
    expect(sellDerivedAmounts("100", "", "5")).toBeNull();
    expect(sellDerivedAmounts("100", "abc", "5")).toBeNull();
    expect(sellDerivedAmounts("abc", "10", "5")).toBeNull();
  });

  it("极端数值下中间结果非有限时返回 null（无法量化即不渲染）", () => {
    // 本组锁的契约是「中间结果无法量化 ⇒ 返回 null」：
    // ① 份额×价格溢出为 Infinity → tradeAmounts.ts L64 的 gross 守卫；
    expect(sellDerivedAmounts(1e18, 1e300, 0)).toBeNull();
    // ② 手续费大到 quantizeAmount2 产出 NaN（1e19 级指数记法边界，根因见 #504）→ 今日经
    //    L66 的到手守卫返回 null；#504 按「非有限结果返回 null」修复后改由 L62 拦截，
    //    断言不变（若 #504 选择「抛错」修法，本条须同步改）。
    expect(sellDerivedAmounts(1, 1, 1e19)).toBeNull();
  });
});

describe("applyBuyAmountLinkage（联动状态推进：手改保留原值、fee 按锚点重算）", () => {
  it("手改实付 → 净投入派生，实付保留原始输入", () => {
    expect(
      applyBuyAmountLinkage("actual", "actual", { actual: "1000", net: "", fee: "5" })
    ).toEqual({ actual: "1000", net: "995.00" });
    // 输入中间态不抹掉用户原值
    expect(
      applyBuyAmountLinkage("actual", "actual", { actual: "12.", net: "", fee: "" })
    ).toEqual({ actual: "12.", net: "12.00" });
  });

  it("手改净投入 → 实付派生，净投入保留原始输入", () => {
    expect(
      applyBuyAmountLinkage("net", "net", { actual: "", net: "995", fee: "5" })
    ).toEqual({ actual: "1000.00", net: "995" });
  });

  it("fee 变更按锚点重算：锚在净投入 → 净额不变、实付重算", () => {
    expect(
      applyBuyAmountLinkage("fee", "net", { actual: "1000.00", net: "995", fee: "10" })
    ).toEqual({ actual: "1005.00", net: "995" });
  });

  it("fee 变更按锚点重算：锚在实付 → 实付不变、净额重算", () => {
    expect(
      applyBuyAmountLinkage("fee", "actual", { actual: "1000", net: "995.00", fee: "10" })
    ).toEqual({ actual: "1000", net: "990.00" });
  });

  it("锚点字段为空/非法 → 联动字段清空", () => {
    expect(
      applyBuyAmountLinkage("fee", "actual", { actual: "", net: "995.00", fee: "10" })
    ).toEqual({ actual: "", net: "" });
    expect(
      applyBuyAmountLinkage("actual", "actual", { actual: "", net: "995.00", fee: "5" })
    ).toEqual({ actual: "", net: "" });
  });
});
