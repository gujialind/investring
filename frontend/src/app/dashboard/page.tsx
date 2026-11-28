"use client";

import MainLayout from "@/components/layout/MainLayout";
import DashboardSummary from "@/components/shared/DashboardSummary";
import LoadingState from "@/components/shared/LoadingState";
import QueryErrorState from "@/components/shared/QueryErrorState";
import { useDashboardStats } from "@/hooks/useDashboardStats";

export default function DashboardPage() {
  const {
    summary,
    subscriptions,
    trades,
    isLoading,
    isFetching,
    isInitialError,
    summaryError,
    activityError,
    refetchSummary,
    refetchActivity,
  } = useDashboardStats();

  const renderContent = () => {
    if (isLoading) return <LoadingState />;
    if (isInitialError || !summary) {
      return <QueryErrorState error={summaryError} onRetry={() => void refetchSummary()} />;
    }
    return (
      <DashboardSummary
        summary={summary}
        subscriptions={subscriptions}
        trades={trades}
        variant="desktop"
        isFetching={isFetching}
        summaryError={summaryError}
        onRetrySummary={() => void refetchSummary()}
        activityError={activityError}
        onRetryActivity={() => void refetchActivity()}
      />
    );
  };

  return (
    <MainLayout>
      <div className="space-y-6">
        <div>
          <h1 className="text-xl font-semibold leading-7">首页</h1>
          <p className="text-muted-foreground">家庭财务档案，总账</p>
        </div>
        {renderContent()}
      </div>
    </MainLayout>
  );
}
