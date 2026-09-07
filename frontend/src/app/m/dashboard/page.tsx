"use client";

import { Card, CardContent } from "@/components/ui/card";
import { formatCurrency, formatSharesUnit, formatReturnRate, getReturnColorClass, getStatusBadgeVariant } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import Link from "next/link";
import { useDashboardStats } from "@/hooks/useDashboardStats";
import DashboardStatsCards from "@/components/shared/DashboardStatsCards";
import LoadingState from "@/components/shared/LoadingState";

export default function MobileDashboardPage() {
  const {
    portfolios,
    activePortfolios,
    subscriptions,
    investors,
    totalValue,
    avgReturn,
    pendingSubscriptions,
    isLoading,
  } = useDashboardStats();

  if (isLoading) {
    return (
      <LoadingState />
    );
  }

  return (
    <div className="space-y-4 p-4">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold">首页</h1>
        <p className="text-sm text-muted-foreground">投资组合概览</p>
      </div>

      {/* Summary Cards */}
      <DashboardStatsCards
        totalValue={totalValue}
        avgReturn={avgReturn}
        activeCount={activePortfolios.length}
        totalCount={portfolios.length}
        investorCount={investors.length}
        variant="mobile"
      />

      {/* Pending Transactions Alert */}
      {pendingSubscriptions.length > 0 && (
        <Card className="bg-warning-soft border-warning/30">
          <CardContent className="p-3">
            <div className="flex items-center gap-2">
              <div className="h-2 w-2 rounded-full bg-warning"></div>
              <span className="text-sm font-medium text-warning-foreground">
                {pendingSubscriptions.length} 笔待确认交易
              </span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Active Portfolios List */}
      <div>
        <h2 className="text-base font-semibold mb-3">活跃组合</h2>
        {activePortfolios.length === 0 ? (
          <Card>
            <CardContent className="p-8 text-center text-muted-foreground">
              暂无活跃组合，请先创建组合
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-2">
            {activePortfolios.map((portfolio) => (
              <Link key={portfolio.code} href={`/m/portfolio/${portfolio.code}`}>
                <Card className="hover:bg-accent transition-colors">
                  <CardContent className="p-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="font-medium text-sm">{portfolio.name}</p>
                        <p className="text-xs text-muted-foreground">{portfolio.code}</p>
                      </div>
                      <div className="text-right">
                        <p className="font-medium text-sm">{formatCurrency(portfolio.total_value || 0)}</p>
                        <p className={`text-xs ${getReturnColorClass(portfolio.cumulative_return || 0)}`}>
                          {formatReturnRate(portfolio.cumulative_return || 0)}
                        </p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </div>

      {/* Recent Activity */}
      {subscriptions.length > 0 && (
        <div>
          <h2 className="text-base font-semibold mb-3">最近交易</h2>
          <div className="space-y-2">
            {subscriptions.slice(0, 5).map((sub) => (
              <Card key={sub.id}>
                <CardContent className="p-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm font-medium">
                        {sub.sub_type === "subscribe" ? "申购" : "赎回"}
                        <Badge className="ml-2" variant={getStatusBadgeVariant(sub.status)}>
                          {sub.status === "pending" ? "待确认" : sub.status === "confirmed" ? "已确认" : "已取消"}
                        </Badge>
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {sub.portfolio_code} | {sub.investor_code}
                      </p>
                    </div>
                    <div className="text-right">
                      <p className="text-sm font-medium">
                        {sub.sub_type === "subscribe"
                          ? formatCurrency(sub.amount || 0)
                          : formatSharesUnit(sub.shares || 0)
                        }
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {sub.apply_date}
                      </p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
