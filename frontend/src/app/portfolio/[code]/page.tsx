"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import MainLayout from "@/components/layout/MainLayout";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { ArrowLeft, RefreshCw, Settings2 } from "lucide-react";
import Link from "next/link";
import {
  usePortfolio,
  useLatestSnapshot,
  usePortfolioInvestors,
  usePositionList,
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
import { toDateOnly } from "@/lib/utils";

/** 净值走势区间：近6月 / 近1年 / 近3年 / 成立以来 */
type NavRange = "6m" | "1y" | "3y" | "all";

const NAV_RANGES: { key: NavRange; label: string }[] = [
  { key: "6m", label: "近6个月" },
  { key: "1y", label: "近1年" },
  { key: "3y", label: "近3年" },
  { key: "all", label: "成立以来" },
];

/** 区间起点（原生 Date 计算，不引入日期库）；all → undefined（全量） */
function rangeStartDate(range: NavRange): string | undefined {
  if (range === "all") return undefined;
  const d = new Date();
  if (range === "6m") d.setMonth(d.getMonth() - 6);
  else if (range === "1y") d.setFullYear(d.getFullYear() - 1);
  else d.setFullYear(d.getFullYear() - 3);
  return toDateOnly(d);
}

function PortfolioDetailInner() {
  const params = useParams();
  const searchParams = useSearchParams();
  const code = params.code as string;
  const showInvestors = searchParams.get("tab") === "investors";

  const { data: portfolio, isLoading: portfolioLoading } = usePortfolio(code);
  const { data: snapshot, isLoading: snapshotLoading } = useLatestSnapshot(code);
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
  const activatePortfolio = useActivatePortfolio();
  const { isAdmin } = useRoleCheck();

  // 净值走势区间切换（issue #99）：start_date 按选中区间计算，「成立以来」全量
  const [navRange, setNavRange] = useState<NavRange>("all");
  const navParams = useMemo(() => {
    const start = rangeStartDate(navRange);
    return start ? { start_date: start } : undefined;
  }, [navRange]);
  const { data: navHistoryData } = useNavHistory(code, navParams);

  // 绩效指标（后端计算）：draft 组合无快照，不请求
  const isDraftStatus = portfolio?.status === "draft";
  const { data: performance } = usePortfolioPerformance(code, !isDraftStatus);

  // 分组维度配置弹窗（issue #144）
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
  const allocation = buildAllocation(positions, assetClasses);
  const navHistory = (navHistoryData || [])
    .filter((r) => r.unit_price !== null)
    .map((r) => ({ date: r.snapshot_date, nav: r.unit_price as number }));

  return (
    <MainLayout>
      <div className="space-y-6">
        {/* 页头：返回 / 名称+code+成立日期·状态小字 / 操作区 */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <Link href="/portfolio">
              <Button variant="ghost" size="sm">
                <ArrowLeft className="h-4 w-4" />
              </Button>
            </Link>
            <div>
              <h1 className="text-2xl font-semibold">{portfolio.name}</h1>
              <p className="text-muted-foreground">
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
          {isAdmin && (
            <div className="flex gap-2">
              <PortfolioActionButtons
                portfolioCode={code}
                status={portfolio.status as "draft" | "active" | "closed"}
                basePath="/portfolio"
                variant="desktop"
                onActivateClick={() => activatePortfolio.mutate(code)}
                isActivatePending={activatePortfolio.isPending}
              />
            </div>
          )}
        </div>

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
          /* 单列五段（draft 时仅第 1 段，其余不渲染） */
          <div className="space-y-4">
            {/* 1. 四项统计 + 最新快照日期小字 */}
            <div>
              <PortfolioStatsCards
                totalValue={!isDraft ? portfolio.total_value ?? snapshot?.total_value ?? null : null}
                unitPrice={!isDraft ? snapshot?.unit_price ?? null : null}
                totalProfit={!isDraft ? portfolio.total_profit ?? null : null}
                holdingDays={!isDraft ? performance?.holding_days ?? null : null}
                variant="desktop"
              />
              <p className="mt-2 text-xs text-muted-foreground">
                最新快照日期：{!isDraft ? snapshot?.snapshot_date || "--" : "--"}
              </p>
            </div>

            {!isDraft && (
              <>
                {/* 2. 资产分布（环形图 + 图例） */}
                <Card>
                  <CardContent className="pt-6">
                    <h3 className="mb-4 text-[15px] font-semibold">资产分布</h3>
                    <AssetAllocationPie items={allocation} />
                  </CardContent>
                </Card>

                {/* 3. 分类持仓分区（含在途资金独立卡片）；
                    二级分组维度优先取组合级 display_config（issue #144） */}
                <PositionSections
                  positions={positions}
                  assetClasses={assetClasses}
                  displayConfig={portfolio.display_config}
                  action={
                    isAdmin ? (
                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setDisplayConfigOpen(true)}
                        >
                          <Settings2 className="mr-2 h-4 w-4" />
                          分组维度
                        </Button>
                        <Link href={`/portfolio/${code}/positions`}>
                          <Button variant="outline" size="sm">
                            <RefreshCw className="mr-2 h-4 w-4" />
                            管理持仓
                          </Button>
                        </Link>
                      </div>
                    ) : undefined
                  }
                />
                <DisplayConfigDialog
                  open={displayConfigOpen}
                  onOpenChange={setDisplayConfigOpen}
                  portfolioCode={code}
                  currentConfig={portfolio.display_config}
                />

                {/* 4. 净值走势 + 区间 chips */}
                <Card>
                  <CardContent className="pt-6">
                    <div className="mb-2 flex items-center justify-between">
                      <h3 className="text-[15px] font-semibold">净值走势</h3>
                      <div className="flex gap-2">
                        {NAV_RANGES.map((r) => (
                          <button
                            key={r.key}
                            onClick={() => setNavRange(r.key)}
                            className={`rounded-full px-3.5 py-1.5 text-[13px] transition-colors ${
                              navRange === r.key
                                ? "bg-primary font-semibold text-primary-foreground"
                                : "bg-muted text-muted-foreground hover:bg-accent"
                            }`}
                          >
                            {r.label}
                          </button>
                        ))}
                      </div>
                    </div>
                    {navHistory.length > 0 ? (
                      <NavCurve data={navHistory} initialNav={1.0} />
                    ) : (
                      <div className="flex h-[300px] items-center justify-center text-muted-foreground">
                        该区间暂无净值数据
                      </div>
                    )}
                  </CardContent>
                </Card>

                {/* 5. 绩效指标（6 项） */}
                <PerformanceMetrics data={performance} variant="desktop" />
              </>
            )}
          </div>
        )}
      </div>
    </MainLayout>
  );
}

/**
 * 组合详情页（issue #99）：单列五段 + ?tab=investors 投资人视图。
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
