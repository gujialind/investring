"use client";

import { useMemo, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import NavCurve from "@/components/charts/NavCurve";
import LoadingState from "@/components/shared/LoadingState";
import QueryErrorState from "@/components/shared/QueryErrorState";
import { useNavHistory } from "@/hooks/usePortfolio";
import { toDateOnly } from "@/lib/utils";

/** 净值走势区间：近6月 / 近1年 / 近3年 / 成立以来 */
type NavRange = "6m" | "1y" | "3y" | "all";

const NAV_RANGES: { key: NavRange; label: string }[] = [
  { key: "6m", label: "近6个月" },
  { key: "1y", label: "近1年" },
  { key: "3y", label: "近3年" },
  { key: "all", label: "成立以来" },
];

/** 区间起点（原生 Date 计算，不引入日期库）；all → undefined（全量，不传 start_date） */
function rangeStartDate(range: NavRange): string | undefined {
  if (range === "all") return undefined;
  const d = new Date();
  if (range === "6m") d.setMonth(d.getMonth() - 6);
  else if (range === "1y") d.setFullYear(d.getFullYear() - 1);
  else d.setFullYear(d.getFullYear() - 3);
  return toDateOnly(d);
}

interface PortfolioNavTrendCardProps {
  /** 组合代码 */
  code: string;
  variant?: "desktop" | "mobile";
}

/**
 * 组合净值走势卡（桌面/移动共用，#649）。
 *
 * 双端**同能力**：4 个区间 chips 两端都给（方案 B，维护者 2026-09-30 确认）——
 * 移动端此前无区间切换只是 #99 单端落地的遗留，不是产品决定。`variant` 只改
 * CardContent 内边距与曲线/空态/加载占位高度，不改口径：区间 state、`start_date` 归属、
 * `navHistory` 映射与空态文案都只在本组件持有一份。
 */
export default function PortfolioNavTrendCard({
  code,
  variant = "desktop",
}: PortfolioNavTrendCardProps) {
  // 区间切换（#99）：start_date 由本组件计算并持有，「成立以来」不传 params（全量）
  const [navRange, setNavRange] = useState<NavRange>("all");
  const navParams = useMemo(() => {
    const start = rangeStartDate(navRange);
    return start ? { start_date: start } : undefined;
  }, [navRange]);
  const {
    data: navHistoryData,
    isPending,
    isError,
    error,
    refetch,
  } = useNavHistory(code, navParams);
  const navHistory = (navHistoryData || [])
    .filter((r) => r.unit_price !== null)
    .map((r) => ({ date: r.snapshot_date, nav: r.unit_price as number }));

  const isMobile = variant === "mobile";
  const curveHeight = isMobile ? 200 : 300;

  return (
    <Card data-testid="portfolio-nav-trend-card">
      <CardContent className={isMobile ? "p-4" : "pt-6"}>
        {/* 窄栏（桌面右栏 360px / 移动整宽）放不下标题 + chips 时**整组换行**，
            组内 chips 不换行（shrink-0 + whitespace-nowrap）。不给容器加 overflow-x-auto：
            容器宽度随内容，横向永不溢出，加了只会让 overflow-y 算成 auto 而裁掉焦点环 */}
        <div className="mb-2 flex flex-wrap items-center justify-between gap-x-2 gap-y-1.5">
          <h3 className="shrink-0 text-sm font-medium">净值走势</h3>
          <div
            className="flex shrink-0 gap-2"
            role="group"
            aria-label="净值区间"
          >
            {NAV_RANGES.map((r) => (
              <button
                key={r.key}
                type="button"
                aria-pressed={navRange === r.key}
                data-testid={`nav-range-${r.key}`}
                onClick={() => setNavRange(r.key)}
                className={`shrink-0 whitespace-nowrap rounded-full px-3.5 py-1.5 text-xs transition-colors ${
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
        {isPending ? (
          /* 加载期（首屏与切区间）不写空态文案——「还没拿到数据」被说成「没有数据」是错的
             语义（移动端抽取前整卡不渲染，故这也是本次新增形态）。占位与曲线同高，
             避免换区间时布局跳动。 */
          <LoadingState height={`${curveHeight}px`} />
        ) : isError ? (
          /* 判序承重：失败必须排在「长度为 0」之前。不取 isError 时读取失败会直接落进
             下面的空态分支，把「取不到」说成「这个区间没有净值」——页面上无从区分
             （#663 第 5 处，本清单里形态最重的一处） */
          <QueryErrorState error={error} onRetry={() => void refetch()} className="mt-3" />
        ) : navHistory.length > 0 ? (
          <NavCurve data={navHistory} height={curveHeight} initialNav={1.0} />
        ) : (
          /* 空态文案单点（#649）：全量态与区间态措辞不同、同一状态双端同句。
             空数据不调 NavCurve——它内置另一句「暂无净值数据」，两套并存就会漂移 */
          <div
            className="flex items-center justify-center text-muted-foreground"
            style={{ height: curveHeight }}
          >
            {navRange === "all" ? "暂无净值数据" : "该区间暂无净值数据"}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
