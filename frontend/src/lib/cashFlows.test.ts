import { describe, it, expect } from "vitest";
import {
  toAmount,
  cashFlowGroupKey,
  balanceSpans,
  sameDayOthersCount,
  cashFlowDotColor,
  amountNote,
  projectedBalance,
  normalizeMoneyText,
  formatBalanceCurrency,
  formatBareSigned,
  cashFlowAmountText,
  CASH_FLOW_DOT_COLORS,
  CASH_FLOW_TYPE_OPTIONS,
  CASH_FLOW_STATUS_OPTIONS,
} from "@/lib/cashFlows";
import type { CashFlowRow } from "@/types/cash-flow";

let seq = 1;
function makeRow(over: Partial<CashFlowRow> = {}): CashFlowRow {
  const id = seq++;
  return {
    source_kind: "trade",
    flow_type: "rebal_buy",
    flow_type_label: "调仓 · 买入扣款",
    cash_date: "2026-08-01",
    cash_date_label: "交易日",
    platform_code: "PF1",
    source_main: "买入 F1",
    amount: "-1000.0000",
    is_override: false,
    settled: true,
    status: { variant: "success", label: "已生效" },
    action: { label: "详情", kind: "detail" },
    origin_ref: { kind: "trade", id },
    group_total_count: 1,
    ...over,
  };
}

describe("toAmount", () => {
  it("空值一律 0", () => {
    expect(toAmount(null)).toBe(0);
    expect(toAmount(undefined)).toBe(0);
    expect(toAmount("")).toBe(0);
  });
  it("number 直接返回，string 走 parseFloat", () => {
    expect(toAmount(12.5)).toBe(12.5);
    expect(toAmount("12.5")).toBe(12.5);
    expect(toAmount("-1000.0000")).toBe(-1000);
  });
  it("非法字符串 NaN 收敛为 0", () => {
    expect(toAmount("abc")).toBe(0);
  });
});

describe("cashFlowGroupKey / balanceSpans", () => {
  it("合并键 = 平台|现金日期", () => {
    expect(cashFlowGroupKey(makeRow({ platform_code: "PF1", cash_date: "2026-08-01" }))).toBe(
      "PF1|2026-08-01"
    );
  });
  it("空数组返回空 spans", () => {
    expect(balanceSpans([])).toEqual([]);
  });
  it("连续同 (平台,日期) 合并，被覆盖行标 -1", () => {
    const rows = [
      makeRow({ platform_code: "PF1", cash_date: "2026-08-02" }),
      makeRow({ platform_code: "PF1", cash_date: "2026-08-02" }),
      makeRow({ platform_code: "PF1", cash_date: "2026-08-02" }),
      makeRow({ platform_code: "PF2", cash_date: "2026-08-02" }),
      makeRow({ platform_code: "PF1", cash_date: "2026-08-01" }),
    ];
    expect(balanceSpans(rows)).toEqual([3, -1, -1, 1, 1]);
  });
  it("全不相同 → 全 1", () => {
    const rows = [
      makeRow({ platform_code: "PF1", cash_date: "2026-08-03" }),
      makeRow({ platform_code: "PF2", cash_date: "2026-08-03" }),
    ];
    expect(balanceSpans(rows)).toEqual([1, 1]);
  });
});

describe("sameDayOthersCount", () => {
  it("N = group_total_count − 在屏同组行数", () => {
    const a = makeRow({ platform_code: "PF1", cash_date: "2026-08-01", group_total_count: 4 });
    const b = makeRow({ platform_code: "PF1", cash_date: "2026-08-01", group_total_count: 4 });
    // 在屏同组 2 行，总 4 → 另有 2 笔
    expect(sameDayOthersCount(a, [a, b])).toBe(2);
  });
  it("在屏已全部展示 → 0（不 clamp 成负）", () => {
    const a = makeRow({ platform_code: "PF1", cash_date: "2026-08-01", group_total_count: 2 });
    const b = makeRow({ platform_code: "PF1", cash_date: "2026-08-01", group_total_count: 2 });
    expect(sameDayOthersCount(a, [a, b])).toBe(0);
  });
  it("在屏行数超过 total（异常）也 clamp 到 0", () => {
    const a = makeRow({ platform_code: "PF1", cash_date: "2026-08-01", group_total_count: 1 });
    const b = makeRow({ platform_code: "PF1", cash_date: "2026-08-01", group_total_count: 1 });
    expect(sameDayOthersCount(a, [a, b])).toBe(0);
  });
  it("group_total_count 缺位时按 1 兜底", () => {
    const a = makeRow({ platform_code: "PF1", cash_date: "2026-08-01" });
    const patched = { ...a, group_total_count: undefined } as unknown as CashFlowRow;
    expect(sameDayOthersCount(patched, [patched])).toBe(0);
  });
});

describe("cashFlowDotColor", () => {
  it("重估（is_override）= 灰蓝 neutral，优先于 settled/符号", () => {
    expect(cashFlowDotColor(makeRow({ is_override: true, settled: true, amount: "5000" }))).toBe(
      CASH_FLOW_DOT_COLORS.neutral
    );
  });
  it("未生效 = 天蓝 inTransit", () => {
    expect(cashFlowDotColor(makeRow({ settled: false, amount: "1000" }))).toBe(
      CASH_FLOW_DOT_COLORS.inTransit
    );
  });
  it("已生效负额 = 赭橙 outflow", () => {
    expect(cashFlowDotColor(makeRow({ settled: true, amount: "-1000" }))).toBe(
      CASH_FLOW_DOT_COLORS.outflow
    );
  });
  it("已生效正/零额 = 靛蓝 inflow", () => {
    expect(cashFlowDotColor(makeRow({ settled: true, amount: "1000" }))).toBe(
      CASH_FLOW_DOT_COLORS.inflow
    );
    expect(cashFlowDotColor(makeRow({ settled: true, amount: "0" }))).toBe(
      CASH_FLOW_DOT_COLORS.inflow
    );
  });
});

