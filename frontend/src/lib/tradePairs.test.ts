import { describe, it, expect } from "vitest";
import {
  groupTradeRows,
  cashSubMeta,
  cashLegArrived,
  cashOrphanLabel,
  isCashLeg,
  canEditArrivalDate,
} from "@/lib/tradePairs";
import { toDateOnly } from "@/lib/utils";
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

  it("双 CASH 腿均 buy（缺 sell，异常数据）回落两行 single", () => {
    const first = makeTrade({ product_code: "CASH", trade_type: "buy", transfer_group: "abcdef123457" });
    const second = makeTrade({ product_code: "CASH", trade_type: "buy", transfer_group: "abcdef123457" });
    expect(groupTradeRows([first, second])).toEqual([
      { kind: "single", trade: first },
      { kind: "single", trade: second },
    ]);
  });

  it("双 CASH 腿均 sell（缺 buy，异常数据）回落两行 single，不按「sell 主 buy 子」错配", () => {
    const first = makeTrade({ product_code: "CASH", trade_type: "sell", transfer_group: "abcdef123458" });
    const second = makeTrade({ product_code: "CASH", trade_type: "sell", transfer_group: "abcdef123458" });
    expect(groupTradeRows([first, second])).toEqual([
      { kind: "single", trade: first },
      { kind: "single", trade: second },
    ]);
  });

  it("两条均非 CASH（异常数据）回落两行 single，子行不错渲成现金子行", () => {
    const f1 = makeTrade({ product_code: "F1", trade_type: "buy", transfer_group: "rebal_ff" });
    const f2 = makeTrade({ product_code: "F2", trade_type: "sell", transfer_group: "rebal_ff" });
    expect(groupTradeRows([f1, f2])).toEqual([
      { kind: "single", trade: f1 },
      { kind: "single", trade: f2 },
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

  // #493：卖出到账腿可携带未来 confirm_date（此时快照记 IN_TRANSIT_SELL），
  // 主行状态讲的是基金腿，现金子行须自行区分「已到账 / 待到账」
  it("卖出未到账 → 现金待到账；买入方向不受 arrived 影响（扣款即事实）", () => {
    expect(cashSubMeta(makeTrade({ trade_type: "sell" }), { arrived: false })).toEqual({
      label: "现金待到账",
      sign: "+",
    });
    expect(cashSubMeta(makeTrade({ trade_type: "sell" }), { arrived: true })).toEqual({
      label: "现金到账",
      sign: "+",
    });
    expect(cashSubMeta(makeTrade({ trade_type: "buy" }), { arrived: false })).toEqual({
      label: "现金扣款",
      sign: "-",
    });
  });
});

describe("cashLegArrived（#493 评审加固：口径 = confirmed 且日期不在未来）", () => {
  const TODAY = "2026-09-18";

  it("confirmed 且生效日已到（含当天）→ 已到账", () => {
    expect(
      cashLegArrived(makeTrade({ status: "confirmed", confirm_date: "2026-09-17" }), TODAY),
    ).toBe(true);
    expect(
      cashLegArrived(makeTrade({ status: "confirmed", confirm_date: TODAY }), TODAY),
    ).toBe(true);
  });

  it("confirmed 但生效日在未来 → 未到账（卖出到账腿 A > C，快照记 IN_TRANSIT_SELL）", () => {
    expect(
      cashLegArrived(makeTrade({ status: "confirmed", confirm_date: "2026-09-21" }), TODAY),
    ).toBe(false);
  });

  // 只看日期的旧口径会把这一档错标成「现金到账」，而 pending 腿不计入可用现金（根 AGENTS.md §2.5）
  it("pending 腿即便生效日已到（跨天现金转移的转入腿到期未确认）→ 未到账", () => {
    expect(
      cashLegArrived(makeTrade({ status: "pending", confirm_date: "2026-09-17" }), TODAY),
    ).toBe(false);
    expect(cashLegArrived(makeTrade({ status: "pending", confirm_date: TODAY }), TODAY)).toBe(
      false,
    );
  });

  it("cancelled 腿 → 未到账（整组回退后不得再显示现金到账）", () => {
    expect(
      cashLegArrived(makeTrade({ status: "cancelled", confirm_date: "2026-09-17" }), TODAY),
    ).toBe(false);
  });

  it("无生效日（尚未生效的 pending 腿）→ 未到账", () => {
    expect(
      cashLegArrived(makeTrade({ status: "confirmed", confirm_date: undefined }), TODAY),
    ).toBe(false);
  });

  // 缺省 today = 今天：买入扣款腿创建即 confirmed、现金日 = 下单日 T ⇒ 行为不变
  it("缺省基准为当天（不传 today 时按系统日期判断）", () => {
    expect(cashLegArrived(makeTrade({ status: "confirmed", confirm_date: toDateOnly(new Date()) }))).toBe(
      true,
    );
    expect(cashLegArrived(makeTrade({ status: "confirmed", confirm_date: "2999-01-01" }))).toBe(false);
  });
});

describe("isCashLeg", () => {
  it("按 product_code 判定（与后端 update_trade 的 CASH 守卫同口径）", () => {
    expect(isCashLeg(makeTrade({ product_code: "CASH" }))).toBe(true);
    expect(isCashLeg(makeTrade({ product_code: "F1" }))).toBe(false);
  });
});

// #525：门控漏掉产品维度时，赎回配对腿与现金转移主腿（均为 confirmed CASH sell）
// 会拿到「修改到账日期」按钮，而点开是死路弹窗（预填恒空 + 提交必 CASH_TRADE_FORBIDDEN）
describe("canEditArrivalDate（#525：只给基金卖出腿）", () => {
  it("已确认基金卖出腿 → 适用（窄表单入口不受影响）", () => {
    expect(
      canEditArrivalDate(makeTrade({ product_code: "F1", trade_type: "sell", transfer_group: "rebal_a" })),
    ).toBe(true);
  });

  it("赎回配对的 CASH 卖出腿（sub_ 组）→ 不适用", () => {
    expect(
      canEditArrivalDate(
        makeTrade({ product_code: "CASH", trade_type: "sell", transfer_group: "sub_42" }),
      ),
    ).toBe(false);
  });

  it("当天完成现金转移的 CASH 主腿（12 位 hex 组）→ 不适用", () => {
    expect(
      canEditArrivalDate(
        makeTrade({
          product_code: "CASH",
          trade_type: "sell",
          transfer_group: "0123456789ab",
        }),
      ),
    ).toBe(false);
  });

  it("调仓 CASH 腿与买入方向（含 CASH 扣款腿）→ 不适用", () => {
    expect(
      canEditArrivalDate(makeTrade({ product_code: "CASH", trade_type: "buy", transfer_group: "rebal_a" })),
    ).toBe(false);
    expect(canEditArrivalDate(makeTrade({ product_code: "F1", trade_type: "buy" }))).toBe(false);
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
