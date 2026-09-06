"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ArrowLeft, ChevronRight, Settings2 } from "lucide-react";
import Link from "next/link";
import {
  usePortfolio,
  useLatestSnapshot,
  usePositionList,
  usePortfolioInvestors,
  useActivatePortfolio,
  useNavHistory,
  usePortfolioPerformance,
} from "@/hooks/usePortfolio";
import { useRoleCheck } from "@/hooks/useAuth";
import { useAssetClassifications } from "@/hooks/useAssetClassification";
import NavCurve from "@/components/charts/NavCurve";
import AssetAllocationPie from "@/components/charts/AssetAllocationPie";
import PortfolioStatsCards from "@/components/shared/PortfolioStatsCards";
import PerformanceMetrics from "@/components/shared/PerformanceMetrics";
import PortfolioActionButtons from "@/components/shared/PortfolioActionButtons";
import PortfolioInvestorsList from "@/components/shared/PortfolioInvestorsList";
import PositionSections from "@/components/shared/PositionSections";
import DisplayConfigDialog from "@/components/shared/dialogs/DisplayConfigDialog";
import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import { buildAllocation } from "@/lib/allocation";

function MobilePortfolioDetailInner() {
  const params = useParams();
  const searchParams = useSearchParams();
  const code = params.code as string;
  const showInvestors = searchParams.get("tab") === "investors";

  const { data: portfolio, isLoading: portfolioLoading } = usePortfolio(code);
  const { data: snapshot, isLoading: snapshotLoading } = useLatestSnapshot(code);
  const { data: positionsData, isLoading: positionsLoading } = usePositionList(code, {
    page_size: 100,
  });
  // asset_class 维度字典（issue #128）：驱动饼图颜色/顺序与持仓分区
  const { data: assetClassDict, isLoading: dictLoading } =
    useAssetClassifications("asset_class");
  // 投资人列表仅 ?tab=investors 视图惰性查询（draft 也允许查看）
  const { data: investors, isLoading: investorsLoading } = usePortfolioInvestors(code, {
    enabled: showInvestors,
  });
  const activatePortfolio = useActivatePortfolio();
  const { isAdmin } = useRoleCheck();

  // 净值走势用历史序列（与桌面统一 NavCurve，移动端高度 200）
  const { data: navHistoryData } = useNavHistory(code);
  const navHistory = (navHistoryData || [])
    .filter((r) => r.unit_price !== null)
    .map((r) => ({ date: r.snapshot_date, nav: r.unit_price as number }));

  // 绩效指标：draft 组合无快照，不请求
  const isDraftStatus = portfolio?.status === "draft";
  const { data: performance } = usePortfolioPerformance(code, !isDraftStatus);

  // 分组维度配置弹窗（issue #144，与桌面端共用 Dialog）
  const [displayConfigOpen, setDisplayConfigOpen] = useState(false);

  const positions = positionsData?.items || [];
  const assetClasses = assetClassDict?.items || [];
  const isLoading =
    portfolioLoading ||
    snapshotLoading ||
    positionsLoading ||
    dictLoading ||
    (showInvestors && investorsLoading);

  if (isLoading) {
    return <LoadingState />;
  }

  if (!portfolio) {
    return (
      <EmptyState
        message="组合不存在"
        action={
          <Link href="/m/portfolio">
            <Button variant="outline">
              <ArrowLeft className="mr-2 h-4 w-4" />
              返回列表
            </Button>
          </Link>
        }
      />
    );
  }

  const isDraft = portfolio.status === "draft";
  const allocation = buildAllocation(positions, assetClasses);

  /* 页尾「管理」列表项（低频入口，替换旧 Quick Links） */
  const manageLinks = [
    { label: "持仓管理", href: `/m/portfolio/${code}/positions` },
    { label: "申购赎回记录", href: `/m/portfolio/${code}/subscriptions` },
    { label: "调仓交易记录", href: `/m/portfolio/${code}/trades` },
    { label: "份额变动事件", href: `/m/portfolio/${code}/share-change-events` },
    { label: "快照管理", href: `/m/portfolio/${code}/snapshots` },
  ];

  return (
    <div className="space-y-4 p-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link href="/m/portfolio">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-semibold">{portfolio.name}</h1>
          <p className="text-xs text-muted-foreground">
            {portfolio.code}
            {portfolio.started_at && (
              <span> · 成立于 {portfolio.started_at.split("T")[0]}</span>
            )}
            <span>
              {" "}· {portfolio.status === "active" ? "活跃" : isDraft ? "草稿" : "已关闭"}
            </span>
          </p>
        </div>
      </div>

      {/* Status Alert */}
      {isDraft && (
        <Alert>
          <AlertDescription>
            组合尚未激活，请执行首次申购以启动组合。初始净值固定为 1.0000
          </AlertDescription>
        </Alert>
      )}

      {showInvestors ? (
        /* ?tab=investors 投资人视图（draft 也允许） */
        <PortfolioInvestorsList
          investors={investors}
          totalShares={snapshot?.total_shares || 0}
        />
      ) : (
        <>
          {/* Stats Cards */}
          {!isDraft && (
            <div>
              <PortfolioStatsCards
                totalValue={portfolio.total_value ?? snapshot?.total_value ?? null}
                unitPrice={snapshot?.unit_price ?? null}
                totalProfit={portfolio.total_profit ?? null}
                holdingDays={performance?.holding_days ?? null}
                variant="mobile"
              />
              <p className="mt-1.5 text-xs text-muted-foreground">
                最新快照日期：{snapshot?.snapshot_date || "--"}
              </p>
            </div>
          )}

          {/* 高频操作：申购赎回 / 调仓（2 列网格；draft/closed 由 ActionButtons 内部处理） */}
          {isAdmin && (
            <div className="grid grid-cols-2 gap-2">
              <PortfolioActionButtons
                portfolioCode={code}
                status={portfolio.status as "draft" | "active" | "closed"}
                basePath="/m/portfolio"
                variant="mobile"
                onActivateClick={() => activatePortfolio.mutate(code)}
                isActivatePending={activatePortfolio.isPending}
              />
            </div>
          )}

          {!isDraft && (
            <>
              {/* 资产分布 */}
              <Card>
                <CardContent className="p-4">
                  <h3 className="mb-3 text-sm font-medium">资产分布</h3>
                  <AssetAllocationPie items={allocation} height={150} />
                </CardContent>
              </Card>

              {/* 分类持仓分区（含在途资金独立卡片）；
                  二级分组维度优先取组合级 display_config（issue #144） */}
              <PositionSections
                positions={positions}
                assetClasses={assetClasses}
                displayConfig={portfolio.display_config}
                action={
                  isAdmin ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setDisplayConfigOpen(true)}
                    >
                      <Settings2 className="mr-1.5 h-4 w-4" />
                      分组维度
                    </Button>
                  ) : undefined
                }
              />
              <DisplayConfigDialog
                open={displayConfigOpen}
                onOpenChange={setDisplayConfigOpen}
                portfolioCode={code}
                currentConfig={portfolio.display_config}
              />

              {/* NAV Chart */}
              {navHistory.length > 0 && (
                <Card>
                  <CardContent className="p-4">
                    <h3 className="mb-3 text-sm font-medium">净值走势</h3>
                    <NavCurve data={navHistory} height={200} initialNav={1.0} />
                  </CardContent>
                </Card>
              )}

              {/* 绩效指标（紧凑两列） */}
              <PerformanceMetrics data={performance} variant="mobile" />
            </>
          )}

          {/* 页尾「管理」列表（替换旧 Quick Links） */}
          <Card>
            <CardContent className="p-0">
              <h3 className="px-4 pb-1 pt-4 text-sm font-medium text-muted-foreground">
                管理
              </h3>
              <div className="divide-y">
                {manageLinks.map((link) => (
                  <Link
                    key={link.href}
                    href={link.href}
                    className="flex items-center justify-between px-4 py-3 text-sm"
                  >
                    {link.label}
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  </Link>
                ))}
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

/**
 * 移动端组合详情页（issue #99）：与桌面同构单列 + 页尾管理列表。
 * useSearchParams 需包 Suspense 边界（Next 15 静态预渲染要求）。
 */
export default function MobilePortfolioDetailPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <MobilePortfolioDetailInner />
    </Suspense>
  );
}
