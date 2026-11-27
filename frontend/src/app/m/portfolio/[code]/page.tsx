"use client";

import PageHeader from "@/components/shared/PageHeader";
import { Badge } from "@/components/ui/badge";
import { getStatusBadgeVariant } from "@/lib/utils";

import { useParams, useSearchParams, useRouter } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ArrowLeft, Settings2 } from "lucide-react";
import Link from "next/link";
import {
  usePortfolio,
  usePositionList,
  usePortfolioInvestors,
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
import BackToTop from "@/components/mobile/BackToTop";
import PortfolioInvestorsList from "@/components/shared/PortfolioInvestorsList";
import PortfolioHoldings from "@/components/shared/PortfolioHoldings";
import PortfolioNavTrendCard from "@/components/shared/PortfolioNavTrendCard";
import SnapshotCatchupAlert from "@/components/shared/SnapshotCatchupAlert";
import LedgerContent from "@/components/shared/LedgerContent";
import DisplayConfigDialog from "@/components/shared/dialogs/DisplayConfigDialog";
import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import QueryErrorState from "@/components/shared/QueryErrorState";
import { buildAllocation } from "@/lib/allocation";
import { useChartTheme } from "@/hooks/useChartTheme";
import { useViewQueryState } from "@/hooks/useViewQueryState";

function MobilePortfolioDetailInner() {
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
  // 投资人列表仅 ?tab=investors 视图惰性查询（draft 也允许查看）
  const { data: investors, isLoading: investorsLoading } = usePortfolioInvestors(code, {
    enabled: showInvestors,
  });
  const activatePortfolio = useActivatePortfolio();
  const activateLedger = useActivateLedgerPortfolio();
  const deletePortfolio = useDeletePortfolio();
  const { isAdmin } = useRoleCheck();
  // 主题 hook 须在早退（isLoading/!portfolio）之前调用（rules-of-hooks）
  const theme = useChartTheme();

  // 绩效指标：draft 组合无快照，不请求
  const { data: performance } = usePortfolioPerformance(code, !isDraftStatus);
  // 累计收益率 chip 数据源（#904 P0b）：returns 端点后端单点计算
  const { data: returns } = usePortfolioReturns(code, !isDraftStatus);

  // 分组维度配置弹窗（issue #144，与桌面端共用 Dialog）
  const [displayConfigOpen, setDisplayConfigOpen] = useState(false);
  // 删除空壳确认弹窗（#820 扩围，与桌面端共用 Dialog）
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
  // 删除入口可见性（#820 扩围）：draft/closed 两态出现，是否真空壳由后端 409 判定
  const isDeletable = portfolio.status === "draft" || portfolio.status === "closed";
  const allocation = buildAllocation(positions, assetClasses, theme);
  // 组合操作组（#943 S4）：台账与非台账共用同一组入参，仅放置位置不同
  const actionButtons = (
    <PortfolioActionButtons
      portfolioCode={code}
      status={portfolio.status as "draft" | "active" | "closed"}
      kind={portfolio.kind}
      basePath="/m/portfolio"
      variant="mobile"
      isAdmin={isAdmin}
      onActivateClick={() => (isLedger && isDraft ? activateLedger.mutate(code) : activatePortfolio.mutate(code))}
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
  );

  const detailContent = (
    <div className="space-y-4 px-4 pt-4 safe-area-pb-16">
      {snapshotError && (
        <QueryErrorState
          error={snapshotErr}
          onRetry={() => void refetchSnapshots()}
          className="rounded-md border border-destructive/30 bg-destructive-soft p-4 text-center"
        />
      )}
      {/* Header */}
      <PageHeader
        variant="mobile"
        crumbs={[{ href: "/m/portfolio", label: "组合", ariaLabel: "返回组合列表" }]}
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
      />

      {/* Status Alert（台账型 draft 引导在 LedgerContent，不显示净值型首购文案） */}
      {isDraft && !isLedger && (
        <Alert>
          <AlertDescription>
            组合尚未激活，请执行首次申购以启动组合。初始净值固定为 1.0000
          </AlertDescription>
        </Alert>
      )}

      {/* 快照停更可见提示（#906 SPEC §4，与桌面同构）：trailing 缺口非空才渲染 */}
      {!isLedger && !isDraft && <SnapshotCatchupAlert portfolioCode={code} />}

      {isLedger ? (
        /* 台账型独立视图（issue #905 PR3）：操作区（激活/删除）+ LedgerContent；无净值/份额/投资人 Tab */
        <>
          {isAdmin && actionButtons}
          <DeletePortfolioDialog
            open={isDeleteOpen}
            onOpenChange={setIsDeleteOpen}
            portfolioCode={portfolio.code}
            portfolioName={portfolio.name}
            returnFocusRef={deleteReturnFocusRef}
            isPending={deletePortfolio.isPending}
            onConfirm={() =>
              deletePortfolio.mutate(code, {
                onSuccess: () => router.push("/m/portfolio"),
                onSettled: () => setIsDeleteOpen(false),
              })
            }
          />
          <LedgerContent portfolio={portfolio} variant="mobile" />
        </>
      ) : showInvestors ? (
        /* ?tab=investors 投资人视图（draft 也允许）；占比用后端 ratio，不再传 totalShares（#693） */
        <PortfolioInvestorsList investors={investors} variant="mobile" />
      ) : (
        <>
          {/* Stats Cards */}
          {!isDraft && (
            <PortfolioStatsCards
              totalValue={portfolio.total_value ?? snapshot?.total_value ?? null}
              unitPrice={snapshot?.unit_price ?? null}
              totalProfit={portfolio.total_profit ?? null}
              cumulativeReturn={returns?.cumulative_return ?? null}
              dailyReturn={dailyReturn}
              holdingDays={performance?.holding_days ?? null}
              variant="mobile"
            />
          )}

          {/* 入流操作区（#937）：主动作 +「更多」Sheet，单层网格由组件内部控制 */}
          {actionButtons}
          {/* 删除空壳确认（#820 扩围，与桌面端共用弹窗） */}
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
                onSuccess: () => router.push("/m/portfolio"),
                onSettled: () => setIsDeleteOpen(false),
              })
            }
          />

          {!isDraft && (
            <>
              {/* 资产分布 */}
              <Card>
                <CardContent className="p-4">
                  <h3 className="mb-3 text-sm font-medium">资产分布</h3>
                  <AssetAllocationPie items={allocation} height={150} />
                </CardContent>
              </Card>

              {/* 持仓明细：按产品/按平台双视图（#595，URL ?view= 持久化）；
                  二级组合键子集优先取组合级 display_config（issue #144/#915） */}
              <PortfolioHoldings
                portfolioCode={code}
                basePath="/m/portfolio"
                assetClasses={assetClasses}
                displayConfig={portfolio.display_config}
                dimensionRules={assetClassDict?.dimension_rules}
                view={view}
                onViewChange={handleViewChange}
                subDimOverrides={subDimOverrides}
                onSubDimToggle={handleSubDimToggle}
                variant="mobile"
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

              {/* #649 起双端共享组件：移动端同得 4 个区间 chips；卡恒在（抽取前空数据
                  整卡不渲染），加载期出占位 spinner、空数据出卡内空态，与桌面一致 */}
              <PortfolioNavTrendCard code={code} variant="mobile" />

              {/* 绩效指标（紧凑两列） */}
              <PerformanceMetrics data={performance} variant="mobile" />
            </>
          )}
        </>
      )}
    </div>
  );

  return (
    <>
      {detailContent}
      <BackToTop returnFocusSelector='[data-testid="page-header"] a[href]' />
    </>
  );
}

/**
 * 移动端组合详情页（issue #99 单列同构；#595 持仓双视图 + ?view= 持久化 + #943 统一操作菜单）。
 * useSearchParams 需包 Suspense 边界（Next 15 静态预渲染要求）。
 */
export default function MobilePortfolioDetailPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <MobilePortfolioDetailInner />
    </Suspense>
  );
}
