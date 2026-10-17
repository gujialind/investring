import type {
  CashFlowRow,
  CashFlowStatusFilter,
  CashFlowType,
} from "@/types/cash-flow";
import { CHART_COLORS } from "@/lib/colors";
import { formatCurrency, formatNumber } from "@/lib/utils";

/**
 * 现金流水展示层纯函数（issue #725）。只吃后端行、吐展示值，无副作用、可单测。
 *
 * 方向口径以**后端 `flow_type_label` 与带符号 `amount`** 为准（申购=注资入账 正、
 * 赎回=支付 负）；本文件不重算方向、不重造标签——mockup 的申赎方向是错的，见
 * docs/design/cash-flow-module.md。dot 色一律从带符号 amount + settled + is_override
 * 派生，与具体 flow_type 解耦，故修正方向后自动正确。
 */

/** flow_type 筛选下拉取值域（label 与后端 FLOW_TYPE_LABELS 逐字一致） */
export const CASH_FLOW_TYPE_OPTIONS: { value: CashFlowType; label: string }[] = [
  { value: "rebal_buy", label: "调仓 · 买入扣款" },
  { value: "rebal_sell", label: "调仓 · 卖出到账" },
  { value: "sub_buy", label: "申购 · 注资入账" },
  { value: "redeem", label: "赎回 · 支付" },
  { value: "dividend", label: "现金分红" },
  { value: "transfer", label: "平台间转移" },
  { value: "revalue", label: "现金重估" },
];

/** status 筛选下拉取值域（映射派生 settled 两值，不透出 raw trade.status） */
export const CASH_FLOW_STATUS_OPTIONS: { value: CashFlowStatusFilter; label: string }[] = [
  { value: "settled", label: "已生效" },
  { value: "in_transit", label: "在途" },
];

/** dot 色板：C1 靛蓝=流入 / C6 赭橙=流出 / C3 天蓝=在途 / C7 灰蓝=重估（非流量） */
export const CASH_FLOW_DOT_COLORS = {
  inflow: CHART_COLORS[0],
  outflow: CHART_COLORS[5],
  inTransit: CHART_COLORS[2],
  neutral: CHART_COLORS[6],
} as const;

/** Decimal 字符串 → number；空值/非法一律 0（余额缺位时不参与运算） */
export function toAmount(value: string | number | null | undefined): number {
  if (value === null || value === undefined || value === "") return 0;
  const n = typeof value === "number" ? value : parseFloat(value);
  return Number.isNaN(n) ? 0 : n;
}

/** (平台, 现金日期) 合并键——余额列与日期·平台组列共用同一 rowSpan 段 */
export function cashFlowGroupKey(row: CashFlowRow): string {
  return `${row.platform_code}|${row.cash_date}`;
}

/**
 * 余额列 / 日期·平台组列的 rowSpan 段（照 mockup data.js `balanceSpans`）：
 * 连续的同 (平台, 现金日期) 行合并成一格。返回与 rows 等长的数组，值 = 该格向下
 * 跨的行数，`-1` 表示被前面那格的 rowspan 覆盖、本行不再输出该单元格。
 * 段按**在屏行**算——筛掉邻居后自然退化为单格。排序保证同组连续（后端 cash_date
 * desc → platform asc → id asc），故只需线性扫连续段，无需全局分桶。
 */
export function balanceSpans(rows: CashFlowRow[]): number[] {
  const spans = new Array<number>(rows.length).fill(1);
  for (let i = 0; i < rows.length; i++) {
    if (spans[i] === -1) continue;
    const key = cashFlowGroupKey(rows[i]);
    let n = 1;
    while (i + n < rows.length && cashFlowGroupKey(rows[i + n]) === key) n++;
    spans[i] = n;
    for (let k = 1; k < n; k++) spans[i + k] = -1;
  }
  return spans;
}

/**
 * 「当日该平台另有 N 笔」的 N = 同 (平台, 现金日期) 解除类型筛选后的总笔数
 * （后端 `group_total_count`）− 当前在屏的同组行数（含本行）。
 * N=0 时调用方不渲染次行（桌面合并格 / 移动相邻卡片已把同组行摆明，次行冗余）。
 */
export function sameDayOthersCount(row: CashFlowRow, rows: CashFlowRow[]): number {
  const key = cashFlowGroupKey(row);
  const onScreen = rows.filter((r) => cashFlowGroupKey(r) === key).length;
  return Math.max(0, (row.group_total_count ?? 1) - onScreen);
}

