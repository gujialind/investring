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
import { productApi, getErrorMessage, type NavAnalysisRange, type NavHistoryItem } from "@/lib/api";
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
import { usePlatformList } from "@/hooks/usePlatform";
import PlatformDistributionCard from "./PlatformDistributionCard";
import QueryErrorState from "./QueryErrorState";
import CashMarketValueUpdateDialog from "./dialogs/CashMarketValueUpdateDialog";
import CashTransferDialog from "./dialogs/CashTransferDialog";

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
 * 概览卡 → 操作行（非现金：买入/卖出/事件，trades 产品预填；现金：转入/转出 + 市值更新）→
 * 平台分布 → 累计净值走势 → 区间收益率（六窗）→ 历史净值（首屏 5 行 + 查看更多）→
 * 交易记录（该产品跨平台）。
 * 数据源：holdings/by-product 行 + platforms 切片（#635）、nav-analysis/nav-history
 * （#637）、trades 列表 product_code+market 过滤（D-8 既有参数）。
 * 路由 /portfolio/{code}/product/{productCode}，market 维度走 ?market= searchParams
 * （步骤⑤：现金 market="" 无 path 段可表达，与平台-产品页形态对齐）。
 * 平台分布行点击 → 平台-产品详情页（步骤④已接线，PlatformDistributionCard rowLinkPrefix）。
 * 现金产品（CASH）隐藏净值相关卡片，转入/转出打开现金转移 Dialog（聚合视角无平台上下文，
 * from/to 由用户选择），市值更新打开 CashMarketValueUpdateDialog。
 */
