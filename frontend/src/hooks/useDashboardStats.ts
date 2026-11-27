"use client";

import { useDashboardSummary } from "./useDashboardSummary";
import { useSubscriptionList, useTradeList } from "./useTrade";
import type {
  DashboardAggregateXirr,
  DashboardPortfolioSummary,
  DashboardSummary,
} from "@/lib/api";
import type { Subscription } from "@/types/subscription";
import type { Trade } from "@/types/trade";

export interface DashboardStats {
  summary: DashboardSummary | undefined;
  activePortfolios: DashboardPortfolioSummary[];
  subscriptions: Subscription[];
  trades: Trade[];
  totalValue: number | undefined;
  totalProfit: number | undefined;
  aggregateXirr: DashboardAggregateXirr | undefined;
  pendingSubscriptionCount: number | undefined;
  pendingTradeCount: number | undefined;
  pendingSubscriptions: Subscription[];
  pendingTrades: Trade[];
  isLoading: boolean;
  isFetching: boolean;
  isInitialError: boolean;
  isRefetchError: boolean;
  error: unknown;
  refetch: () => Promise<void>;
}

/** 首页汇总与最近活动共用同一数据源；核心指标只认 summary，不在前端二次求和。 */
export function useDashboardStats(): DashboardStats {
  const summaryQuery = useDashboardSummary();
  const subscriptionsQuery = useSubscriptionList({ page_size: 100 });
  const tradesQuery = useTradeList({ page_size: 100 });

  const summary = summaryQuery.data;
  const subscriptions = subscriptionsQuery.data?.items ?? [];
  const trades = tradesQuery.data?.items ?? [];
  const isError = summaryQuery.isError || subscriptionsQuery.isError || tradesQuery.isError;
  const error = summaryQuery.error ?? subscriptionsQuery.error ?? tradesQuery.error;

  return {
    summary,
    activePortfolios: summary?.portfolios ?? [],
    subscriptions,
    trades,
    totalValue: summary?.total_value,
    totalProfit: summary?.total_profit,
    aggregateXirr: summary?.aggregate_xirr,
    pendingSubscriptionCount: summary?.pending_subscription_count,
    pendingTradeCount: summary?.pending_trade_count,
    pendingSubscriptions: subscriptions.filter((subscription) => subscription.status === "pending"),
    pendingTrades: trades.filter((trade) => trade.status === "pending"),
    isLoading: summaryQuery.isPending || subscriptionsQuery.isPending || tradesQuery.isPending,
    isFetching: summaryQuery.isFetching || subscriptionsQuery.isFetching || tradesQuery.isFetching,
    isInitialError: summaryQuery.isError && !summary,
    isRefetchError: Boolean(summary) && isError,
    error,
    refetch: async () => {
      await Promise.all([summaryQuery.refetch(), subscriptionsQuery.refetch(), tradesQuery.refetch()]);
    },
  };
}
