import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * 合并 Tailwind CSS 类名
 * 结合 clsx 和 tailwind-merge 实现条件类名合并
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// ==================== 日期格式化 ====================

/**
 * 将 Date 对象转换为本地日期字符串 (yyyy-MM-dd)
 * 使用本地时区的年/月/日，避免 toISOString() 的 UTC 转换导致日期偏移
 * （如 UTC+8 下 7月3日 00:00 经 toISOString 会变成 7月2日）
 */
export function toDateOnly(date: Date | undefined | null): string {
  if (!date || isNaN(date.getTime())) return "";
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/**
 * 将日期字符串 (yyyy-MM-dd) 解析为本地 Date 对象
 * 添加 T00:00:00 后缀使其按本地时间解析，避免 new Date("yyyy-MM-dd") 按 UTC 解析导致时区偏移
 */
export function parseDateOnly(dateStr: string): Date | undefined {
  if (!dateStr) return undefined;
  const d = new Date(dateStr + "T00:00:00");
  return isNaN(d.getTime()) ? undefined : d;
}

/**
 * 格式化日期为 YYYY-MM-DD
 * date-only 字符串（yyyy-MM-dd）按本地零点解析（与 parseDateOnly 同口径）：
 * new Date("yyyy-MM-dd") 按 UTC 解析，UTC 负偏移时区取本地年月日会回退一天
 */
export function formatDate(date: string | Date | number): string {
  const d =
    typeof date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(date)
      ? parseDateOnly(date)
      : new Date(date);
  if (!d || isNaN(d.getTime())) return "--";
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

// ==================== 金额格式化 ====================

/**
 * 格式化数字（千分位 + 小数位）
 * @param num 数字
 * @param decimals 小数位数（默认 2）
 * @param fallback 无效值时的回显（默认 "--"）
 */
export function formatNumber(
  num: number | string | undefined | null,
  decimals: number = 2,
  fallback: string = "--"
): string {
  if (num === undefined || num === null || num === "" || Number.isNaN(Number(num))) {
    return fallback;
  }
  const n = typeof num === "string" ? parseFloat(num) : num;
  return n.toLocaleString("zh-CN", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });
}

/**
 * 格式化金额（人民币，带 ¥ 符号）
 */
export function formatCurrency(
  num: number | string | undefined | null,
  decimals: number = 2,
  fallback: string = "--"
): string {
  if (num === undefined || num === null || num === "" || Number.isNaN(Number(num))) {
    return fallback;
  }
  return `¥${formatNumber(num, decimals, fallback)}`;
}

/**
 * 格式化份额（固定 2 位小数，对齐后端 Numeric(15,2) 与场外基金行业惯例）。
 * 份额不带货币符号，请勿用 formatCurrency 代替。
 */
export function formatShares(
  num: number | string | undefined | null,
  fallback: string = "--"
): string {
  return formatNumber(num, 2, fallback);
}

/**
 * 格式化份额并带「份」单位（visual-spec §12，issue #249）。
 * 符号在数字内、单位在外（-1,000.00 份）；空值走 fallback，消除各处散写的
 * `${formatShares(x)} 份` 模板串。
 */
export function formatSharesUnit(
  num: number | string | undefined | null,
  fallback: string = "--"
): string {
  if (num === undefined || num === null || num === "" || Number.isNaN(Number(num))) {
    return fallback;
  }
  return `${formatShares(num)} 份`;
}

/**
 * 格式化净值/价格（固定 4 位小数，对齐后端 Numeric(10,4)）。
 * 净值不是货币量，不带 ¥ 符号。
 */
export function formatNav(
  num: number | string | undefined | null,
  fallback: string = "--"
): string {
  return formatNumber(num, 4, fallback);
}

/**
 * 格式化 4 位小数金额（对齐后端 Numeric(15,4)，用于需与后端/CLI 精确对账的场景）
 *
 * 当前无调用点——保留是刻意的：visual-spec §5「精确对账金额」点名的口径载体，
 * 删除会让规范表指向不存在的 helper。预期调用方 = 按 §5 落地的对账场景展示点。
 */
export function formatAmount4(
  num: number | string | undefined | null,
  fallback: string = "--"
): string {
  return formatCurrency(num, 4, fallback);
}

/**
 * 获取数字显示的CSS类名（右对齐 + 等宽字体）
 * 用于金融数字展示，确保对齐和可读性
 */
export function getNumberCellClass(): string {
  return "text-right font-mono tabular-nums";
}

/**
 * 格式化金额（简化显示，大于万显示为 X.XX 万）
 *
 * 当前无调用点——保留是刻意的：visual-spec §5「金额（概览大字）」/ §12 点名的紧凑
 * 格式口径载体（表格内禁用，见 §12 边界）。预期调用方 = 概览大字类展示点。
 */
export function formatCompactCurrency(
  num: number | string | undefined | null,
  fallback: string = "--"
): string {
  if (num === undefined || num === null || num === "" || Number.isNaN(Number(num))) {
    return fallback;
  }
  const n = typeof num === "string" ? parseFloat(num) : num;
  if (Math.abs(n) >= 100000000) {
    return `¥${(n / 100000000).toFixed(2)} 亿`;
  }
  if (Math.abs(n) >= 10000) {
    return `¥${(n / 10000).toFixed(2)} 万`;
  }
  return formatCurrency(n, 2, fallback);
}

/**
 * 格式化百分比
 * @param num 小数形式（如 0.0523 表示 5.23%）
 * @param decimals 小数位数（默认 2）
 * @param showSign 是否显示正负号（默认 true）
 *
 * 当前无调用点——保留是刻意的：visual-spec §5「百分比/收益率」/ §12 的 +/- 符号惯例
 * 点名口径（注意 §5 明确行级占比不走本函数，见 largestRemainderPercents）。
 * 预期调用方 = 按 §5 落地的百分比展示点（现有展示多直用 formatReturnRate）。
 */
export function formatPercent(
  num: number | string | undefined | null,
  decimals: number = 2,
  showSign: boolean = true,
  fallback: string = "--"
): string {
  if (num === undefined || num === null || num === "" || Number.isNaN(Number(num))) {
    return fallback;
  }
  const n = typeof num === "string" ? parseFloat(num) : num;
  const percent = n * 100;
  const sign = showSign && percent > 0 ? "+" : "";
  return `${sign}${percent.toFixed(decimals)}%`;
}

/**
 * 格式化收益率（直接传入百分比数值，如 5.23 表示 5.23%）
 */
export function formatReturnRate(
  num: number | string | undefined | null,
  decimals: number = 2,
  showSign: boolean = true,
  fallback: string = "--"
): string {
  if (num === undefined || num === null || num === "" || Number.isNaN(Number(num))) {
    return fallback;
  }
  const n = typeof num === "string" ? parseFloat(num) : num;
  const sign = showSign && n > 0 ? "+" : "";
  return `${sign}${n.toFixed(decimals)}%`;
}

/**
 * 最大余数法占比（issue #99）：将权重数组转换为加总恒为 100.0% 的百分比。
 *
 * 精度 1 位小数（千分位整数分配）：先取 floor，剩余点数按小数余数降序补给前 N 项。
 * 饼图图例与持仓分区头共用，禁止各处自行 toFixed(1)（会产生 ±0.1%×n 漂移）。
 *
 * @param weights 权重数组（Σ=1，允许 Σ≠1 时按 Σ 归一化）
 * @returns 百分比数组（如 [62.4, 20.3, 5.1, 12.2]，加总 100.0）
 */
export function largestRemainderPercents(weights: number[]): number[] {
  const total = weights.reduce((s, w) => s + w, 0);
  if (weights.length === 0 || total <= 0) return weights.map(() => 0);
  const raws = weights.map((w) => (w / total) * 1000);
  const floors = raws.map((r) => Math.floor(r));
  let remainder = 1000 - floors.reduce((s, f) => s + f, 0);
  // 按小数余数降序，前 remainder 项 +1（余数相同按原顺序，保证稳定性）
  const order = raws
    .map((r, i) => ({ i, frac: r - Math.floor(r) }))
    .sort((a, b) => b.frac - a.frac || a.i - b.i);
  for (const { i } of order) {
    if (remainder <= 0) break;
    floors[i] += 1;
    remainder -= 1;
  }
  return floors.map((f) => f / 10);
}

// ==================== 收益率颜色判断 ====================

/**
 * 根据收益率/涨跌值获取对应的颜色类名
 * 中国市场惯例：红涨绿跌（issue #127 语义 token：gain 朱红 / loss 松绿）
 * text-gain/text-loss 只允许由本函数（或显式涨跌语义）输出，禁止用于状态色
 * @param value 数值
 * @returns Tailwind CSS 颜色类名
 */
export function getReturnColorClass(value: number | string | undefined | null): string {
  if (value === undefined || value === null || value === "" || Number.isNaN(Number(value))) {
    return "text-muted-foreground";
  }
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (n > 0) return "text-gain";   // 涨：朱红
  if (n < 0) return "text-loss";   // 跌：松绿
  return "text-muted-foreground";
}

/**
 * 根据收益率/涨跌值获取对应背景色类名
 * 中国市场惯例：红涨绿跌（issue #127 语义 token soft 浅底）
 *
 * 当前无调用点——保留是刻意的：visual-spec §2/§17 点名「涨跌色只允许由
 * getReturnColorClass / getReturnBgClass 输出」，它是涨跌背景色的唯一合规出口。
 * 预期调用方 = 需要浅底涨跌色的展示点（现网暂无）。
 */
export function getReturnBgClass(value: number | string | undefined | null): string {
  if (value === undefined || value === null || value === "" || Number.isNaN(Number(value))) {
    return "bg-muted";
  }
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (n > 0) return "bg-gain-soft";   // 涨：浅朱底
  if (n < 0) return "bg-loss-soft";   // 跌：浅松底
  return "bg-muted";
}

// ==================== 状态徽标 ====================

/** Badge 语义 variant（与 badge.tsx 对齐） */
export type StatusBadgeVariant = "success" | "warning" | "destructive" | "neutral";

/**
 * 业务状态 → Badge variant 单一映射（issue #127，visual-spec §1.3）：
 * 完成/活跃 → success（靛蓝）；待定/在途/进行中 → warning（赭珀）；
 * 失败/异常 → destructive（绛红）；取消/关闭/草稿/停用 → neutral（灰）。
 * 状态色永远不许用 gain/loss token（涨跌专属数值场景）。
 */
const STATUS_BADGE_VARIANTS: Record<string, StatusBadgeVariant> = {
  confirmed: "success",
  active: "success",
  success: "success",
  passed: "success",
  enabled: "success",
  pending: "warning",
  running: "warning",
  partial_success: "warning",
  in_transit: "warning",
  failed: "destructive",
  error: "destructive",
  cancelled: "neutral",
  closed: "neutral",
  draft: "neutral",
  disabled: "neutral",
};

export function getStatusBadgeVariant(status: string | undefined | null): StatusBadgeVariant {
  return (status && STATUS_BADGE_VARIANTS[status]) || "neutral";
}

/**
 * 获取带符号的收益率字符串（自动着色）
 * 用于需要同时显示符号和颜色的场景
 */
export function getSignedReturn(
  value: number | string | undefined | null,
  decimals: number = 2
): { text: string; colorClass: string } {
  if (value === undefined || value === null || value === "" || Number.isNaN(Number(value))) {
    return { text: "--", colorClass: "text-muted-foreground" };
  }
  const n = typeof value === "string" ? parseFloat(value) : value;
  const sign = n > 0 ? "+" : "";
  return {
    text: `${sign}${n.toFixed(decimals)}%`,
    colorClass: getReturnColorClass(n),
  };
}

// ==================== 市场/产品类型映射 ====================

const MARKET_NAME_MAP: Record<string, string> = {
  CN_EXCHANGE: "A股场内",
  CN_OTC: "内地场外",
  HK_MUTUAL: "香港互认",
};

/**
 * 格式化市场名称为中文
 * @param market 市场代码（如 CN_EXCHANGE）
 * @returns 中文市场名称，未知市场返回 "--"
 */
export function formatMarketName(market: string | undefined | null): string {
  if (!market) return "--";
  return MARKET_NAME_MAP[market] || market;
}

/**
 * 格式化产品展示名（#342）：`名称（代码）` 双信息；名称缺失回退裸代码，代码缺失 "--"
 */
export function formatProductName(
  productName: string | undefined | null,
  productCode: string | undefined | null
): string {
  if (!productCode) return "--";
  return productName ? `${productName}（${productCode}）` : productCode;
}
