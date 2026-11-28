"use client";

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
        variant="mobile"
        basePath="/m"
        isFetching={isFetching}
        summaryError={summaryError}
        onRetrySummary={() => void refetchSummary()}
        activityError={activityError}
        onRetryActivity={() => void refetchActivity()}
      />
    );
  };

  return (
    <div className="space-y-4 p-4">
      <div>
        <h1 className="text-xl font-semibold leading-7">首页</h1>
        <p className="text-sm text-muted-foreground">家庭财务档案，总账</p>
      </div>
      {renderContent()}

    </div>
  );
}
