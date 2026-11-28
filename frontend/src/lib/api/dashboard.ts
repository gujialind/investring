import { request } from "./client";

export type DashboardXirrReason = "no_solution" | "insufficient_flows" | "short_history";

export interface DashboardAggregateXirr {
  value: number | null;
  reliable: boolean;
  reason: DashboardXirrReason | null;
  cash_flow_count: number;
  holding_days: number | null;
}

export interface DashboardPortfolioSummary {
  code: string;
  name: string;
  kind: string;
  total_value: number | null;
  total_profit: number | null;
  cumulative_return: number | null;
  mwr: number | null;
  annualization_reliable: boolean;
  holding_days: number | null;
}

export interface DashboardSummary {
  total_value: number;
  total_profit: number;
  aggregate_xirr: DashboardAggregateXirr;
  pending_subscription_count: number;
  pending_trade_count: number;
  portfolios: DashboardPortfolioSummary[];
}

export const dashboardApi = {
  getSummary: () =>
    request<DashboardSummary>({ method: "GET", url: "/dashboard/summary" }),
};
