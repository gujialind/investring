"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import NavCurve from "@/components/charts/NavCurve";
import { type NavAnalysisRange } from "@/lib/api";
import { navBasisLabels } from "@/lib/navBasis";
import {
  cn,
  formatDate,
  formatNav,
  formatReturnRate,
  getReturnColorClass,
  getSignedReturn,
} from "@/lib/utils";
import { useNavAnalysis, useNavHistory } from "@/hooks/useProduct";
import QueryErrorState from "./QueryErrorState";

/** 净值曲线区间 Tab（M3/D3：近1月/近3月/近6月/近1年，默认近6月） */
const NAV_RANGE_TABS: { key: NavAnalysisRange; label: string }[] = [
  { key: "1m", label: "近1月" },
  { key: "3m", label: "近3月" },
  { key: "6m", label: "近6月" },
  { key: "1y", label: "近1年" },
];

/** 六窗区间收益率展示序（label, 字段） */
const RETURN_WINDOWS: { label: string; field: "m1" | "m3" | "m6" | "y1" | "ytd" | "all" }[] = [
  { label: "近1月", field: "m1" },
  { label: "近3月", field: "m3" },
  { label: "近6月", field: "m6" },
  { label: "近1年", field: "y1" },
  { label: "今年以来", field: "ytd" },
  { label: "成立以来", field: "all" },
];

interface ProductNavCardsProps {
  productCode: string;
  /** 非空市场维度；现金产品（market=""）由调用方决定是否渲染本组件 */
  market: string;
  variant: "desktop" | "mobile";
  /** 可选 testid 前缀，缺省 "product"；平台-产品页传 "platform-product"，保持既有 E2E 定位契约 */
  testIdPrefix?: string;
}

/**
 * #824 产品净值三卡（累计净值走势 / 区间收益率 / 历史净值）——从 ProductDetailContent 逐字抽出，
 * 供组合详情-产品详情页、产品管理-净值型产品详情页与平台-产品详情页共用，消除同形副本漂移
 * （口径单点见 lib/navBasis.ts；#828 起平台页也改走本组件，testid 由 testIdPrefix 保持不串台）。
 * 三卡是纯 `(productCode, market)` 函数、无内部链接，故不依赖组合上下文与 basePath。
 * 现金产品无净值端点，调用方（!isCash）不渲染本组件即隐藏三卡。
 * 数据源：nav-analysis（曲线 + 六窗，#637）、nav-history 分页（首屏 5 行 + 查看更多）。
 * 返回三卡的 Fragment，由调用方容器负责栅格与间距（桌面左栏 space-y-4、移动 space-y-3）。
 */
