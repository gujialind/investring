"use client";

import { useParams, useSearchParams, useRouter, usePathname } from "next/navigation";
import { Suspense, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ArrowLeft, Settings2 } from "lucide-react";
import Link from "next/link";
import {
  usePortfolio,
  useLatestSnapshot,
  usePositionList,
  usePortfolioInvestors,
  useActivatePortfolio,
  usePortfolioPerformance,
} from "@/hooks/usePortfolio";
import { useRoleCheck } from "@/hooks/useAuth";
import { useAssetClassifications } from "@/hooks/useAssetClassification";
import AssetAllocationPie from "@/components/charts/AssetAllocationPie";
import PortfolioStatsCards from "@/components/shared/PortfolioStatsCards";
import PerformanceMetrics from "@/components/shared/PerformanceMetrics";
import PortfolioActionButtons from "@/components/shared/PortfolioActionButtons";
import PortfolioInvestorsList from "@/components/shared/PortfolioInvestorsList";
import PortfolioHoldings from "@/components/shared/PortfolioHoldings";
import ManageLinksCard from "@/components/shared/ManageLinksCard";
import PortfolioNavTrendCard from "@/components/shared/PortfolioNavTrendCard";
import DisplayConfigDialog from "@/components/shared/dialogs/DisplayConfigDialog";
import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import { buildAllocation } from "@/lib/allocation";
import { parseHoldingsView, type HoldingsView } from "@/types/holding";

function MobilePortfolioDetailInner() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const code = params.code as string;
  const showInvestors = searchParams.get("tab") === "investors";
  // 持仓明细视图（#595 D-7）：URL ?view=product|platform，默认 product（省略参数）
  const view = parseHoldingsView(searchParams.get("view"));
  const handleViewChange = (next: HoldingsView) => {
    const sp = new URLSearchParams(searchParams.toString());
    if (next === "product") sp.delete("view");
    else sp.set("view", next);
    const qs = sp.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

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

              {/* 持仓明细：按产品/按平台双视图（#595，URL ?view= 持久化）；
                  二级分组维度优先取组合级 display_config（issue #144） */}
              <PortfolioHoldings
                portfolioCode={code}
                basePath="/m/portfolio"
                assetClasses={assetClasses}
                displayConfig={portfolio.display_config}
                view={view}
                onViewChange={handleViewChange}
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

          {/* 页尾「管理」列表（#595 抽取共享卡，桌面端同卡） */}
          <ManageLinksCard basePath="/m/portfolio" code={code} />
        </>
      )}
    </div>
  );
}

/**
 * 移动端组合详情页（issue #99 单列同构；#595 持仓双视图 + ?view= 持久化 + 管理卡共享）。
 * useSearchParams 需包 Suspense 边界（Next 15 静态预渲染要求）。
 */
export default function MobilePortfolioDetailPage() {
  return (
    <Suspense fallback={<LoadingState />}>
      <MobilePortfolioDetailInner />
    </Suspense>
  );
}
