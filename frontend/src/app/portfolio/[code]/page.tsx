"use client";

import { useParams, useSearchParams, useRouter } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import MainLayout from "@/components/layout/MainLayout";
import PageHeader from "@/components/shared/PageHeader";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ArrowLeft, Settings2 } from "lucide-react";
import Link from "next/link";
import {
  usePortfolio,
  usePortfolioInvestors,
  usePositionList,
  useActivatePortfolio,
  useActivateLedgerPortfolio,
  useDeletePortfolio,
  usePortfolioPerformance,
  usePortfolioReturns,
} from "@/hooks/usePortfolio";
import { useRoleCheck } from "@/hooks/useAuth";
import { useAssetClassifications } from "@/hooks/useAssetClassification";
import { usePortfolioSnapshotSummary } from "@/hooks/useSnapshot";
import DeletePortfolioDialog from "@/components/shared/dialogs/DeletePortfolioDialog";
import AssetAllocationPie from "@/components/charts/AssetAllocationPie";
import PortfolioStatsCards from "@/components/shared/PortfolioStatsCards";
import PerformanceMetrics from "@/components/shared/PerformanceMetrics";
import PortfolioActionButtons from "@/components/shared/PortfolioActionButtons";
import PortfolioInvestorsList from "@/components/shared/PortfolioInvestorsList";
import PortfolioHoldings from "@/components/shared/PortfolioHoldings";
import DisplayConfigDialog from "@/components/shared/dialogs/DisplayConfigDialog";
import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import QueryErrorState from "@/components/shared/QueryErrorState";
import PortfolioNavTrendCard from "@/components/shared/PortfolioNavTrendCard";
import SnapshotCatchupAlert from "@/components/shared/SnapshotCatchupAlert";
import LedgerContent from "@/components/shared/LedgerContent";
import { buildAllocation } from "@/lib/allocation";
import { getStatusBadgeVariant } from "@/lib/utils";
import { useChartTheme } from "@/hooks/useChartTheme";
import { useViewQueryState } from "@/hooks/useViewQueryState";

