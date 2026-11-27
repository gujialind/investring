"use client";

import { Card, CardContent } from "@/components/ui/card";
import AccountMenu from "@/components/shared/AccountMenu";
import DashboardSummary from "@/components/shared/DashboardSummary";
import LoadingState from "@/components/shared/LoadingState";
import QueryErrorState from "@/components/shared/QueryErrorState";
import { useDashboardStats } from "@/hooks/useDashboardStats";

export default function MobileDashboardPage() {
  const {
    summary,
    subscriptions,
    trades,
    isLoading,
    isFetching,
    isInitialError,
    isRefetchError,
    error,
    refetch,
  } = useDashboardStats();

  if (isLoading) {
    return <LoadingState />;
  }

  if (isInitialError || !summary) {
    return <QueryErrorState error={error} onRetry={() => void refetch()} />;
  }

  return (
    <div className="space-y-4 p-4">
      <div>
        <h1 className="text-xl font-semibold leading-7">首页</h1>
        <p className="text-sm text-muted-foreground">家庭财务档案，总账</p>
      </div>
      <DashboardSummary
        summary={summary}
        subscriptions={subscriptions}
        trades={trades}
        variant="mobile"
        basePath="/m"
        isFetching={isFetching}
        activityError={isRefetchError ? error : undefined}
        onRetryActivity={() => void refetch()}
      />

      {/* 账户区（#878）：移动端此前无退出入口，这里补账户菜单（修改密码/退出登录，全角色） */}
      <div>
        <h2 className="text-base font-semibold mb-3">账户</h2>
        <Card>
          <CardContent className="p-2">
            <AccountMenu align="end" />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
