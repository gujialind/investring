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
