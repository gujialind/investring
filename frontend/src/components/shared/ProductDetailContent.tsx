"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQueries } from "@tanstack/react-query";
import { ArrowLeft, ChevronRight } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import NavCurve from "@/components/charts/NavCurve";
import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import { productApi, type NavAnalysisRange, type NavHistoryItem } from "@/lib/api";
import { TRADE_DIRECTION_COLORS } from "@/lib/colors";
import {
  formatCurrency,
  formatDate,
  formatMarketName,
  formatNav,
  formatReturnRate,
  formatSharesUnit,
  formatSignedCurrency,
  getReturnColorClass,
  getSignedReturn,
  getStatusBadgeVariant,
  getTradeStatusLabel,
} from "@/lib/utils";
import { useHoldingsByProduct } from "@/hooks/usePosition";
import { useNavAnalysis } from "@/hooks/useProduct";
import { useTradeList } from "@/hooks/useTrade";
import { usePlatformList } from "@/hooks/usePlatform";

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

const HISTORY_PAGE_SIZE = 5;

const TRADE_TYPE_LABELS: Record<string, string> = { buy: "买入", sell: "卖出" };

/** MM/DD 短日期（「最新收益(09/25)」标签，§12 注记；与持仓卡同一惯例） */
function shortMmDd(dateStr: string): string {
  return dateStr.slice(5, 10).replace("-", "/");
}

interface ProductDetailContentProps {
  /** 链接前缀：桌面 "/portfolio"，移动 "/m/portfolio" */
  basePath: string;
  variant: "desktop" | "mobile";
}

/**
 * #595 步骤③ 持仓产品详情页（M3/D3，双端共享）：
 * 概览卡 → 操作行（买入/卖出/事件，trades 产品预填）→ 平台分布 → 累计净值走势 →
 * 区间收益率（六窗）→ 历史净值（首屏 5 行 + 查看更多）→ 交易记录（该产品跨平台）。
 * 数据源：holdings/by-product 行 + platforms 切片（#635）、nav-analysis/nav-history
 * （#637）、trades 列表 product_code+market 过滤（D-8 既有参数）。
 * 平台分布行点击 → 平台-产品详情页由步骤④接线，本步骤渲染为纯展示行。
 */