export default function ProductNavCards({
  productCode,
  market,
  variant,
  testIdPrefix = "product",
}: ProductNavCardsProps) {
  const isMobile = variant === "mobile";

  const [range, setRange] = useState<NavAnalysisRange>("6m");
  const {
    data: analysis,
    isError: analysisError,
    error: analysisErr,
    refetch: refetchAnalysis,
    // #683 D-1：换区间在途时 react-query 用 placeholder 保留**上一个区间**的值
    // （keepPreviousData），此时 isRefetching 为真而 isError 仍为假。库自带具名字段，
    // 首屏在途为假（isPending 时 isRefetching=false），故不必再造派生谓词。
    isRefetching: analysisRevalidating,
  } = useNavAnalysis(productCode, market, range);
  // #703：整段历史无累计净值时后端回退单位净值口径，标题与注记随 basis 变（单点见 lib/navBasis.ts）
  const navLabels = navBasisLabels(analysis?.nav_basis, market);

  // 历史净值「首屏 5 行 + 查看更多」：按页并行查询后按页序拼接（见 useNavHistory）
  const [visiblePages, setVisiblePages] = useState(1);
  const {
    items: historyItems,
    total: historyTotal,
    // #647：历史净值请求失败与真空态必须可区分——失败走失败态 + 重试，
    // 不得落进「暂无净值数据」空态分支（静默失败会把后端故障读成产品无历史）
    isError: historyError,
    error: historyErr,
    refetch: refetchHistory,
  } = useNavHistory(productCode, market, visiblePages, !!market);

  const curveCard = (
    <section
      className="relative rounded-lg border border-border bg-card p-4"
      data-testid={`${testIdPrefix}-curve-card`}
      // #683 D-1：重取期（含 placeholder 生效的换区间在途）标为 busy。断言按属性而非
      // opacity 类名——定位器契约禁止按 Tailwind 工具类定位（手法同 #650 的 aria-current）
      aria-busy={analysisRevalidating ? "true" : "false"}
    >
      <h3 className="text-lg font-semibold">{navLabels.curveTitle}</h3>
      {navLabels.note && (
        <p className="mt-1 text-xs text-muted-foreground" data-testid="nav-basis-note">
          {navLabels.note}
        </p>
      )}
      {analysisRevalidating && (
        <Loader2 className="absolute right-3 top-3 z-10 h-4 w-4 animate-spin text-muted-foreground" />
      )}
      <div className="mt-2 flex gap-1" role="group" aria-label="净值区间">
        {NAV_RANGE_TABS.map((tab) => (
          <button
            key={tab.key}
            type="button"
            aria-pressed={range === tab.key}
            data-testid={`nav-range-${tab.key}`}
            className={`rounded-md px-3 py-1 text-sm ${
              range === tab.key
                ? "bg-primary/10 text-primary font-medium"
                : "text-muted-foreground hover:bg-muted"
            }`}
            onClick={() => setRange(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {/* #683 D-2：失败 ≠「这个区间没净值」。失败臂必须排在 NavCurve 之前，否则后端故障被
          NavCurve 的内置空态文案吞掉。
          D-1：重取期保留 placeholder 的**上一个区间**数值，但整块压暗 + 右上角 spinner
          （docs/design/visual-spec.md §14 局部加载态），不得为消除该观感而删 keepPreviousData。 */}
      {analysisError ? (
        <QueryErrorState error={analysisErr} onRetry={refetchAnalysis} className="mt-3" />
      ) : (
        <div className={cn("mt-2", analysisRevalidating && "opacity-50")}>
          <NavCurve data={(analysis?.curve ?? []).map((p) => ({ date: p.date, nav: p.accumulated_nav }))} height={isMobile ? 220 : 300} />
        </div>
      )}
    </section>
  );

  const returnsCard = (
    <section
      className="relative rounded-lg border border-border bg-card p-4"
      data-testid={`${testIdPrefix}-returns-card`}
      aria-busy={analysisRevalidating ? "true" : "false"}
    >
      <h3 className="text-lg font-semibold">{navLabels.returnsTitle}</h3>
      {navLabels.note && (
        <p className="mt-1 text-xs text-muted-foreground" data-testid="returns-basis-note">
          {navLabels.note}
        </p>
      )}
      {analysisRevalidating && (
        <Loader2 className="absolute right-3 top-3 z-10 h-4 w-4 animate-spin text-muted-foreground" />
      )}
      {/* 失败臂整块替换六窗（不是塞进 grid 子位）：失败时六个 return-<field> 都不存在，
          「全为 --」的形态随之消失，后端故障不再被读成「这些窗口没有净值」 */}
      {analysisError ? (
        <QueryErrorState error={analysisErr} onRetry={refetchAnalysis} className="mt-3" />
      ) : (
        <div className={cn("mt-3 grid grid-cols-3 gap-3", analysisRevalidating && "opacity-50")}>
          {RETURN_WINDOWS.map((w) => {
            const value = analysis?.interval_returns?.[w.field];
            return (
              <div key={w.field} data-testid={`return-${w.field}`}>
                <div className={`text-sm font-medium tabular-nums ${getReturnColorClass(value)}`}>
                  {formatReturnRate(value)}
                </div>
                <div className="text-xs text-muted-foreground">{w.label}</div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );

  const historyCard = (
    <section className="rounded-lg border border-border bg-card p-4" data-testid={`${testIdPrefix}-history-card`}>
      <h3 className="text-lg font-semibold">历史净值</h3>
      {historyError ? (
        <QueryErrorState error={historyErr} onRetry={refetchHistory} className="mt-3" />
      ) : historyItems.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">暂无净值数据</p>
      ) : (
        <>
          <div className="mt-2 grid grid-cols-4 text-xs text-muted-foreground">
            <span>日期</span>
            <span className="text-right">单位净值</span>
            <span className="text-right">累计净值</span>
            <span className="text-right">日涨跌</span>
          </div>
          <ul>
            {historyItems.map((item) => {
              const pct = getSignedReturn(item.pct_change);
              return (
                <li
                  key={item.price_date}
                  data-testid={`${testIdPrefix}-history-row`}
                  className="grid grid-cols-4 border-t border-border py-1.5 text-sm tabular-nums"
                >
                  <span className="whitespace-nowrap">{formatDate(item.price_date)}</span>
                  <span className="text-right">{formatNav(item.unit_price)}</span>
                  <span className="text-right">
                    {item.accumulated_nav !== null ? formatNav(item.accumulated_nav) : "--"}
                  </span>
                  <span className={`text-right ${pct.colorClass}`}>{pct.text}</span>
                </li>
              );
            })}
          </ul>
          {historyItems.length < historyTotal && (
            <div className="mt-2 text-center">
              <Button
                variant="link"
                size="sm"
                data-testid="history-load-more"
                onClick={() => setVisiblePages((p) => p + 1)}
              >
                查看更多
              </Button>
            </div>
          )}
        </>
      )}
    </section>
  );

  return (
    <>
      {curveCard}
      {returnsCard}
      {historyCard}
    </>
  );
}
