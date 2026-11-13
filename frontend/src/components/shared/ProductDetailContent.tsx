"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import { tradeDirectionColors } from "@/lib/colors";
import { useChartTheme } from "@/hooks/useChartTheme";
import { CASH_PRODUCT_CODE } from "@/lib/allocation";
import { cashRowLabel, isCashLeg, tradeCardDetailLine } from "@/lib/tradePairs";
import {
  cn,
  formatCurrency,
  formatDate,
  formatMarketName,
  formatNav,
  formatProductCodeLabel,
  formatReturnRate,
  formatSharesUnit,
  formatSignedCurrency,
  getNumberCellClass,
  getReturnColorClass,
  getStatusBadgeVariant,
  getTradeStatusLabel,
} from "@/lib/utils";
import { useHoldingsByProduct } from "@/hooks/usePosition";
import { useNavHistory } from "@/hooks/useProduct";
import { useTradeList } from "@/hooks/useTrade";
import { usePlatformList } from "@/hooks/usePlatform";
import PlatformDistributionCard from "./PlatformDistributionCard";
import ProductNavCards from "./ProductNavCards";
import QueryErrorState from "./QueryErrorState";
import CashMarketValueUpdateDialog from "./dialogs/CashMarketValueUpdateDialog";
import CashTransferDialog from "./dialogs/CashTransferDialog";

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
  const theme = useChartTheme();
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

  // 概览卡「单位净值 · 累计净值 (MM/DD)」取最新一条历史净值。三卡（走势/区间收益率/历史净值）
  // 已移入 ProductNavCards（#824），此处只为概览取首屏首条——页 1 的 query key 与 ProductNavCards
  // 首屏查询一致，react-query 去重为单次请求（见 useNavHistory 注释）。现金（market=""）不请求。
  const { items: latestNavItems } = useNavHistory(productCode, market, 1, !isCash && !!market);
  const latestPrice = latestNavItems[0];

  const {
    data: tradesData,
    isError: tradesError,
    error: tradesErr,
    refetch: refetchTrades,
  } = useTradeList({
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
    return <QueryErrorState error={holdingsErr} onRetry={refetchHoldings} />;
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
  // #705 页头代码用显示码；港互认双码并列（路由 href/key 仍用原始 productCode）
  const codeLabel = formatProductCodeLabel(product.display_code, product.product_code, market);
  const tradesLink = `${basePath}/${portfolioCode}/trades?product=${encodeURIComponent(productCode)}&market=${encodeURIComponent(market)}`;
  // #646：操作行「事件」落事件录入页（原 href 是不带参数的裸 tradesLink，与「查看全部」
  // 完全相同，跳到没有事件录入的调仓列表）
  const eventsLink = `${basePath}/${portfolioCode}/share-change-events?product=${encodeURIComponent(productCode)}&market=${encodeURIComponent(market)}&action=create`;

  const overview = (
    <Card data-testid="product-overview-card">
      <CardContent className="pt-4">
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>持仓市值</span>
          <span>最新快照 {snapshotDate ? formatDate(snapshotDate) : "--"}</span>
        </div>
        {/* 统计卡主数值与标签同侧左对齐（visual-spec §5，#729）：helper 供数字形态，
            text-left 经 twMerge 覆盖其表格专用右对齐；不直接挂自带 text-right 的 number-cell utility */}
        <div
          data-testid="overview-market-value"
          className={cn(getNumberCellClass(), "text-left whitespace-nowrap font-semibold text-2xl mt-1")}
        >
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
        <Link href={`${tradesLink}&trade_type=buy&action=create`}>买入</Link>
      </Button>
      <Button asChild variant="outline" className="flex-1">
        <Link href={`${tradesLink}&trade_type=sell&action=create`}>卖出</Link>
      </Button>
      <Button asChild variant="outline" className="flex-1">
        <Link href={eventsLink}>事件</Link>
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

  const tradesCard = (
    <section className="rounded-lg border border-border bg-card p-4" data-testid="product-trades-card">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">交易记录</h3>
        <Link href={tradesLink} className="text-sm text-primary hover:underline">
          查看全部
        </Link>
      </div>
      {/* #683B：判序承重——失败必须排在「长度为 0」之前。形状逐字照同形的 PlatformDetailContent
          交易卡（#663① 的产物），本页是漏掉的那一个：组合明明有已确认交易，
          5xx 时该卡却说「暂无交易记录」，用户读成「这个产品没成交过」 */}
      {tradesError ? (
        <QueryErrorState error={tradesErr} onRetry={refetchTrades} className="mt-3" />
      ) : (tradesData?.items ?? []).length === 0 ? (
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
                    style={{ background: tradeDirectionColors(theme)[trade.trade_type === "buy" ? "buy" : "sell"] }}
                  />
                  <span className="truncate">
                    {/* 现金腿：主行是产品页，平台列才是变量 → 用资金流向 + 本平台
                        （#719；列表主行仍走 §5.8 的 cashOrphanLabel 来源口径） */}
                    {isCashLeg(trade)
                      ? cashRowLabel(
                          trade,
                          trade.platform_code
                            ? platformNameMap.get(trade.platform_code)
                            : undefined
                        )
                      : `${TRADE_TYPE_LABELS[trade.trade_type] ?? trade.trade_type} · ${
                          trade.platform_code
                            ? (platformNameMap.get(trade.platform_code) ?? trade.platform_code)
                            : "--"
                        }`}
                  </span>
                </div>
                <div className="truncate text-xs text-muted-foreground tabular-nums">
                  {tradeCardDetailLine(trade, platformNameMap)}
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
          <h1 className="text-lg font-semibold">{product.product_name || codeLabel}</h1>
          <p className="text-sm text-muted-foreground">
            {codeLabel} · {formatMarketName(product.market)}
          </p>
        </div>
      </div>

      {overview}
      {actionRow}

      {isMobile ? (
        <>
          {platformCard}
          {/* #824：净值三卡（走势/区间收益率/历史净值）抽为 ProductNavCards，仅非现金渲染 */}
          {!isCash && <ProductNavCards productCode={productCode} market={market} variant={variant} />}
          {tradesCard}
        </>
      ) : (
        <div className="grid grid-cols-[1fr_360px] gap-4">
          <div className="space-y-4">
            {!isCash && <ProductNavCards productCode={productCode} market={market} variant={variant} />}
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
