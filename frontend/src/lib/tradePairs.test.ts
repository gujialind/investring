import { describe, it, expect } from "vitest";
import {
  groupTradeRows,
  cashSubMeta,
  cashLegArrived,
  cashLegOrigin,
  cashOrphanLabel,
  cashRowLabel,
  counterpartDateText,
  counterpartSubLabel,
  isCashLeg,
  canEditArrivalDate,
  tradeCardDetailLine,
  type TradeRow,
} from "@/lib/tradePairs";
import { toDateOnly } from "@/lib/utils";
import type { Trade, TradePairedLeg } from "@/types/trade";

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

/** 对手腿派生快照（#719）：字段比 Trade 少，测试里与真实行不能混用同一个工厂 */
function makeLeg(over: Partial<TradePairedLeg>): TradePairedLeg {
  return {
    id: nextId++,
    product_code: "CASH",
    trade_type: "sell",
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
    // derived=false：两腿都是结果集里的真实行（#719 加字段，配对规则本身不变）
    const expected = [{ kind: "pair", main: fund, sub: cash, derived: false }];
    expect(groupTradeRows([fund, cash])).toEqual(expected);
    expect(groupTradeRows([cash, fund])).toEqual(expected);
  });

  it("双 CASH 腿（现金跨平台转移）结对：sell 主、buy 子", () => {
    const sell = makeTrade({ product_code: "CASH", trade_type: "sell", transfer_group: "0123456789ab" });
    const buy = makeTrade({ product_code: "CASH", trade_type: "buy", transfer_group: "0123456789ab" });
    expect(groupTradeRows([buy, sell])).toEqual([
      { kind: "pair", main: sell, sub: buy, derived: false },
    ]);
  });

  it("双 CASH 腿顺序颠倒（sell 在前）同样 sell 主、buy 子", () => {
    const sell = makeTrade({ product_code: "CASH", trade_type: "sell", transfer_group: "abcdef123456" });
    const buy = makeTrade({ product_code: "CASH", trade_type: "buy", transfer_group: "abcdef123456" });
    expect(groupTradeRows([sell, buy])).toEqual([
      { kind: "pair", main: sell, sub: buy, derived: false },
    ]);
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
    expect(rows[0]).toEqual({ kind: "pair", main: a, sub: aCash, derived: false });
    expect(rows[1]).toEqual({ kind: "single", trade: b });
  });
});