describe("amountNote", () => {
  it("重估 → 绝对值 · 非流水", () => {
    expect(amountNote(makeRow({ is_override: true }))).toBe("绝对值 · 非流水");
  });
  it("未生效 → 不计入余额", () => {
    expect(amountNote(makeRow({ settled: false }))).toBe("不计入余额");
  });
  it("已生效增量 → null", () => {
    expect(amountNote(makeRow({ settled: true, is_override: false }))).toBeNull();
  });
});

describe("projectedBalance", () => {
  const pending = { label: "确认到账", kind: "confirm" } as const;
  it("已生效行不投影", () => {
    expect(projectedBalance(makeRow({ settled: true, action: pending }))).toBeNull();
  });
  it("重估行不投影", () => {
    expect(projectedBalance(makeRow({ settled: false, is_override: true, action: pending }))).toBeNull();
  });
  it("confirmed 但到账日在未来的行不投影（余额列已含本笔，再 +会重复计）", () => {
    // action.kind !== "confirm"（详情行）：调仓卖出到账腿 A>C / 未来分红等
    expect(
      projectedBalance(
        makeRow({ settled: false, amount: "2000", platform_end_of_day_balance: "8000" })
      )
    ).toBeNull();
  });
  it("日终余额缺位不投影", () => {
    expect(
      projectedBalance(makeRow({ settled: false, action: pending, platform_end_of_day_balance: null }))
    ).toBeNull();
    expect(
      projectedBalance(makeRow({ settled: false, action: pending, platform_end_of_day_balance: undefined }))
    ).toBeNull();
  });
  it("pending 转入腿 = 日终余额（不含本笔）+ 本笔带符号金额", () => {
    expect(
      projectedBalance(
        makeRow({
          settled: false,
          action: pending,
          amount: "2000.0000",
          platform_end_of_day_balance: "8000.0000",
        })
      )
    ).toBe(10000);
  });
});

describe("normalizeMoneyText", () => {
  it("空值 → 空串", () => {
    expect(normalizeMoneyText(null)).toBe("");
    expect(normalizeMoneyText(undefined)).toBe("");
    expect(normalizeMoneyText("")).toBe("");
  });
  it("4 位小数裸数字收敛为 2 位货币", () => {
    expect(normalizeMoneyText("覆盖前计算值 92400.0000")).toBe("覆盖前计算值 ¥92,400.00");
  });
  it("负数也收敛", () => {
    expect(normalizeMoneyText("差额 -1234.5000")).toBe("差额 -¥1,234.50");
  });
  it("无小数点数字不误伤（日期用连字符、代码无小数）", () => {
    expect(normalizeMoneyText("F1 · 2026-08-01")).toBe("F1 · 2026-08-01");
  });
});

describe("formatBalanceCurrency", () => {
  it("符号在 ¥ 前，非负不补 +", () => {
    expect(formatBalanceCurrency("1234.5")).toBe("¥1,234.50");
    expect(formatBalanceCurrency(0)).toBe("¥0.00");
    expect(formatBalanceCurrency("-1234.5")).toBe("-¥1,234.50");
  });
  it("空值/非法值 fallback --（§12 空值占位，#727 S4），真 0 不走 fallback", () => {
    expect(formatBalanceCurrency(null)).toBe("--");
    expect(formatBalanceCurrency(undefined)).toBe("--");
    expect(formatBalanceCurrency("")).toBe("--");
    expect(formatBalanceCurrency("abc")).toBe("--");
    expect(formatBalanceCurrency("0")).toBe("¥0.00");
  });
});

describe("cashFlowAmountText", () => {
  it("重估行绝对值走存量档：正数不补 +（#727 N3）", () => {
    expect(cashFlowAmountText(makeRow({ is_override: true, amount: "5000.0000" }))).toBe(
      "¥5,000.00"
    );
  });
  it("非重估行走流量档：± 在 ¥ 前", () => {
    expect(cashFlowAmountText(makeRow({ amount: "1000.0000" }))).toBe("+¥1,000.00");
    expect(cashFlowAmountText(makeRow({ amount: "-1000.0000" }))).toBe("-¥1,000.00");
  });
});

describe("formatBareSigned", () => {
  it("裸数字带 ±、不带 ¥", () => {
    expect(formatBareSigned("1000")).toBe("+1,000.00");
    expect(formatBareSigned("-1000")).toBe("-1,000.00");
    expect(formatBareSigned("0")).toBe("0.00");
  });
  it("空值按 0 处理", () => {
    expect(formatBareSigned(undefined)).toBe("0.00");
  });
});

describe("选项常量与后端逐字对齐", () => {
  it("flow_type 七值顺序与 label", () => {
    expect(CASH_FLOW_TYPE_OPTIONS.map((o) => o.value)).toEqual([
      "rebal_buy",
      "rebal_sell",
      "sub_buy",
      "redeem",
      "dividend",
      "transfer",
      "revalue",
    ]);
    expect(CASH_FLOW_TYPE_OPTIONS.find((o) => o.value === "sub_buy")?.label).toBe("申购 · 注资入账");
    expect(CASH_FLOW_TYPE_OPTIONS.find((o) => o.value === "redeem")?.label).toBe("赎回 · 支付");
  });
  it("status 两值", () => {
    expect(CASH_FLOW_STATUS_OPTIONS).toEqual([
      { value: "settled", label: "已生效" },
      { value: "in_transit", label: "在途" },
    ]);
  });
});
