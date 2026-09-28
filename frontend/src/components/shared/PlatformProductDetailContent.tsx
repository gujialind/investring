"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useQueries } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import NavCurve from "@/components/charts/NavCurve";
import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import PlatformDistributionCard from "./PlatformDistributionCard";
import { productApi, type NavAnalysisRange, type NavHistoryItem } from "@/lib/api";
import { TRADE_DIRECTION_COLORS } from "@/lib/colors";
import { CASH_PRODUCT_CODE } from "@/lib/allocation";
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

const NAV_RANGE_TABS: { key: NavAnalysisRange; label: string }[] = [
  { key: "1m", label: "近1月" },
  { key: "3m", label: "近3月" },
  { key: "6m", label: "近6月" },
  { key: "1y", label: "近1年" },
];

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

function shortMmDd(dateStr: string): string {
  return dateStr.slice(5, 10).replace("-", "/");
}

interface PlatformProductDetailContentProps {
  basePath: string;
  variant: "desktop" | "mobile";
}

/**
 * #595 步骤④ 平台-产品详情页（M5/D5，双端共享）：
 * 概览卡（该平台切片市值/份额/收益/占产品比）→ 操作行（trades 平台+产品预填）→
 * 净值走势 + 区间收益率 + 历史净值 → 交易记录（该产品在该平台）→
 * 「查看该产品全部平台持仓」链接 → 产品详情页。
 * 数据源：holdings/by-product 行的 platforms 切片 + nav-analysis/nav-history + trades。
 */