// #719「只补信息、不补行」：对手腿被筛选/分页挡在结果集外时，用后端只读快照合成派生子行
describe("groupTradeRows 规则 6（派生子行：真实优先、派生兜底）", () => {
  it("孤儿行带 paired_leg → 升级为 derived pair（子行取派生快照，不新增真实行）", () => {
    const cash = makeTrade({
      product_code: "CASH",
      trade_type: "sell",
      transfer_group: "rebal_1",
      paired_leg: makeLeg({ id: 901, product_code: "F1", trade_type: "buy" }),
    });
    const rows = groupTradeRows([cash]);
    expect(rows).toEqual([{ kind: "pair", main: cash, sub: cash.paired_leg, derived: true }]);
  });

  // #721 评审 S1：平台间转移被筛选/分页拆开后，屏幕上剩一条现金腿 + 一条**现金**对手腿。
  // 规范 §8 只给这种派生子行承诺了 `↳ 转出/转入 平台名`（无 ± 前缀）那一档，文案单点在
  // counterpartSubLabel 的现金分支——组行只负责把形态标成 derived=true，渲染端据此分流。
  it("双 CASH 转移组只剩一条腿 → derived=true，子行文案走 ↳ 转入/转出（非 ± 现金子行）", () => {
    const arrived = "2000-01-04";
    const sell = makeTrade({
      product_code: "CASH",
      trade_type: "sell",
      transfer_group: "0123456789ab",
      confirm_date: arrived,
      paired_leg: makeLeg({
        id: 911,
        product_code: "CASH",
        trade_type: "buy",
        platform_code: "MAYI",
        status: "confirmed",
        confirm_date: arrived,
      }),
    });
    const rows = groupTradeRows([sell]);
    expect(rows).toEqual([{ kind: "pair", main: sell, sub: sell.paired_leg, derived: true }]);
    const sub = (rows[0] as Extract<TradeRow, { kind: "pair" }>).sub;
    expect(counterpartSubLabel(sub, "蚂蚁基金")).toBe("↳ 转入 蚂蚁基金");
    expect(counterpartSubLabel(sub)).toBe("↳ 转入 MAYI");

    // 反向：只剩转入腿时，对手是转出腿（已 confirmed，资金已离开，不带待到账后缀）
    const buy = makeTrade({
      product_code: "CASH",
      trade_type: "buy",
      transfer_group: "0123456789ab",
      status: "pending",
      confirm_date: "2999-01-04",
      paired_leg: makeLeg({
        id: 912,
        product_code: "CASH",
        trade_type: "sell",
        platform_code: "HBZQ",
        confirm_date: arrived,
      }),
    });
    const rows2 = groupTradeRows([buy]);
    expect(rows2).toEqual([{ kind: "pair", main: buy, sub: buy.paired_leg, derived: true }]);
    const sub2 = (rows2[0] as Extract<TradeRow, { kind: "pair" }>).sub;
    expect(counterpartSubLabel(sub2, "华宝证券")).toBe("↳ 转出 华宝证券");
  });

  it("对手腿已在结果集内（真实行）→ 绝不重复派生", () => {
    const fund = makeTrade({ product_code: "F1", transfer_group: "rebal_2" });
    const cash = makeTrade({
      product_code: "CASH",
      trade_type: "sell",
      transfer_group: "rebal_2",
      // 后端两腿互指：真实行都在本页时派生字段必须被忽略
      paired_leg: makeLeg({ id: fund.id, product_code: "F1", trade_type: "buy" }),
    });
    fund.paired_leg = makeLeg({ id: cash.id, product_code: "CASH", trade_type: "sell" });
    expect(groupTradeRows([fund, cash])).toEqual([
      { kind: "pair", main: fund, sub: cash, derived: false },
    ]);
  });

  it("异常数据（同组两条非 CASH 腿互指）→ 两行 single，不重复渲两遍", () => {
    const f1 = makeTrade({ product_code: "F1", transfer_group: "rebal_ff" });
    const f2 = makeTrade({ product_code: "F2", trade_type: "sell", transfer_group: "rebal_ff" });
    f1.paired_leg = makeLeg({ id: f2.id, product_code: "F2", trade_type: "sell" });
    f2.paired_leg = makeLeg({ id: f1.id, product_code: "F1", trade_type: "buy" });
    expect(groupTradeRows([f1, f2])).toEqual([
      { kind: "single", trade: f1 },
      { kind: "single", trade: f2 },
    ]);
  });

  it("sub_ 申赎腿即便带 paired_leg 也不派生（对手方是 subscription 记录，刻意边界）", () => {
    const sub = makeTrade({
      product_code: "CASH",
      trade_type: "sell",
      transfer_group: "sub_42",
      paired_leg: makeLeg({ id: 902, product_code: "F1", trade_type: "buy" }),
    });
    expect(groupTradeRows([sub])).toEqual([{ kind: "single", trade: sub }]);
  });

  it("paired_leg 为 null（待确认卖出尚无现金腿）→ 保持 single", () => {
    const t = makeTrade({ transfer_group: "rebal_3", paired_leg: null });
    expect(groupTradeRows([t])).toEqual([{ kind: "single", trade: t }]);
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

  // 只看日期的旧口径会把这一档错标成「现金到账」，而 pending 腿不计入可用现金（[现金账本](../../../docs/reference/business-constraints.md#rule-cash)）
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

describe("cashLegOrigin", () => {
  it("按 transfer_group 编码分三类", () => {
    expect(cashLegOrigin(makeTrade({ transfer_group: "sub_1" }))).toBe("subscription");
    expect(cashLegOrigin(makeTrade({ transfer_group: "0123456789ab" }))).toBe("transfer");
    expect(cashLegOrigin(makeTrade({ transfer_group: "rebal_x" }))).toBe("rebalance");
  });

  it("缺组号与不匹配 12 位 hex 的组号都回落调仓", () => {
    expect(cashLegOrigin(makeTrade({ transfer_group: undefined }))).toBe("rebalance");
    // 11/13 位、含非 hex 字符都不是现金转移组
    expect(cashLegOrigin(makeTrade({ transfer_group: "0123456789a" }))).toBe("rebalance");
    expect(cashLegOrigin(makeTrade({ transfer_group: "0123456789abc" }))).toBe("rebalance");
    expect(cashLegOrigin(makeTrade({ transfer_group: "0123456789ag" }))).toBe("rebalance");
  });

  it("cashOrphanLabel 由本分类器派生，三档文案不变（#527）", () => {
    expect(cashOrphanLabel(makeTrade({ transfer_group: "sub_1" }))).toBe("现金 · 申赎确认");
    expect(cashOrphanLabel(makeTrade({ transfer_group: "0123456789ab" }))).toBe("现金 · 平台间转移");
    expect(cashOrphanLabel(makeTrade({ transfer_group: "rebal_x" }))).toBe("现金 · 调仓");
    expect(cashOrphanLabel(makeTrade({ transfer_group: undefined }))).toBe("现金 · 调仓");
  });
});

// #719：现金行在详情页交易卡与列表子行上要读得出「从哪来、到哪去」，文案单点在这三个函数
describe("cashRowLabel（现金腿自身的资金流向）", () => {
  it("调仓腿按本行方向给扣款/到账，不查对手腿", () => {
    // CASH sell = 买入的扣款腿；CASH buy = 卖出的到账腿（attach_paired_cash_leg 方向恒相反）
    expect(
      cashRowLabel(makeTrade({ product_code: "CASH", trade_type: "sell", transfer_group: "rebal_a" }))
    ).toBe("现金扣款");
    expect(
      cashRowLabel(
        makeTrade({
          product_code: "CASH",
          trade_type: "buy",
          transfer_group: "rebal_a",
          status: "confirmed",
          // 日期一律远离系统时钟（过去/未来各取定值），断言不随跑测日期漂移
          confirm_date: "2000-01-01",
        })
      )
    ).toBe("现金到账");
  });

  // #493 口径在卡片上同样成立：到账腿可携带未来 confirm_date / 到期未确认，都不能标成已到账
  it("到账腿未实际到账（未来日或仍 pending）→ 现金待到账", () => {
    expect(
      cashRowLabel(
        makeTrade({
          product_code: "CASH",
          trade_type: "buy",
          transfer_group: "rebal_a",
          status: "confirmed",
          confirm_date: "2999-01-01",
        })
      )
    ).toBe("现金待到账");
    expect(
      cashRowLabel(
        makeTrade({
          product_code: "CASH",
          trade_type: "buy",
          transfer_group: "rebal_a",
          status: "pending",
          confirm_date: "2000-01-01",
        })
      )
    ).toBe("现金待到账");
  });

  it("转移腿按方向给转出/转入；申赎腿保持来源标注（主体在申赎页）", () => {
    expect(
      cashRowLabel(
        makeTrade({ product_code: "CASH", trade_type: "sell", transfer_group: "0123456789ab" })
      )
    ).toBe("现金转出");
    expect(
      cashRowLabel(
        makeTrade({
          product_code: "CASH",
          trade_type: "buy",
          transfer_group: "0123456789ab",
          status: "confirmed",
          confirm_date: "2000-01-01",
        })
      )
    ).toBe("现金转入");
    expect(cashRowLabel(makeTrade({ product_code: "CASH", transfer_group: "sub_9" }))).toBe(
      "现金 · 申赎确认"
    );
  });

  // #721 评审 S3：跨天转移的转入腿是 pending + 未来生效日（cash_transfer_service 只把
  // 转出腿建成 confirmed）。列表子行按 #493 显示「现金待到账」，卡片恒写「现金转入」
  // 会把同一笔在途钱在详情页说成已到账——两栈口径必须同一判据。
  it("转移转入腿未到账（pending 或未来日）→ 现金待到账，与列表同口径", () => {
    expect(
      cashRowLabel(
        makeTrade({
          product_code: "CASH",
          trade_type: "buy",
          transfer_group: "0123456789ab",
          status: "pending",
          confirm_date: "2000-01-01",
        })
      )
    ).toBe("现金待到账");
    expect(
      cashRowLabel(
        makeTrade({
          product_code: "CASH",
          trade_type: "buy",
          transfer_group: "0123456789ab",
          status: "confirmed",
          confirm_date: "2999-01-01",
        })
      )
    ).toBe("现金待到账");
    // 转出腿不判到账：扣款腿创建即 confirmed、转移日就是资金离开日（无「待转出」态）
    expect(
      cashRowLabel(
        makeTrade({
          product_code: "CASH",
          trade_type: "sell",
          transfer_group: "0123456789ab",
          status: "confirmed",
          confirm_date: "2999-01-01",
        })
      )
    ).toBe("现金转出");
  });

  it("给了平台名才追加「 · 平台」，不给就不带", () => {
    const t = makeTrade({ product_code: "CASH", trade_type: "sell", transfer_group: "rebal_a" });
    expect(cashRowLabel(t, "华宝证券")).toBe("现金扣款 · 华宝证券");
    expect(cashRowLabel(t, undefined)).toBe("现金扣款");
    expect(cashRowLabel(t, "")).toBe("现金扣款");
  });
});

describe("counterpartSubLabel / counterpartDateText（对手腿一行文字）", () => {
  it("对手是基金腿 → `↳ 方向 产品名（对手状态）`，状态取对手自身而不是主行", () => {
    expect(
      counterpartSubLabel(makeLeg({ product_code: "510300.SH", market: "CN_EXCHANGE", product_name: "沪深300ETF", trade_type: "buy", status: "pending" }))
    ).toBe("↳ 买入 沪深300ETF（待确认）");
    // 名称缺失回退显示码（后端未派生名称时不渲裸机器码之外的假信息）
    expect(
      counterpartSubLabel(makeLeg({ product_code: "510300.SH", market: "CN_EXCHANGE", display_code: "510300", trade_type: "sell" }))
    ).toBe("↳ 卖出 510300（已确认）");
  });

  it("对手是另一条现金腿（平台间转移）→ 只说平台，优先名字、无名字回退平台码", () => {
    // 已到账（confirmed 且生效日不在未来）不带状态后缀：主行状态列已说了「已确认」
    const leg = makeLeg({
      product_code: "CASH",
      platform_code: "MAYI",
      trade_type: "buy",
      confirm_date: "2000-01-01",
    });
    expect(counterpartSubLabel(leg, "蚂蚁基金")).toBe("↳ 转入 蚂蚁基金");
    expect(counterpartSubLabel(leg)).toBe("↳ 转入 MAYI");
    expect(
      counterpartSubLabel(
        makeLeg({
          product_code: "CASH",
          platform_code: null,
          trade_type: "buy",
          confirm_date: "2000-01-01",
        })
      )
    ).toBeNull();
  });

  // #721 评审 S3：转入腿可 pending / 生效日在未来，而它挂在已 confirmed 的转出腿下面，
  // 状态列讲的是转出腿——只写「↳ 转入 平台B」会把在途的钱读成已到账（与 cashRowLabel 同判据）
  it("对手转入腿未到账 → 带（现金待到账）；转出腿不带（资金已离开）", () => {
    const cash = { product_code: "CASH", platform_code: "MAYI" };
    expect(
      counterpartSubLabel(
        makeLeg({ ...cash, trade_type: "buy", status: "pending", confirm_date: "2000-01-01" }),
        "蚂蚁基金",
      )
    ).toBe("↳ 转入 蚂蚁基金（现金待到账）");
    expect(
      counterpartSubLabel(
        makeLeg({ ...cash, trade_type: "buy", status: "confirmed", confirm_date: "2999-01-01" }),
        "蚂蚁基金",
      )
    ).toBe("↳ 转入 蚂蚁基金（现金待到账）");
    expect(
      counterpartSubLabel(
        makeLeg({ ...cash, trade_type: "sell", status: "confirmed", confirm_date: "2999-01-01" }),
        "蚂蚁基金",
      )
    ).toBe("↳ 转出 蚂蚁基金");
  });

  it("详情页卡片：跨天转移的转入腿未到账 → 现金待到账 + 对手转出腿", () => {
    const incoming = makeTrade({
      product_code: "CASH",
      trade_type: "buy",
      transfer_group: "0123456789ab",
      status: "pending",
      confirm_date: "2999-01-06",
      shares: null as unknown as number,
      paired_leg: makeLeg({
        product_code: "CASH",
        platform_code: "HBZQ",
        trade_type: "sell",
        confirm_date: "2000-01-06",
      }),
    });
    // 卡片主标签（本行方向 + 到账口径）与副行（对手腿）读出的都是「还没到」
    expect(cashRowLabel(incoming, "蚂蚁基金")).toBe("现金待到账 · 蚂蚁基金");
    expect(tradeCardDetailLine(incoming, new Map([["HBZQ", "华宝证券"]]))).toBe(
      "↳ 转出 华宝证券 · 2999-01-06"
    );
  });

  it("生效日：卖出腿标「到账」，买入腿不加前缀，无生效日回空串", () => {
    expect(counterpartDateText(makeLeg({ trade_type: "sell", confirm_date: "2026-10-20" }))).toBe(
      "到账 2026-10-20"
    );
    expect(counterpartDateText(makeLeg({ trade_type: "buy", confirm_date: "2026-10-14" }))).toBe(
      "2026-10-14"
    );
    expect(counterpartDateText(makeLeg({ trade_type: "buy", confirm_date: null }))).toBe("");
  });
});

describe("tradeCardDetailLine（详情页交易卡副行）", () => {
  it("基金行：份额 @ 净值 · 交易日（与改版前文案一致，零回归）", () => {
    expect(tradeCardDetailLine(makeTrade({ shares: 1000, price: 2 }))).toBe("1,000.00 份 @ 2.0000 · 2026-08-01");
  });

  // 原缺陷：判据写成 `shares !== undefined`，对现金腿的 null 为真 → 渲成 `-- @ 1.0000`
  it("无份额的基金行（shares 为 null）只回日期，不再出 `-- @ 1.0000`", () => {
    expect(tradeCardDetailLine(makeTrade({ shares: null as unknown as number, price: 1 }))).toBe(
      "2026-08-01"
    );
  });

  it("现金行：带出对手腿与本平台生效日，对手平台名走映射", () => {
    const cash = makeTrade({
      product_code: "CASH",
      trade_type: "buy",
      transfer_group: "0123456789ab",
      shares: null as unknown as number,
      price: 1,
      confirm_date: "2026-10-06",
      paired_leg: makeLeg({ product_code: "CASH", platform_code: "MAYI", trade_type: "sell" }),
    });
    expect(tradeCardDetailLine(cash, new Map([["MAYI", "蚂蚁基金"]]))).toBe(
      "↳ 转出 蚂蚁基金 · 2026-10-06"
    );
    // 无映射时回退平台码，不静默丢掉这行信息
    expect(tradeCardDetailLine(cash)).toBe("↳ 转出 MAYI · 2026-10-06");
  });

  it("现金行无对手腿（申赎腿 / 未建出现金腿）→ 只回日期，不编造文案", () => {
    expect(
      tradeCardDetailLine(
        makeTrade({ product_code: "CASH", transfer_group: "sub_9", shares: null as unknown as number, confirm_date: "2026-09-30" })
      )
    ).toBe("2026-09-30");
  });
});
