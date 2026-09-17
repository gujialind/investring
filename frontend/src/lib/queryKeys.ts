import type { TradeConfirmParams } from "@/types/trade";

/**
 * react-query queryKey 统一工厂。
 *
 * 背景（审查报告 P1-17 / P2-7）：页面内联 mutation 手写 key 与 hooks 内的 key
 * 结构不一致（如 ["trades", code] vs ["trades", "list", params]），导致
 * invalidateQueries 失配、列表不刷新。所有新代码必须从此处取 key；
 * 存量 hooks 的字面量 key 与此处保持值相等，可渐进迁移。
 *
 * 约定：[域(复数小写/kebab-case), 子资源/操作, ...参数]
 */
/**
 * 交易确认预览的有效业务选项（#493）：从 `types/trade.ts::TradeConfirmParams`
 * 的 preview 子集派生（排除 confirm 端独占的 `sync_nav`），字段集与类型只有一份
 * 事实来源——新增预览输入时不会漏进 `previewWith` 的缓存键（漏了就是拿过期预览
 * 当确认值，即本文件约定块要防的事）。
 * 字段为 undefined 表示「交给后端取缺省值」，与显式传值不同 key。
 */
export type TradePreviewOptions = Pick<
  TradeConfirmParams,
  "confirm_date" | "price" | "cash_confirm_date" | "cash_platform_code"
>;

/**
 * 把「用户显式选择」归一为「是否要真的传参」（#493 约定⑤ → `previewWith` 分键的唯一性）：
 * 与**后端本次会用的缺省**同值时返回 undefined（= 不传、落 null 槽位），否则原样返回。
 *
 * 两个边界都是刻意的：
 * - 缺省未知（`backendDefault === undefined`，如 preview 尚未回来）**不归一**——宁可多一个
 *   缓存键，也不把用户的选择误当归一掉（少了参数就可能让后端取到另一个有效值）；
 * - 用户没选（`chosen === undefined`）恒为不传，与缺省无关。
 */
export function normalizePreviewOption<T>(
  chosen: T | undefined,
  backendDefault: T | undefined
): T | undefined {
  return chosen !== undefined && chosen !== backendDefault ? chosen : undefined;
}

export const queryKeys = {
  investors: {
    root: ["investors"] as const,
    list: (params?: unknown) => ["investors", "list", params] as const,
    detail: (code: string) => ["investors", code] as const,
  },
  portfolios: {
    root: ["portfolios"] as const,
    list: (params?: unknown) => ["portfolios", "list", params] as const,
    detail: (code: string) => ["portfolios", code] as const,
  },
  positions: {
    root: ["positions"] as const,
    byPortfolio: (portfolioCode: string) => ["positions", portfolioCode] as const,
    list: (portfolioCode: string, params?: unknown) =>
      ["positions", portfolioCode, "list", params] as const,
  },
  trades: {
    root: ["trades"] as const,
    list: () => ["trades", "list"] as const,
    detail: (id: number) => ["trades", id] as const,
    preview: (id: number) => ["trades", id, "preview"] as const,
    /**
     * 确认预览（#248）按**有效业务选项**分键（#493 §3.4.3）：确认日/价格/到账日/
     * 到账平台任一变化即换 key → 触发重新预览，绝不拿过期预览当确认值。
     * 调用方按「显式值 + 缺省下沉后端」整理 options，避免同一有效选项产出两个 key。
     */
    previewWith: (id: number, options?: TradePreviewOptions) =>
      [
        "trades",
        id,
        "preview",
        options?.confirm_date ?? null,
        options?.price ?? null,
        options?.cash_confirm_date ?? null,
        options?.cash_platform_code ?? null,
      ] as const,
  },
  subscriptions: {
    root: ["subscriptions"] as const,
    list: () => ["subscriptions", "list"] as const,
    detail: (id: number) => ["subscriptions", id] as const,
    preview: (id: number) => ["subscriptions", id, "preview"] as const,
  },
  products: {
    root: ["products"] as const,
    list: (params?: unknown) => ["products", "list", params] as const,
    detail: (code: string, market?: string) => ["products", code, market] as const,
    prices: (code?: string, market?: string) =>
      ["products", "prices", code, market] as const,
  },
  platforms: {
    root: ["platforms"] as const,
    list: (params?: unknown) => ["platforms", "list", params] as const,
    detail: (code: string) => ["platforms", code] as const,
  },
  snapshots: {
    root: ["snapshots"] as const,
    status: (portfolioCode: string) => ["snapshots", "status", portfolioCode] as const,
  },
  shareChangeEvents: {
    root: ["share-change-events"] as const,
    byPortfolio: (portfolioCode: string) => ["share-change-events", portfolioCode] as const,
    list: (portfolioCode: string, params?: unknown) =>
      ["share-change-events", portfolioCode, params] as const,
    // 确认预览（#424，同 trades/subscriptions 口径）
    preview: (id: number) => ["share-change-events", id, "preview"] as const,
  },
  cashTransfers: {
    root: ["cash-transfers"] as const,
    list: (portfolioCode: string, params?: unknown) =>
      ["cash-transfers", "list", portfolioCode, params] as const,
  },
  tasks: {
    root: ["tasks"] as const,
    list: () => ["tasks", "list"] as const,
    executions: (params?: unknown) => ["tasks", "executions", params] as const,
  },
  tradingCalendar: {
    root: ["trading-calendar"] as const,
    byYear: (year: number) => ["trading-calendar", year] as const,
  },
  dataSources: {
    config: () => ["data-source-config"] as const,
  },
};
