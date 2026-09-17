import type { Trade } from "@/types/trade";
import { toDateOnly } from "@/lib/utils";

/**
 * 调仓列表结对视图行（#126 决策⑧）：
 * pair = 主行（基金腿 / 现金转移 sell 腿）+ 子行（配对现金腿 / 转移 buy 腿）；
 * single = 普通单行，或配对腿被筛选/分页排除后的孤儿回退。
 */
export type TradeRow =
  | { kind: "pair"; main: Trade; sub: Trade }
  | { kind: "single"; trade: Trade };

const CASH_CODE = "CASH";

/**
 * 现金行判定（根 `AGENTS.md` §2.5：一律按 `product_code`，不看产品分类字符串）。
 * 与后端 `update_trade` 的 CASH 守卫同口径（`trade.product_code == "CASH"`）。
 */
export function isCashLeg(trade: Trade): boolean {
  return trade.product_code === CASH_CODE;
}

/**
 * 已确认卖出行的「修改到账日期」窄入口是否适用（#525）：只给**基金**卖出腿。
 *
 * CASH 卖出腿（赎回配对 `sub_` 腿、当天完成的现金转移主腿）不是窄例外的设计对象：
 * ① 后端 PUT 对 CASH 腿的非 notes 字段一律 `CASH_TRADE_FORBIDDEN`；② 读侧派生的
 * `cash_confirm_date` 只填在基金腿上（CASH 腿自身恒 null）→ 弹窗预填恒空、保存钮
 * 恒禁用，而手选日期提交必被 ① 拒绝（死路入口）。这类行回到「先取消确认」分支。
 */
export function canEditArrivalDate(trade: Trade): boolean {
  return !isCashLeg(trade) && trade.trade_type === "sell";
}

/**
 * trades → 结对行。顺序敏感：分组与输出均保持传入顺序（= 后端
 * trade_date DESC, transfer_group, id DESC 排序序，决策⑪保证同组相邻）。
 *
 * 规则（边界即契约，异常形态显式回落 single、不静默错渲成现金子行，#507）：
 * 1. 按 transfer_group 分组；
 * 2. `sub_` 前缀组（申赎配对现金腿）→ 恒 single，主体在申赎页；
 * 3. 组内恰 2 条且恰 1 条 CASH → pair（非 CASH 主、现金子）；
 * 4. 组内恰 2 条且均 CASH → pair（sell 主、buy 子；现金跨平台转移）；
 * 5. 其余（孤儿单腿、异常多条、恰 2 条但两腿均非 CASH、双 CASH 而缺 sell 或 buy）
 *    → 全部 single 回退，不错行不空白。
 *
 * 规则 3/4 之外的 2 腿形态不可由后端数据到达（rebal_ 恒「基金腿 + CASH 腿」、
 * 跨平台转移恒「CASH sell + CASH buy」、sub_ 恒单腿），兜底只为异常数据不产生错行。
 */
export function groupTradeRows(trades: Trade[]): TradeRow[] {
  const groups = new Map<string, Trade[]>();
  for (const t of trades) {
    // transfer_group 后端 NOT NULL；前端类型可选，缺省时按 id 自成一组走 single 回退
    const key = t.transfer_group ?? `__none_${t.id}`;
    const g = groups.get(key);
    if (g) g.push(t);
    else groups.set(key, [t]);
  }

  const rows: TradeRow[] = [];
  for (const [key, legs] of groups) {
    if (key.startsWith("sub_")) {
      legs.forEach((t) => rows.push({ kind: "single", trade: t }));
      continue;
    }
    if (legs.length === 2) {
      const [a, b] = legs;
      const aIsCash = isCashLeg(a);
      const bIsCash = isCashLeg(b);
      // 恰一条 CASH 才成对；两腿均非 CASH（异常数据）不满足，落规则 5
      if (aIsCash !== bIsCash) {
        rows.push({ kind: "pair", main: aIsCash ? b : a, sub: aIsCash ? a : b });
        continue;
      }
      if (aIsCash && bIsCash) {
        const sell = a.trade_type === "sell" ? a : b.trade_type === "sell" ? b : null;
        const buy = sell === a ? b : sell === b ? a : null;
        // 缺 sell（均 buy）或另一腿非 buy（均 sell）均属异常数据，落规则 5
        if (sell && buy && buy.trade_type === "buy") {
          rows.push({ kind: "pair", main: sell, sub: buy });
          continue;
        }
      }
    }
    legs.forEach((t) => rows.push({ kind: "single", trade: t }));
  }
  return rows;
}

/**
 * 配对现金腿是否**已到账**（#493 评审加固；现金账本口径见根 `AGENTS.md` §2.5）：
 * 须 **confirmed 且生效日不在未来**——只看日期会把「已到期但尚未确认」的腿
 * （跨天现金转移的转入腿、卖出到账腿）显示成已到账，而 pending 腿**不计入可用现金**。
 * 无生效日（尚未生效的 pending 腿）按未到账处理；cancelled 腿一律未到账。
 *
 * 买入方向刻意**不消费本判据**（`cashSubMeta` 直接丢弃 `arrived`）：扣款腿创建即
 * confirmed、现金日 = 下单日 T，与下单同日完成。未来 T 的预约/补录虽可能存在
 * （`create_trade` 只校验交易日与晚于最新快照日，不禁止未来日），但「扣款已记账」
 * 是既成事实，与卖出「份额已扣、资金未到账」的在途语义不同源——故买入恒显示
 * 「现金扣款」，不设「现金待扣款」态。
 */
export function cashLegArrived(sub: Trade, today: string = toDateOnly(new Date())): boolean {
  if (!sub.confirm_date) return false;
  return sub.status === "confirmed" && sub.confirm_date <= today;
}

/**
 * 现金子行派生数据（规范 §8）：主行为买入 → 现金扣款（-）；主行为卖出 → 现金到账（+）。
 * 符号为语义修饰，展示层手工前缀，不回写数值、不走涨跌色 token。
 *
 * `arrived=false`（#493）：配对现金腿尚未实际到账——判据见 `cashLegArrived`
 * （**confirmed 且日期不在未来**，不是只看日期：卖出到账腿可携带未来
 * `confirm_date`，跨天转移的转入腿到期未确认时仍 pending）。主行状态讲的是基金腿，
 * 故在此显式区分——**未来到账的 confirmed 现金、以及已到期仍 pending 的现金，
 * 都不得标成已经到账**。
 * 缺省 true = 沿用旧行为（买入扣款腿创建即 confirmed、现金日 = 下单日 T）。
 * 买入侧的 `arrived` 被刻意忽略，依据见 `cashLegArrived`。
 */
export function cashSubMeta(
  main: Trade,
  options?: { arrived?: boolean }
): { label: "现金扣款" | "现金到账" | "现金待到账"; sign: "-" | "+" } {
  if (main.trade_type === "buy") return { label: "现金扣款", sign: "-" };
  return options?.arrived === false
    ? { label: "现金待到账", sign: "+" }
    : { label: "现金到账", sign: "+" };
}

/**
 * CASH 孤儿单行的来源标注（§5.8 简化口径：由 transfer_group 前缀推导，不查 subscription、不新增接口）：
 * `sub_` 前缀 → 申赎确认；12 位 hex（现金转移组）被拆散的单腿 → 平台间转移；其余 → 调仓。
 */
export function cashOrphanLabel(trade: Trade): string {
  const g = trade.transfer_group ?? "";
  if (g.startsWith("sub_")) return "现金 · 申赎确认";
  if (/^[0-9a-f]{12}$/.test(g)) return "现金 · 平台间转移";
  return "现金 · 调仓";
}
