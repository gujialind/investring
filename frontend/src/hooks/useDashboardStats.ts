"use client";

import { useDashboardSummary } from "./useDashboardSummary";
import { useSubscriptionList, useTradeList } from "./useTrade";
import type { DashboardSummary } from "@/lib/api";
import type { Subscription } from "@/types/subscription";
import type { Trade } from "@/types/trade";

export interface DashboardStats {
  summary: DashboardSummary | undefined;
  subscriptions: Subscription[];
  trades: Trade[];
  isLoading: boolean;
  isFetching: boolean;
  isInitialError: boolean;
  summaryError: unknown;
  activityError: unknown;
  refetchSummary: () => Promise<void>;
  refetchActivity: () => Promise<void>;
}

/** 首页汇总与最近活动共用同一数据源；核心指标只认 summary，不在前端二次求和。 */
export function useDashboardStats(): DashboardStats {
  const summaryQuery = useDashboardSummary();
  const subscriptionsQuery = useSubscriptionList({ page_size: 100 });
  const tradesQuery = useTradeList({ page_size: 100 });

  const summary = summaryQuery.data;
  const subscriptions = subscriptionsQuery.data?.items ?? [];
  const trades = tradesQuery.data?.items ?? [];
  const activityError = subscriptionsQuery.isError || tradesQuery.isError
    ? subscriptionsQuery.error ?? tradesQuery.error
    : undefined;

  return {
    summary,
    subscriptions,
    trades,
    isLoading: summaryQuery.isPending || subscriptionsQuery.isPending || tradesQuery.isPending,
    isFetching: summaryQuery.isFetching || subscriptionsQuery.isFetching || tradesQuery.isFetching,
    isInitialError: summaryQuery.isError && !summary,
    summaryError: summaryQuery.error,
    activityError,
    refetchSummary: async () => {
      await summaryQuery.refetch();
    },
    refetchActivity: async () => {
      await Promise.all([subscriptionsQuery.refetch(), tradesQuery.refetch()]);
    },
  };
}
