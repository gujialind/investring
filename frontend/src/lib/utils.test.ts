import { describe, it, expect } from "vitest";
import {
  cn,
  formatNumber,
  formatCurrency,
  formatShares,
  formatSharesUnit,
  formatNav,
  formatAmount4,
  formatCompactCurrency,
  formatPercent,
  formatReturnRate,
  largestRemainderPercents,
  getReturnColorClass,
  getReturnBgClass,
  getStatusBadgeVariant,
  getSignedReturn,
  getNumberCellClass,
  toDateOnly,
  parseDateOnly,
  formatDate,
  formatMarketName,
  formatProductName,
  truncateText,
} from "@/lib/utils";

describe("formatNumber", () => {
  it("千分位 + 默认 2 位小数", () => {
    expect(formatNumber(1234.5)).toBe("1,234.50");
    expect(formatNumber(1234.567)).toBe("1,234.57");
  });

  it("真 0 不被占位符掩盖", () => {
    expect(formatNumber(0)).toBe("0.00");
  });

  it("无效值走 fallback", () => {
    expect(formatNumber(undefined)).toBe("--");
    expect(formatNumber(null)).toBe("--");
    expect(formatNumber("")).toBe("--");
    expect(formatNumber(NaN)).toBe("--");
    expect(formatNumber(undefined, 2, "N/A")).toBe("N/A");
  });

  it("字符串数字与自定义小数位", () => {
    expect(formatNumber("1234.5")).toBe("1,234.50");
    expect(formatNumber(1234.5, 0)).toBe("1,235");
  });
});

describe("formatCurrency / formatShares / formatNav / formatAmount4", () => {
  it("金额带 ¥ 前缀，真 0 显示 ¥0.00", () => {
    expect(formatCurrency(0)).toBe("¥0.00");
    expect(formatCurrency(1234.5)).toBe("¥1,234.50");
    expect(formatCurrency(null)).toBe("--");
  });

  it("份额固定 2 位不带符号", () => {
    expect(formatShares(8933.891)).toBe("8,933.89");
    expect(formatShares(undefined)).toBe("--");
  });

  it("净值固定 4 位不带 ¥", () => {
    expect(formatNav(1.23456)).toBe("1.2346");
    expect(formatNav(1)).toBe("1.0000");
  });

  it("对账金额 4 位带 ¥", () => {
    expect(formatAmount4(1234.5)).toBe("¥1,234.5000");
  });
});

describe("formatSharesUnit", () => {
  it("符号在数字内、单位在外（visual-spec §12）", () => {
    expect(formatSharesUnit(8933.891)).toBe("8,933.89 份");
    expect(formatSharesUnit(-1000)).toBe("-1,000.00 份");
    expect(formatSharesUnit(0)).toBe("0.00 份");
  });

  it("无效值走 fallback，不受单位影响", () => {
    expect(formatSharesUnit(undefined)).toBe("--");
    expect(formatSharesUnit(null)).toBe("--");
    expect(formatSharesUnit("")).toBe("--");
    expect(formatSharesUnit(NaN)).toBe("--");
    expect(formatSharesUnit(null, "N/A")).toBe("N/A");
  });
});

describe("formatCompactCurrency", () => {
  it("万/亿分档", () => {
    expect(formatCompactCurrency(9999)).toBe("¥9,999.00");
    expect(formatCompactCurrency(123456)).toBe("¥12.35 万");
    expect(formatCompactCurrency(123456789)).toBe("¥1.23 亿");
    expect(formatCompactCurrency(-12345)).toBe("¥-1.23 万");
  });

  it("无效值走 fallback", () => {
    expect(formatCompactCurrency(undefined)).toBe("--");
  });
});

describe("formatPercent / formatReturnRate", () => {
  it("小数输入转百分比并带符号", () => {
    expect(formatPercent(0.0523)).toBe("+5.23%");
    expect(formatPercent(-0.0123)).toBe("-1.23%");
    expect(formatPercent(0)).toBe("0.00%");
    expect(formatPercent(0.0523, 2, false)).toBe("5.23%");
    expect(formatPercent(null)).toBe("--");
  });

  it("百分比数值直传", () => {
    expect(formatReturnRate(5.23)).toBe("+5.23%");
    expect(formatReturnRate(-1.23)).toBe("-1.23%");
    expect(formatReturnRate(null)).toBe("--");
    expect(formatReturnRate("abc")).toBe("--");
  });
});

