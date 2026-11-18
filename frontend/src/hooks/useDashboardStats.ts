"use client";

import { usePortfolioList } from "./usePortfolio";
import { useSubscriptionList, useTradeList } from "./useTrade";
import { useInvestorList } from "./useInvestor";
import { Portfolio } from "@/types/portfolio";
import { Subscription } from "@/types/subscription";
import { Trade } from "@/types/trade";
import { Investor } from "@/types/investor";

export interface DashboardStats {
  portfolios: Portfolio[];
  activePortfolios: Portfolio[];
  subscriptions: Subscription[];
  trades: Trade[];
  investors: Investor[];
  totalValue: number;
  totalProfit: number;
  avgReturn: number;
  pendingSubscriptions: Subscription[];
  pendingTrades: Trade[];
  isLoading: boolean;
}

/**
 * 首页仪表盘统计聚合 hook。
 * 桌面端与移动端共用同一数据源与计算逻辑，仅渲染层各自实现。
 */
export function useDashboardStats(): DashboardStats {
  const { data: portfoliosData, isLoading: portfoliosLoading } = usePortfolioList({ page_size: 100 });
  const { data: subscriptionsData, isLoading: subscriptionsLoading } = useSubscriptionList({ page_size: 100 });
  const { data: tradesData, isLoading: tradesLoading } = useTradeList({ page_size: 100 });
  const { data: investorsData, isLoading: investorsLoading } = useInvestorList({ page_size: 100 });

  const portfolios = portfoliosData?.items || [];
  const subscriptions = subscriptionsData?.items || [];
  const trades = tradesData?.items || [];
  const investors = investorsData?.items || [];

  const activePortfolios = portfolios.filter((p) => p.status === "active");
  // R8 跨口径合并（#905 PR3）：总资产/总收益 = 净值型 + 台账型。后端 list_portfolios 已按 kind
  // 分流派生（净值型走快照口径、台账型走台账口径，两类语义均「市值−净投入」），前端直接求和。
  const totalValue = activePortfolios.reduce((sum, p) => sum + (p.total_value || 0), 0);
  const totalProfit = activePortfolios.reduce((sum, p) => sum + (p.total_profit || 0), 0);

  // 平均收益率（a 口径，#905 PR3）：只在净值型集合计算（首末净值百分数）。台账型无净值序列、
  // 收益率语义不同，不纳入平均（避免得到无定义量，P1 禁止静默失真）；UI 标注「不含台账型」。
  const navActive = activePortfolios.filter((p) => p.kind !== "ledger");
  const portfoliosWithReturn = navActive.filter(
    (p) => p.cumulative_return !== undefined && p.cumulative_return !== null
  );
  const avgReturn =
    portfoliosWithReturn.length > 0
      ? portfoliosWithReturn.reduce((sum, p) => sum + (p.cumulative_return || 0), 0) /
        portfoliosWithReturn.length
      : 0;

  const pendingSubscriptions = subscriptions.filter((s) => s.status === "pending");
  const pendingTrades = trades.filter((t) => t.status === "pending");

  return {
    portfolios,
    activePortfolios,
    subscriptions,
    trades,
    investors,
    totalValue,
    totalProfit,
    avgReturn,
    pendingSubscriptions,
    pendingTrades,
    isLoading: portfoliosLoading || subscriptionsLoading || tradesLoading || investorsLoading,
  };
}