export default function PlatformProductDetailContent({
  basePath,
  variant,
}: PlatformProductDetailContentProps) {
  const params = useParams();
  const searchParams = useSearchParams();
  const portfolioCode = params.code as string;
  const platformCode = params.platformCode as string;
  const productCode = params.productCode as string;
  const market = searchParams.get("market") ?? "";
  const isMobile = variant === "mobile";
  const isCash = productCode === CASH_PRODUCT_CODE;

  const { data: holdings, isLoading: holdingsLoading, isError: holdingsError } =
    useHoldingsByProduct(portfolioCode);
  const product = holdings?.products.find(
    (p) => p.product_code === productCode && p.market === market
  );
  const slice = product?.platforms.find((s) => s.platform_code === platformCode);

  const [range, setRange] = useState<NavAnalysisRange>("6m");
  const { data: analysis } = useNavAnalysis(productCode, market, range);

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
    market: market || undefined,
    platform_code: platformCode,
    page: 1,
    page_size: 5,
  });

  if (holdingsLoading) return <LoadingState />;
  if (holdingsError) {
    return (
      <div className="py-8 text-center text-muted-foreground">
        加载失败，请刷新重试
      </div>
    );
  }
  if (!product || !slice) {
    return (
      <EmptyState
        message="未找到该平台产品持仓"
        description="该产品可能不在当前平台持仓或不属于当前组合"
      />
    );
  }

  const snapshotDate = holdings?.snapshot_date;
  const tradesLink = `${basePath}/${portfolioCode}/trades?product=${encodeURIComponent(productCode)}&market=${encodeURIComponent(market)}&platform=${encodeURIComponent(platformCode)}`;
  const productDetailLink = `${basePath}/${portfolioCode}/product/${encodeURIComponent(market)}/${encodeURIComponent(productCode)}`;

  const overview = (
    <Card data-testid="platform-product-overview-card">
      <CardContent className="pt-4">
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>持仓市值</span>
          <span>最新快照 {snapshotDate ? formatDate(snapshotDate) : "--"}</span>
        </div>
        <div className="mt-1 text-2xl font-semibold number-cell">
          {formatCurrency(slice.market_value)}
        </div>
        {!isCash && latestPrice && (
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
              {isCash ? "--" : formatSharesUnit(slice.shares)}
            </div>
          </div>
          <div>
            <div className="text-sm text-muted-foreground">持有收益</div>
            <div className={`text-sm font-medium tabular-nums ${getReturnColorClass(slice.holding_profit)}`}>
              {formatSignedCurrency(slice.holding_profit)}
            </div>
          </div>
          <div>
            <div className="text-sm text-muted-foreground">累计收益</div>
            <div className={`text-sm font-medium tabular-nums ${getReturnColorClass(slice.cumulative_profit)}`}>
              {formatSignedCurrency(slice.cumulative_profit)}
            </div>
          </div>
          <div>
            <div className="text-sm text-muted-foreground">占该产品比</div>
            <div className="text-sm font-medium tabular-nums">
              {slice.ratio_in_product !== null && slice.ratio_in_product !== undefined
                ? `${(slice.ratio_in_product * 100).toFixed(1)}%`
                : "--"}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );

  const actionRow = isCash ? (
    <div className="flex gap-2" data-testid="platform-product-action-row">
      <Button asChild className="flex-1">
        <Link href={tradesLink}>转入</Link>
      </Button>
      <Button asChild variant="outline" className="flex-1">
        <Link href={tradesLink}>转出</Link>
      </Button>
    </div>
  ) : (
    <div className="flex gap-2" data-testid="platform-product-action-row">
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

  // 净值相关卡片仅非现金产品显示
  const curveCard = !isCash && (
    <section className="rounded-lg border border-border bg-card p-4" data-testid="platform-product-curve-card">
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

  const returnsCard = !isCash && (
    <section className="rounded-lg border border-border bg-card p-4" data-testid="platform-product-returns-card">
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

  const historyCard = !isCash && (
    <section className="rounded-lg border border-border bg-card p-4" data-testid="platform-product-history-card">
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
                  data-testid="platform-product-history-row"
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

  const tradesCard = (
    <section className="rounded-lg border border-border bg-card p-4" data-testid="platform-product-trades-card">
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
              data-testid="platform-product-trade-row"
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
                  </span>
                </div>
                <div className="whitespace-nowrap text-xs text-muted-foreground tabular-nums">
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

  // 「查看该产品全部平台持仓」— 非现金产品才有产品详情页
  const allPlatformsLink = !isCash && product.platforms.length > 0 && (
    <Link
      href={productDetailLink}
      className="text-sm text-primary hover:underline"
      data-testid="view-all-platforms-link"
    >
      查看该产品全部平台持仓
    </Link>
  );

  // 全部平台持仓卡（D5 右栏 / M5 底部）
  const allPlatformsCard = !isCash && product.platforms.length > 1 && (
    <PlatformDistributionCard
      title="全部平台持仓"
      slices={product.platforms}
      rowLinkPrefix={(s) =>
        s.platform_code && s.platform_code !== platformCode
          ? `${basePath}/${portfolioCode}/platforms/${s.platform_code}/products/${encodeURIComponent(productCode)}?market=${encodeURIComponent(market)}`
          : undefined
      }
      testId="platform-product-all-platforms-card"
    />
  );

  return (
    <div className={isMobile ? "space-y-3 p-3" : "space-y-4 p-6"}>
      <div className="flex items-center gap-2">
        <Button asChild variant="ghost" size="icon" aria-label="返回">
          <Link href={`${basePath}/${portfolioCode}/platforms/${platformCode}`}>
            <ArrowLeft className="h-4 w-4" />
          </Link>
        </Button>
        <div>
          <h1 className="text-lg font-semibold">
            {slice.platform_name || platformCode} · {product.product_name || productCode}
          </h1>
          <p className="text-sm text-muted-foreground">
            {productCode}{market ? ` · ${formatMarketName(market)}` : ""}
          </p>
        </div>
      </div>

      {overview}
      {actionRow}
      {allPlatformsLink && <div>{allPlatformsLink}</div>}

      {isMobile ? (
        <>
          {curveCard}
          {returnsCard}
          {historyCard}
          {tradesCard}
          {allPlatformsCard}
        </>
      ) : (
        <div className="grid grid-cols-[1fr_360px] gap-4">
          <div className="space-y-4">
            {curveCard}
            {returnsCard}
            {historyCard}
          </div>
          <div className="space-y-4">
            {tradesCard}
            {allPlatformsCard}
          </div>
        </div>
      )}
    </div>
  );
}
