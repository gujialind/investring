import { describe, it, expect } from "vitest";
import { groupTradeRows, cashSubMeta, cashOrphanLabel } from "@/lib/tradePairs";
import type { Trade } from "@/types/trade";

let nextId = 1;
function makeTrade(over: Partial<Trade>): Trade {
  return {
    id: nextId++,
    portfolio_code: "P1",
    product_code: "F1",
    trade_type: "buy",
    fee: 0,
    trade_date: "2026-08-01",
    status: "confirmed",
    ...over,
  };
}

describe("groupTradeRows", () => {
  it("sub_ 前缀组（申赎配对现金腿）恒为 single", () => {
    const fund = makeTrade({ product_code: "F1", transfer_group: "sub_42" });
    const cash = makeTrade({ product_code: "CASH", trade_type: "sell", transfer_group: "sub_42" });
    expect(groupTradeRows([fund, cash])).toEqual([
      { kind: "single", trade: fund },
      { kind: "single", trade: cash },
    ]);
  });

  it("基金腿 + CASH 腿结对为 pair（基金主、现金子），与传入顺序无关", () => {
    const fund = makeTrade({ product_code: "F1", transfer_group: "rebal_a" });
    const cash = makeTrade({ product_code: "CASH", trade_type: "sell", transfer_group: "rebal_a" });
    const expected = [{ kind: "pair", main: fund, sub: cash }];
    expect(groupTradeRows([fund, cash])).toEqual(expected);
    expect(groupTradeRows([cash, fund])).toEqual(expected);
  });

  it("双 CASH 腿（现金跨平台转移）结对：sell 主、buy 子", () => {
    const sell = makeTrade({ product_code: "CASH", trade_type: "sell", transfer_group: "0123456789ab" });
    const buy = makeTrade({ product_code: "CASH", trade_type: "buy", transfer_group: "0123456789ab" });
    expect(groupTradeRows([buy, sell])).toEqual([{ kind: "pair", main: sell, sub: buy }]);
  });

  it("双 CASH 腿顺序颠倒（sell 在前）同样 sell 主、buy 子", () => {
    const sell = makeTrade({ product_code: "CASH", trade_type: "sell", transfer_group: "abcdef123456" });
    const buy = makeTrade({ product_code: "CASH", trade_type: "buy", transfer_group: "abcdef123456" });
    expect(groupTradeRows([sell, buy])).toEqual([{ kind: "pair", main: sell, sub: buy }]);
  });

  it("双 CASH 腿缺 sell（异常数据）回落两行 single", () => {
    const first = makeTrade({ product_code: "CASH", trade_type: "buy", transfer_group: "abcdef123457" });
    const second = makeTrade({ product_code: "CASH", trade_type: "buy", transfer_group: "abcdef123457" });
    expect(groupTradeRows([first, second])).toEqual([
      { kind: "single", trade: first },
      { kind: "single", trade: second },
    ]);
  });

  it("孤儿单腿回退 single", () => {
    const t = makeTrade({ transfer_group: "rebal_solo" });
    expect(groupTradeRows([t])).toEqual([{ kind: "single", trade: t }]);
  });

  it("组内异常多条全部 single 回退", () => {
    const legs = [1, 2, 3].map(() => makeTrade({ transfer_group: "rebal_x" }));
    expect(groupTradeRows(legs)).toEqual(legs.map((t) => ({ kind: "single", trade: t })));
  });

  it("缺失 transfer_group 按 id 自成一组走 single", () => {
    const t = makeTrade({ transfer_group: undefined });
    expect(groupTradeRows([t])).toEqual([{ kind: "single", trade: t }]);
  });

  it("输出保持传入顺序（组序 = 首腿出现序）", () => {
    const a = makeTrade({ transfer_group: "rebal_a" });
    const b = makeTrade({ transfer_group: "rebal_b" });
    const aCash = makeTrade({ product_code: "CASH", trade_type: "sell", transfer_group: "rebal_a" });
    const rows = groupTradeRows([a, b, aCash]);
    expect(rows[0]).toEqual({ kind: "pair", main: a, sub: aCash });
    expect(rows[1]).toEqual({ kind: "single", trade: b });
  });
});

describe("cashSubMeta", () => {
  it("买入主行 → 现金扣款（-）；卖出主行 → 现金到账（+）", () => {
    expect(cashSubMeta(makeTrade({ trade_type: "buy" }))).toEqual({ label: "现金扣款", sign: "-" });
    expect(cashSubMeta(makeTrade({ trade_type: "sell" }))).toEqual({ label: "现金到账", sign: "+" });
  });
});

describe("cashOrphanLabel", () => {
  it("按 transfer_group 前缀推导来源", () => {
    expect(cashOrphanLabel(makeTrade({ transfer_group: "sub_1" }))).toBe("现金 · 申赎确认");
    expect(cashOrphanLabel(makeTrade({ transfer_group: "0123456789ab" }))).toBe("现金 · 平台间转移");
    expect(cashOrphanLabel(makeTrade({ transfer_group: "rebal_x" }))).toBe("现金 · 调仓");
    expect(cashOrphanLabel(makeTrade({ transfer_group: undefined }))).toBe("现金 · 调仓");
  });
});