export default function ProductDetailContent({ basePath, variant }: ProductDetailContentProps) {
  const params = useParams();
  const searchParams = useSearchParams();
  const portfolioCode = params.code as string;
  const productCode = params.productCode as string;
  const isCash = productCode === CASH_PRODUCT_CODE;
  // R-8：现金归一 market=""——手改 ?market=CN_OTC 不得把净值请求与交易过滤带偏；
  // 非现金仍以 query 为准（缺失守卫见下方 EmptyState）
  const market = isCash ? "" : (searchParams.get("market") ?? "");
  const isMobile = variant === "mobile";

  // #595 §4.5/D-10：现金操作 Dialog 状态
  const [isCashUpdateOpen, setIsCashUpdateOpen] = useState(false);
  const [isCashTransferOpen, setIsCashTransferOpen] = useState(false);

  const { data: holdings, isLoading: holdingsLoading, isError: holdingsError, error: holdingsErr, refetch: refetchHoldings } =
    useHoldingsByProduct(portfolioCode);
  // 现金 market="" 无 path 段可表达：isCash 时以 productCode 单键匹配，忽略 market 维度
  const product = holdings?.products.find(
    (p) => p.product_code === productCode && (isCash || p.market === market)
  );

  const [range, setRange] = useState<NavAnalysisRange>("6m");
  const { data: analysis } = useNavAnalysis(productCode, market, range);

  // 历史净值「首屏 5 行 + 查看更多」：按页并行查询后按页序拼接（keepPreviousData
  // 的单页 hook 不适合追加式加载；页间数据由后端分页契约保证不重叠）
  const [visiblePages, setVisiblePages] = useState(1);
  // S5/S1：现金产品无净值端点（market="" 会 404），非现金且 market 非空时才请求
  const historyQueries = useQueries({
    queries: Array.from({ length: visiblePages }, (_, i) => ({
      queryKey: ["products", "nav-history", productCode, market, i + 1, HISTORY_PAGE_SIZE],
      queryFn: () =>
        productApi.getNavHistory(productCode, market, { page: i + 1, page_size: HISTORY_PAGE_SIZE }),
      staleTime: 5 * 60 * 1000,
      enabled: !isCash && !!market,
    })),
  });
  const historyItems: NavHistoryItem[] = historyQueries.flatMap((q) => q.data?.items ?? []);
  const historyTotal = historyQueries[0]?.data?.total ?? 0;
  const latestPrice = historyItems[0];
  // #647：历史净值请求失败与真空态必须可区分——失败走失败态 + 重试，
  // 不得落进「暂无净值数据」空态分支（静默失败会把后端故障读成产品无历史）
  const historyError = historyQueries.some((q) => q.isError);
  const historyErr = historyQueries.find((q) => q.isError)?.error ?? null;
  const refetchHistory = () => {
    historyQueries.forEach((q) => void q.refetch());
  };

  const { data: tradesData } = useTradeList({
    portfolio_code: portfolioCode,
    product_code: productCode,
    market: market || undefined,
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
  // #638 L2 S1：请求失败不得渲染成「已清仓」空态（#214 惯例，同 PortfolioHoldings 文案）
  if (holdingsError) {
    const msg = getErrorMessage(holdingsErr, "请刷新重试");
    return (
      <div className="py-8 text-center">
        <p className="text-muted-foreground">加载失败：{msg}</p>
        <Button variant="link" size="sm" onClick={() => refetchHoldings()}>
          重试
        </Button>
      </div>
    );
  }
  // R-4：非现金缺 market 单独提示（与平台-产品详情页 S4 同形态），避免把参数问题说成数据问题
  if (!isCash && !market) {
    return (
      <EmptyState
        message="缺少 market 参数"
        description="请从组合持仓或平台分布进入产品详情页"
      />
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

  // #595 §4.5/D-10：现金产品操作行为转入/转出 + 市值更新。
  // 转入/转出打开现金转移 Dialog（平台间 from→to 两腿显式落账；聚合视角无平台上下文，
  // from/to 均由用户选择），不走 trades 的 buy/sell（REST 禁止直接创建 CASH 交易）
  const actionRow = isCash ? (
    <div className="flex gap-2" data-testid="product-action-row">
      <Button className="flex-1" onClick={() => setIsCashTransferOpen(true)}>
        转入
      </Button>
      <Button variant="outline" className="flex-1" onClick={() => setIsCashTransferOpen(true)}>
        转出
      </Button>
      <Button variant="outline" className="flex-1" onClick={() => setIsCashUpdateOpen(true)}>
        市值更新
      </Button>
    </div>
  ) : (
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
    <PlatformDistributionCard
      title="平台分布"
      slices={product.platforms}
      rowLinkPrefix={(slice) =>
        slice.platform_code
          ? `${basePath}/${portfolioCode}/platforms/${slice.platform_code}/products/${encodeURIComponent(productCode)}?market=${encodeURIComponent(market)}`
          : undefined
      }
    />
  );

  // 净值相关卡片仅非现金产品显示
  const curveCard = !isCash && (
    <section className="rounded-lg border border-border bg-card p-4" data-testid="product-curve-card">
      <h3 className="text-lg font-semibold">累计净值走势</h3>
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
    <section className="rounded-lg border border-border bg-card p-4" data-testid="product-returns-card">
      <h3 className="text-lg font-semibold">区间收益率</h3>
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
    <section className="rounded-lg border border-border bg-card p-4" data-testid="product-history-card">
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
                  data-testid="product-history-row"
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
    <section className="rounded-lg border border-border bg-card p-4" data-testid="product-trades-card">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">交易记录</h3>
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

      {/* #595 §4.5：现金市值更新 Dialog（仅现金产品使用） */}
      {isCash && (
        <CashMarketValueUpdateDialog
          portfolioCode={portfolioCode}
          open={isCashUpdateOpen}
          onOpenChange={setIsCashUpdateOpen}
        />
      )}

      {/* #595 §4.5/D-10：现金转移 Dialog（仅现金产品使用；聚合视角无平台上下文，
          from/to 由用户选择，「转入」「转出」同一入口） */}
      {isCash && (
        <CashTransferDialog
          portfolioCode={portfolioCode}
          open={isCashTransferOpen}
          onOpenChange={setIsCashTransferOpen}
        />
      )}
    </div>
  );
}