describe("largestRemainderPercents", () => {
  it("加总恒为 100.0", () => {
    const cases = [
      [0.5, 0.3, 0.2],
      [1 / 3, 1 / 3, 1 / 3],
      [0.1, 0.2, 0.3, 0.4],
      [1],
    ];
    for (const weights of cases) {
      const result = largestRemainderPercents(weights);
      expect(result.reduce((s, p) => s + p, 0)).toBeCloseTo(100, 6);
    }
  });

  it("均分余数按原顺序稳定分配", () => {
    expect(largestRemainderPercents([1 / 3, 1 / 3, 1 / 3])).toEqual([33.4, 33.3, 33.3]);
  });

  it("空数组与零权重", () => {
    expect(largestRemainderPercents([])).toEqual([]);
    expect(largestRemainderPercents([0, 0])).toEqual([0, 0]);
  });

  it("Σ≠1 时按 Σ 归一化", () => {
    expect(largestRemainderPercents([2, 3, 5])).toEqual([20, 30, 50]);
  });
});

describe("日期格式化", () => {
  it("date-only 字符串按本地零点解析，不回退一天", () => {
    expect(formatDate("2026-08-29")).toBe("2026-08-29");
    expect(formatDate(parseDateOnly("2026-01-01")!)).toBe("2026-01-01");
  });

  it("toDateOnly / parseDateOnly 互逆", () => {
    const d = parseDateOnly("2026-02-03")!;
    expect(toDateOnly(d)).toBe("2026-02-03");
  });

  it("非法输入", () => {
    expect(formatDate("not-a-date")).toBe("--");
    expect(parseDateOnly("")).toBeUndefined();
    expect(parseDateOnly("2026-13-99")).toBeUndefined();
    expect(toDateOnly(undefined)).toBe("");
    expect(toDateOnly(new Date("invalid"))).toBe("");
  });
});

describe("涨跌色与状态徽标", () => {
  it("红涨绿跌，0 与无效值中性", () => {
    expect(getReturnColorClass(1)).toBe("text-gain");
    expect(getReturnColorClass(-1)).toBe("text-loss");
    expect(getReturnColorClass(0)).toBe("text-muted-foreground");
    expect(getReturnColorClass(null)).toBe("text-muted-foreground");
    expect(getReturnBgClass(1)).toBe("bg-gain-soft");
    expect(getReturnBgClass(-1)).toBe("bg-loss-soft");
    expect(getReturnBgClass(0)).toBe("bg-muted");
    expect(getReturnBgClass(null)).toBe("bg-muted");
    expect(getReturnBgClass("abc")).toBe("bg-muted");
  });

  it("状态到 variant 的映射，未知状态回落 neutral", () => {
    expect(getStatusBadgeVariant("confirmed")).toBe("success");
    expect(getStatusBadgeVariant("pending")).toBe("warning");
    expect(getStatusBadgeVariant("failed")).toBe("destructive");
    expect(getStatusBadgeVariant("whatever")).toBe("neutral");
    expect(getStatusBadgeVariant(null)).toBe("neutral");
  });

  it("getSignedReturn 文本与颜色自洽", () => {
    expect(getSignedReturn(5.2)).toEqual({ text: "+5.20%", colorClass: "text-gain" });
    expect(getSignedReturn(undefined)).toEqual({ text: "--", colorClass: "text-muted-foreground" });
  });
});

describe("其他工具", () => {
  it("formatMarketName 映射与回退", () => {
    expect(formatMarketName("CN_EXCHANGE")).toBe("A股场内");
    expect(formatMarketName("UNKNOWN")).toBe("UNKNOWN");
    expect(formatMarketName(null)).toBe("--");
  });

  it("formatProductName 双信息与回退", () => {
    expect(formatProductName("某基金", "000001")).toBe("某基金（000001）");
    expect(formatProductName(null, "000001")).toBe("000001");
    expect(formatProductName("某基金", "")).toBe("--");
    expect(formatProductName(null, null)).toBe("--");
  });

  it("truncateText", () => {
    expect(truncateText("短文本", 10)).toBe("短文本");
    expect(truncateText("一二三四五六", 3)).toBe("一二三...");
    expect(truncateText("", 3)).toBe("");
  });

  it("cn 过滤 falsy 参数，冲突类由 twMerge 取后者", () => {
    expect(cn("text-sm", undefined, null, "", false, "font-bold")).toBe("text-sm font-bold");
    expect(cn("p-2", "p-4")).toBe("p-4");
    // 组件里的真实组合（getNumberCellClass 与颜色类不冲突，须全部保留）
    expect(cn("text-muted-foreground", getNumberCellClass())).toBe(
      "text-muted-foreground text-right font-mono tabular-nums"
    );
  });

  it("getNumberCellClass 固定类串", () => {
    expect(getNumberCellClass()).toBe("text-right font-mono tabular-nums");
  });
});