/**
 * dot 色（从带符号 amount + settled + is_override 派生，与 flow_type 解耦）：
 * 重估（is_override）= 灰蓝非流量；未生效 = 天蓝在途；已生效按金额正负取靛蓝/赭橙。
 */
export function cashFlowDotColor(row: CashFlowRow): string {
  if (row.is_override) return CASH_FLOW_DOT_COLORS.neutral;
  if (!row.settled) return CASH_FLOW_DOT_COLORS.inTransit;
  return toAmount(row.amount) < 0
    ? CASH_FLOW_DOT_COLORS.outflow
    : CASH_FLOW_DOT_COLORS.inflow;
}

/**
 * 金额旁的语义注记（前端派生，非后端字段）：
 * 重估行是绝对值不是增量 → 「绝对值 · 非流水」；未生效行不进余额 → 「不计入余额」。
 * 已生效增量行无注记（返回 null）。
 */
export function amountNote(row: CashFlowRow): string | null {
  if (row.is_override) return "绝对值 · 非流水";
  if (!row.settled) return "不计入余额";
  return null;
}

/**
 * 详情弹窗「预计到账后余额」投影值（前端推算，非后端 calculate_available_cash 结果）：
 * 当前平台日终余额 + 本笔带符号金额。调用方须显式标注「前端推算」。
 *
 * **仅对「在途未入账」的 pending 转入腿返回非 null**，其余一律 null：
 * - 已生效行、重估（绝对值覆盖）行本就不该投影；
 * - **confirmed 但到账日在未来的行**（调仓卖出到账腿 A>C 窗口、未来 cash_pay_date 分红）：
 *   后端 `platform_end_of_day_balance = calculate_available_cash(as_of=cash_date)` 已把本笔
 *   计入（confirmed buy 按 confirm_date<=as_of 收口，而 as_of 恰为 cash_date），余额列本身
 *   即「到账后余额」，再 +本笔会**重复计**。故这些行不投影（余额列已表达）。
 *
 * pending 转入腿是唯一「余额不含本笔」的形态（calculate_available_cash 从不计 pending buy），
 * 其 `action.kind` 恒为 `"confirm"`（后端：trade_status=pending 且 flow_type=transfer），
 * 以此作为「余额不含本笔」的前端判据。这是 PR-1 余额契约与 mockup 简化余额模型的口径差，
 * 见 PR 待确认清单。
 */
export function projectedBalance(row: CashFlowRow): number | null {
  if (row.settled || row.is_override) return null;
  if (row.action.kind !== "confirm") return null;
  if (row.platform_end_of_day_balance === null || row.platform_end_of_day_balance === undefined) {
    return null;
  }
  return toAmount(row.platform_end_of_day_balance) + toAmount(row.amount);
}

/**
 * 展示层把后端 4 位小数的裸数字文本收敛为 2 位货币（不改后端契约）：
 * 重估行 `source_sub` = 「覆盖前计算值 92400.0000」→「覆盖前计算值 ¥92,400.00」，
 * 与 mockup 视觉基线一致。只匹配带小数点的数字串（日期用连字符、代码无小数点，均不误伤）。
 */
export function normalizeMoneyText(text: string | null | undefined): string {
  if (!text) return "";
  return text.replace(/-?\d+\.\d+/g, (m) => formatBalanceCurrency(m));
}

/**
 * 时点余额金额（可用现金 / 在途资金 / 平台日终余额）：符号在 ¥ 前、非负不补 +。
 * formatCurrency 会把负数渲染成「¥-1,234.00」（符号分居 ¥ 两侧，违 visual-spec §12），
 * 故余额一律走本函数得到「-¥1,234.00」；正值不带 + 是刻意的——余额是存量事实，
 * 不是流量增减（流量用 formatSignedCurrency 带 ± ）。
 */
export function formatBalanceCurrency(value: string | number | null | undefined): string {
  const n = toAmount(value);
  return `${n < 0 ? "-" : ""}${formatCurrency(Math.abs(n))}`;
}

/**
 * 对账块明细行的裸数字（不带 ¥、带 ± ）——与弹窗内合计/余额的 ¥ 形态分层，
 * 照 mockup `fmtNumber`（§12 概览大字例外同源：紧凑、不与表格内 ¥ 争层级）。
 */
export function formatBareSigned(value: string | number | null | undefined): string {
  const n = toAmount(value);
  const sign = n < 0 ? "-" : n > 0 ? "+" : "";
  return `${sign}${formatNumber(Math.abs(n), 2)}`;
}