export default function ProductDetailContent({ basePath, variant }: ProductDetailContentProps) {
  const params = useParams();
  const portfolioCode = params.code as string;
  const productCode = params.productCode as string;
  const market = params.market as string;
  const isMobile = variant === "mobile";

  const { data: holdings, isLoading: holdingsLoading, isError: holdingsError } =
    useHoldingsByProduct(portfolioCode);
  const product = holdings?.products.find(
    (p) => p.product_code === productCode && p.market === market
  );

  const [range, setRange] = useState<NavAnalysisRange>("6m");
  const { data: analysis } = useNavAnalysis(productCode, market, range);

  // 历史净值「首屏 5 行 + 查看更多」：按页并行查询后按页序拼接（keepPreviousData
  // 的单页 hook 不适合追加式加载；页间数据由后端分页契约保证不重叠）
  const [visiblePages, setVisiblePages] = useState(1);
  const historyQueries = useQueries({
    queries: Array.from({ length: visiblePages }, (_, i) => ({
      queryKey: ["products", "nav-history", productCode, market, i + 1, HISTORY_PAGE_SIZE],
      queryFn: () =>
        productApi.getNavHistory(productCode, market, { page: i + 1, page_size: HISTORY_PAGE_SIZE }),
      staleTime: 5 * 60 * 1000,
    })),
  });
  const historyItems: NavHistoryItem[] = historyQueries.flatMap((q) => q.data?.items ?? []);
  const historyTotal = historyQueries[0]?.data?.total ?? 0;
  const latestPrice = historyItems[0];

  const { data: tradesData } = useTradeList({
    portfolio_code: portfolioCode,
    product_code: productCode,
    market,
    page: 1,
    page_size: 5,
  });
  const { data: platformsData } = usePlatformList({ page_size: 100 });
  const platformNameMap = useMemo(
    () => new Map((platformsData?.items ?? []).map((p) => [p.code, p.name])),
    [platformsData]
  );

  if (holdingsLoading) {
    return <LoadingState />;
  }
  // #638 L2 S1：请求失败不得渲染成「已清仓」空态（#214 惯例，同 PortfolioHoldings）
  // #638 L2 S1：请求失败不得渲染成「已清仓」空态（#214 惯例，同 PortfolioHoldings 文案）
  if (holdingsError) {
    return (
      <div className="py-8 text-center text-muted-foreground">
        加载失败，请刷新重试
      </div>
    );
  }
  if (!product) {
    return (
      <EmptyState
        message="未找到该产品持仓"
        description="产品可能已清仓或不属于当前组合（§6 过滤：已清仓产品不展示）"
      />
    );
  }

  const snapshotDate = holdings?.snapshot_date;
  const tradesLink = `${basePath}/${portfolioCode}/trades?product=${encodeURIComponent(productCode)}&market=${encodeURIComponent(market)}`;

  const overview = (
    <Card data-testid="product-overview-card">
      <CardContent className="pt-4">
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>持仓市值</span>
          <span>最新快照 {snapshotDate ? formatDate(snapshotDate) : "--"}</span>
        </div>
        <div className="mt-1 text-2xl font-semibold number-cell">
          {formatCurrency(product.market_value)}
        </div>
        {latestPrice && (
          <div className="mt-1 text-sm text-muted-foreground">
            单位净值 {formatNav(latestPrice.unit_price)}
            {latestPrice.accumulated_nav !== null &&
              ` · 累计净值 ${formatNav(latestPrice.accumulated_nav)}`}
            {` (${shortMmDd(formatDate(latestPrice.price_date))})`}
          </div>
        )}
        <div className={`mt-3 grid gap-3 ${isMobile ? "grid-cols-2" : "grid-cols-4"}`}>
          <div>
            <div className="text-sm text-muted-foreground">持有份额</div>
            <div className="text-sm font-medium tabular-nums">
              {formatSharesUnit(product.shares)}
            </div>
          </div>
          <div>
            <div className="text-sm text-muted-foreground">持有收益</div>
            <div className={`flex flex-wrap items-baseline gap-x-1 text-sm font-medium tabular-nums ${getReturnColorClass(product.holding_profit)}`}>
              <span>{formatSignedCurrency(product.holding_profit)}</span>
              {product.holding_profit_percent !== null &&
                product.holding_profit_percent !== undefined && (
                  <span className="whitespace-nowrap">({formatReturnRate(product.holding_profit_percent)})</span>
                )}
            </div>
          </div>
          <div>
            <div className="text-sm text-muted-foreground">累计收益*</div>
            <div className={`text-sm font-medium tabular-nums ${getReturnColorClass(product.cumulative_profit)}`}>
              {formatSignedCurrency(product.cumulative_profit)}
            </div>
          </div>
          <div>
            <div className="text-sm text-muted-foreground">
              最新收益{snapshotDate ? `(${shortMmDd(formatDate(snapshotDate))})` : ""}
            </div>
            <div className={`text-sm font-medium tabular-nums ${getReturnColorClass(product.daily_profit)}`}>
              {formatSignedCurrency(product.daily_profit)}
            </div>
          </div>
        </div>
        <p className="mt-2 text-xs text-warning">
          *累计收益含已卖出实现盈亏，与持仓卡「累计收益」（持有收益口径）不同
        </p>
      </CardContent>
    </Card>
  );

  const actionRow = (
    <div className="flex gap-2" data-testid="product-action-row">
      <Button asChild className="flex-1">
        <Link href={`${tradesLink}&trade_type=buy`}>买入</Link>
      </Button>
      <Button asChild variant="outline" className="flex-1">
        <Link href={`${tradesLink}&trade_type=sell`}>卖出</Link>
      </Button>
      <Button asChild variant="outline" className="flex-1">
        <Link href={tradesLink}>事件</Link>
      </Button>
    </div>
  );

  const platformCard = (
    <section className="rounded-lg border border-border bg-card p-4" data-testid="product-platform-card">
      <h3 className="text-base font-semibold">平台分布</h3>
      {product.platforms.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">暂无平台持仓</p>
      ) : (
        <ul className="mt-2 space-y-2">
          {product.platforms.map((slice) => (
            <li
              key={slice.platform_code ?? "unknown"}
              data-testid="product-platform-row"
              className="flex items-center justify-between gap-2 rounded-md border border-border px-3 py-2"
            >
              <div className="min-w-0">
                <div className="truncate text-sm font-medium">
                  {slice.platform_name || slice.platform_code || "--"}
                </div>
                <div className="text-sm text-muted-foreground tabular-nums">
                  {formatSharesUnit(slice.shares)}
                </div>
                <div className="text-xs tabular-nums">
                  <span className={getReturnColorClass(slice.holding_profit)}>
                    持有 <span className="whitespace-nowrap">{formatSignedCurrency(slice.holding_profit)}</span>
                  </span>
                  <span className="text-muted-foreground">
                    {" · 累计 "}
                    <span className="whitespace-nowrap">{formatSignedCurrency(slice.cumulative_profit)}</span>
                  </span>
                </div>
              </div>
              <div className="flex shrink-0 items-center gap-1">
                <div className="text-right">
                  <div className="text-sm font-medium number-cell">
                    {formatCurrency(slice.market_value)}
                  </div>
                  <div className="text-xs text-muted-foreground number-cell">
                    {slice.ratio_in_product !== null && slice.ratio_in_product !== undefined
                      ? `${(slice.ratio_in_product * 100).toFixed(1)}%`
                      : "--"}
                  </div>
                </div>
                <ChevronRight className="h-4 w-4 text-muted-foreground" aria-hidden />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );

  const curveCard = (
    <section className="rounded-lg border border-border bg-card p-4" data-testid="product-curve-card">
      <h3 className="text-base font-semibold">累计净值走势</h3>
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
      <div className="mt-2">
        <NavCurve data={(analysis?.curve ?? []).map((p) => ({ date: p.date, nav: p.accumulated_nav }))} height={isMobile ? 220 : 300} />
      </div>
    </section>
  );

  const returnsCard = (
    <section className="rounded-lg border border-border bg-card p-4" data-testid="product-returns-card">
      <h3 className="text-base font-semibold">区间收益率</h3>
      <div className="mt-3 grid grid-cols-3 gap-3">
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
    </section>
  );

  const historyCard = (
    <section className="rounded-lg border border-border bg-card p-4" data-testid="product-history-card">
      <h3 className="text-base font-semibold">历史净值</h3>
      {historyItems.length === 0 ? (
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
                  data-testid="product-history-row"
                  className="grid grid-cols-4 border-t border-border py-1.5 text-sm tabular-nums"
                >
                  <span>{formatDate(item.price_date)}</span>
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

  const tradesCard = (
    <section className="rounded-lg border border-border bg-card p-4" data-testid="product-trades-card">
      <div className="flex items-center justify-between">
        <h3 className="text-base font-semibold">交易记录</h3>
        <Link href={tradesLink} className="text-sm text-primary hover:underline">
          查看全部
        </Link>
      </div>
      {(tradesData?.items ?? []).length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">暂无交易记录</p>
      ) : (
        <ul className="mt-2">
          {(tradesData?.items ?? []).map((trade) => (
            <li
              key={trade.id}
              data-testid="product-trade-row"
              className="flex items-center justify-between gap-2 border-t border-border py-2 first:border-t-0"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-sm">
                  <span
                    className="h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ background: TRADE_DIRECTION_COLORS[trade.trade_type === "buy" ? "buy" : "sell"] }}
                  />
                  <span className="truncate">
                    {TRADE_TYPE_LABELS[trade.trade_type] ?? trade.trade_type}
                    {" · "}
                    {trade.platform_code
                      ? (platformNameMap.get(trade.platform_code) ?? trade.platform_code)
                      : "--"}
                  </span>
                </div>
                <div className="text-xs text-muted-foreground tabular-nums">
                  {trade.shares !== undefined && `${formatSharesUnit(trade.shares)} @ ${formatNav(trade.price)} · `}
                  {formatDate(trade.trade_date)}
                </div>
              </div>
              <div className="flex shrink-0 flex-col items-end gap-0.5">
                <span className="text-sm font-medium number-cell">
                  {formatCurrency(trade.amount)}
                </span>
                <Badge variant={getStatusBadgeVariant(trade.status)}>
                  {getTradeStatusLabel(trade.status)}
                </Badge>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );

  return (
    <div className={isMobile ? "space-y-3 p-3" : "space-y-4 p-6"}>
      <div className="flex items-center gap-2">
        <Button asChild variant="ghost" size="icon" aria-label="返回">
          <Link href={`${basePath}/${portfolioCode}`}>
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <div>
          <h1 className="text-lg font-semibold">{product.product_name || product.product_code}</h1>
          <p className="text-sm text-muted-foreground">
            {product.product_code} · {formatMarketName(product.market)}
          </p>
        </div>
      </div>

      {overview}
      {actionRow}

      {isMobile ? (
        <>
          {platformCard}
          {curveCard}
          {returnsCard}
          {historyCard}
          {tradesCard}
        </>
      ) : (
        <div className="grid grid-cols-[1fr_360px] gap-4">
          <div className="space-y-4">
            {curveCard}
            {returnsCard}
            {historyCard}
          </div>
          <div className="space-y-4">
            {platformCard}
            {tradesCard}
          </div>
        </div>
      )}
    </div>
  );
}