function PortfolioDetailInner() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  const code = params.code as string;
  const showInvestors = searchParams.get("tab") === "investors";

  const { data: portfolio, isLoading: portfolioLoading } = usePortfolio(code);
  const isDraftStatus = portfolio?.status === "draft";
  const isLedger = portfolio?.kind === "ledger";
  const snapshotReadsEnabled = !!portfolio && !isDraftStatus && !isLedger;
  const {
    snapshot,
    dailyReturn,
    isLoading: snapshotLoading,
    isError: snapshotError,
    error: snapshotErr,
    refetch: refetchSnapshots,
  } = usePortfolioSnapshotSummary(code, snapshotReadsEnabled);
  // 投资人列表仅 ?tab=investors 视图惰性查询（draft 也允许查看）
  const { data: investors, isLoading: investorsLoading } = usePortfolioInvestors(code, {
    enabled: showInvestors,
  });
  const { data: positionsData, isLoading: positionsLoading } = usePositionList(code, {
    page_size: 100,
  });
  // asset_class 维度字典（issue #128）：驱动饼图颜色/顺序与持仓分区
  const { data: assetClassDict, isLoading: dictLoading } =
    useAssetClassifications("asset_class");

  // 视图状态 URL 单点（?view= ＋ #915 B2 ?sub=，双端共用 hook；连点竞态口径见 hook 注释）
  const {
    view,
    onViewChange: handleViewChange,
    subDimOverrides,
    onSubDimToggle: handleSubDimToggle,
  } = useViewQueryState(assetClassDict?.dimension_rules);
  const activatePortfolio = useActivatePortfolio();
  const activateLedger = useActivateLedgerPortfolio();
  const deletePortfolio = useDeletePortfolio();
  const { isAdmin } = useRoleCheck();
  // 主题 hook 须在早退（isLoading/!portfolio）之前调用（rules-of-hooks）
  const theme = useChartTheme();

  // 绩效指标（后端计算）：draft 组合无快照，不请求
  const { data: performance } = usePortfolioPerformance(code, !isDraftStatus);
  // 累计收益率 chip 数据源（#904 P0b）：returns 端点后端单点计算；详情/列表 schema 不含该字段
  const { data: returns } = usePortfolioReturns(code, !isDraftStatus);

  // 分组维度配置弹窗（issue #144）
  const [displayConfigOpen, setDisplayConfigOpen] = useState(false);
  // 删除空壳确认弹窗（#820 扩围）
  const [isDeleteOpen, setIsDeleteOpen] = useState(false);
  const deleteReturnFocusRef = useRef<HTMLButtonElement | null>(null);

  const positions = positionsData?.items || [];
  const assetClasses = assetClassDict?.items || [];
  const isLoading =
    portfolioLoading ||
    snapshotLoading ||
    positionsLoading ||
    dictLoading ||
    (showInvestors && investorsLoading);

  if (isLoading) {
    return (
      <MainLayout>
        <LoadingState />
      </MainLayout>
    );
  }

  if (!portfolio) {
    return (
      <MainLayout>
        <EmptyState
          message="组合不存在"
          action={
            <Link href="/portfolio">
              <Button variant="outline">
                <ArrowLeft className="mr-2 h-4 w-4" />
                返回列表
              </Button>
            </Link>
          }
        />
      </MainLayout>
    );
  }

  const isDraft = portfolio.status === "draft";
  // 删除入口可见性（#820 扩围）：draft/closed 两态出现，是否真空壳由后端 409 判定
  const isDeletable = portfolio.status === "draft" || portfolio.status === "closed";
  const allocation = buildAllocation(positions, assetClasses, theme);

  return (
    <MainLayout>
      <div className="space-y-6">
        {snapshotError && (
          <QueryErrorState
            error={snapshotErr}
            onRetry={() => void refetchSnapshots()}
            className="rounded-md border border-destructive/30 bg-destructive-soft p-4 text-center"
          />
        )}
        {/* 页头（#904 PageHeader）：面包屑返回 / 名称+状态 badge / code·最新快照日期 / 操作区（#943 统一「更多」菜单） */}
        <PageHeader
          crumbs={[{ href: "/portfolio", label: "组合", ariaLabel: "返回组合列表" }]}
          title={portfolio.name}
          badge={
            portfolio.status !== "active" ? (
              <Badge variant={getStatusBadgeVariant(portfolio.status)}>
                {isDraft ? "草稿" : "已关闭"}
              </Badge>
            ) : undefined
          }
          meta={
            <>
              {portfolio.code}
              {!isLedger && (
                <span> · 最新快照日期 {snapshot?.snapshot_date || "--"}</span>
              )}
            </>
          }
          actions={
            !showInvestors && (isAdmin || !isLedger) && (
              <PortfolioActionButtons
                portfolioCode={code}
                status={portfolio.status as "draft" | "active" | "closed"}
                kind={portfolio.kind}
                basePath="/portfolio"
                variant="desktop"
                isAdmin={isAdmin}
                onActivateClick={() =>
                  isLedger && isDraft ? activateLedger.mutate(code) : activatePortfolio.mutate(code)
                }
                isActivatePending={isLedger && isDraft ? activateLedger.isPending : activatePortfolio.isPending}
                onDeleteClick={
                  isAdmin && isDeletable
                    ? (target) => {
                        deleteReturnFocusRef.current = target;
                        setIsDeleteOpen(true);
                      }
                    : undefined
                }
                isDeletePending={deletePortfolio.isPending}
              />
            )
          }
        />
          {/* 删除空壳确认（#820 扩围）：点名 name + code + 不可恢复 */}
          <DeletePortfolioDialog
            open={isDeleteOpen}
            onOpenChange={setIsDeleteOpen}
            portfolioCode={portfolio.code}
            portfolioName={portfolio.name}
            returnFocusRef={deleteReturnFocusRef}
            isPending={deletePortfolio.isPending}
            onConfirm={() =>
              deletePortfolio.mutate(code, {
                // 详情页数据已被 removeQueries，留在本页会打到 404，成功后回列表
                onSuccess: () => router.push("/portfolio"),
                onSettled: () => setIsDeleteOpen(false),
              })
            }
          />

        {isDraft && !isLedger && (
          <Alert>
            <AlertDescription>
              组合尚未激活，请执行首次申购以启动组合。初始净值固定为 1.0000
            </AlertDescription>
          </Alert>
        )}

        {/* 快照停更可见提示（#906 SPEC §4）：trailing 缺口非空才渲染；台账型恒空不请求 */}
        {!isLedger && !isDraft && <SnapshotCatchupAlert portfolioCode={code} />}

        {isLedger ? (
          /* 台账型独立视图（issue #905 PR3）：按平台记市值/进出 + 口径A 绩效；无净值/份额/投资人 Tab */
          <LedgerContent portfolio={portfolio} variant="desktop" />
        ) : showInvestors ? (
          /* ?tab=investors 投资人视图（draft 也允许）；占比用后端 ratio，不再传 totalShares（#693） */
          <PortfolioInvestorsList investors={investors} />
        ) : (
          /* 统计卡全宽 + 主体左右分栏（draft 时仅统计段） */
          <div className="space-y-4">
            {/* 1. 四项统计；页头已显示同一最新快照日期 */}
            <div>
              <PortfolioStatsCards
                totalValue={!isDraft ? portfolio.total_value ?? snapshot?.total_value ?? null : null}
                unitPrice={!isDraft ? snapshot?.unit_price ?? null : null}
                totalProfit={!isDraft ? portfolio.total_profit ?? null : null}
                cumulativeReturn={returns?.cumulative_return ?? null}
                dailyReturn={!isDraft ? dailyReturn : null}
                holdingDays={!isDraft ? performance?.holding_days ?? null : null}
                variant="desktop"
              />
            </div>

            {!isDraft && (
              /* 宽屏左右分栏（D1）；窄屏改为单列，避免持仓网格与右侧 360px 栏重叠。 */
              <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
                <div className="min-w-0 space-y-4 lg:flex-1">
                  {/* 持仓明细：按产品/按平台双视图（#595，URL ?view= 持久化）；
                      二级组合键子集优先取组合级 display_config（issue #144/#915） */}
                  <PortfolioHoldings
                    portfolioCode={code}
                    basePath="/portfolio"
                    assetClasses={assetClasses}
                    displayConfig={portfolio.display_config}
                    dimensionRules={assetClassDict?.dimension_rules}
                    view={view}
                    onViewChange={handleViewChange}
                    subDimOverrides={subDimOverrides}
                    onSubDimToggle={handleSubDimToggle}
                    variant="desktop"
                    action={
                      isAdmin ? (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setDisplayConfigOpen(true)}
                        >
                          <Settings2 className="mr-2 h-4 w-4" />
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
                </div>

                <div className="w-full space-y-4 lg:w-[360px] lg:shrink-0">
                  {/* 资产分布（环形图 + 图例；右栏 360 强制竖排，防图例挤压竖排） */}
                  <Card>
                    <CardContent className="pt-6">
                      <h3 className="mb-4 text-sm font-medium">资产分布</h3>
                      <AssetAllocationPie items={allocation} forceColumn />
                    </CardContent>
                  </Card>

                  {/* #649 起双端共享组件（右栏窄：标题与区间 chips 放不下时整组换行） */}
                  <PortfolioNavTrendCard code={code} variant="desktop" />

                  {/* 绩效指标（6 项） */}
                  <PerformanceMetrics data={performance} variant="desktop" />
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </MainLayout>
  );
}

/**
 * 组合详情页（issue #99 单列五段；#595 改为主体左右分栏 + 持仓双视图 + ?view= 持久化）。
 * useSearchParams 需包 Suspense 边界（Next 15 静态预渲染要求）。
 */
export default function PortfolioDetailPage() {
  return (
    <Suspense
      fallback={
        <MainLayout>
          <LoadingState />
        </MainLayout>
      }
    >
      <PortfolioDetailInner />
    </Suspense>
  );
}
