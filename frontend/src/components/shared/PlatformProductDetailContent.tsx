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
import PlatformDistributionCard from "./PlatformDistributionCard";
import ProductNavCards from "./ProductNavCards";
import QueryErrorState from "./QueryErrorState";
import CashMarketValueUpdateDialog from "./dialogs/CashMarketValueUpdateDialog";
import CashTransferDialog from "./dialogs/CashTransferDialog";
import { TRADE_DIRECTION_COLORS } from "@/lib/colors";
import { CASH_PRODUCT_CODE } from "@/lib/allocation";
import { cashRowLabel, isCashLeg, tradeCardDetailLine } from "@/lib/tradePairs";
import {
  cn,
  formatCurrency,
  formatDate,
  formatMarketName,
  formatNav,
  formatProductCodeLabel,
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
 * 「查看该产品全部平台持仓」链接 → 净值走势 + 区间收益率 + 历史净值 → 交易记录 →
 * 全部平台持仓卡（多平台时显示，当前平台行不可点）。
 * 数据源：holdings/by-product 行的 platforms 切片 + nav-analysis/nav-history + trades。
 * 现金产品（CASH）隐藏净值相关卡片，操作行改为转入/转出。
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
  // #595 §4.5/D-10：现金操作 Dialog 状态（transfer 含方向：转出 = 本平台为 from，转入 = 本平台为 to）
  const [isCashUpdateOpen, setIsCashUpdateOpen] = useState(false);
  const [cashTransfer, setCashTransfer] = useState<{ open: boolean; direction: "in" | "out" }>({
    open: false,
    direction: "out",
  });

  const { data: holdings, isLoading: holdingsLoading, isError: holdingsError, error: holdingsErr, refetch: refetchHoldings } =
    useHoldingsByProduct(portfolioCode);
  const product = holdings?.products.find(
    (p) => p.product_code === productCode && p.market === market
  );
  const slice = product?.platforms.find((s) => s.platform_code === platformCode);

  // 概览卡「单位净值 · 累计净值 (MM/DD)」取最新一条历史净值。三卡（走势/区间收益率/历史净值）
  // 已收口到共享 ProductNavCards（#828），此处只为概览取首屏首条——页 1 的 query key 与
  // ProductNavCards 首屏查询一致，react-query 去重为单次请求（见 useNavHistory 注释）。
  // 现金（market=""）不请求。
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
    platform_code: platformCode,
    page: 1,
    page_size: 5,
  });

  // 对手平台名（#719）：本平台-产品页按 platform_code 取数，平台间转移只有本平台这一腿，
  // 副行要说清「转到哪个平台」；映射缺失时回退显示平台码
  const { data: platformsData } = usePlatformList({ page_size: 100 });
  const platformNameMap = useMemo(
    () => new Map((platformsData?.items ?? []).map((p) => [p.code, p.name])),
    [platformsData?.items]
  );

  if (holdingsLoading) return <LoadingState />;
  if (holdingsError) {
    return <QueryErrorState error={holdingsErr} onRetry={refetchHoldings} />;
  }
  // S4：market 缺失（非现金）单独提示，避免与「真的没有」混淆
  if (!isCash && !market) {
    return (
      <EmptyState
        message="缺少 market 参数"
        description="请从产品详情页或平台详情页进入本平台-产品详情"
      />
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
  // #705 页头代码用显示码；港互认双码并列（路由 href/key 仍用原始 productCode）
  const codeLabel = formatProductCodeLabel(product.display_code, productCode, market);
  const tradesLink = `${basePath}/${portfolioCode}/trades?product=${encodeURIComponent(productCode)}&market=${encodeURIComponent(market)}&platform=${encodeURIComponent(platformCode)}`;
  // #646：操作行「事件」落事件录入页（原 href 是不带参数的裸 tradesLink，与「查看全部」
  // 完全相同，跳到没有事件录入的调仓列表）
  const eventsLink = `${basePath}/${portfolioCode}/share-change-events?product=${encodeURIComponent(productCode)}&market=${encodeURIComponent(market)}&platform=${encodeURIComponent(platformCode)}&action=create`;
  // 产品详情页路由（步骤⑤起 market 走 ?market= searchParams，与平台-产品页形态对齐）
  const productDetailLink = `${basePath}/${portfolioCode}/product/${encodeURIComponent(productCode)}?market=${encodeURIComponent(market)}`;

  const overview = (
    <Card data-testid="platform-product-overview-card">
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
            <div className="text-sm text-muted-foreground">累计收益*</div>
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
        <p className="mt-2 text-xs text-warning">
          *累计收益含已卖出实现盈亏，与持仓卡「累计收益」（持有收益口径）不同
        </p>
      </CardContent>
    </Card>
  );

  // #595 §4.5/D-10：现金产品操作行为转入/转出 + 市值更新。
  // 转入/转出打开现金转移 Dialog（预填本平台 + 方向：转出 from=本平台、转入 to=本平台，
  // 两腿显式落账），不走 trades 的 buy/sell（REST 禁止直接创建 CASH 交易）
  const actionRow = isCash ? (
    <div className="flex gap-2" data-testid="platform-product-action-row">
      <Button
        className="flex-1"
        onClick={() => setCashTransfer({ open: true, direction: "in" })}
      >
        转入
      </Button>
      <Button
        variant="outline"
        className="flex-1"
        onClick={() => setCashTransfer({ open: true, direction: "out" })}
      >
        转出
      </Button>
      <Button variant="outline" className="flex-1" onClick={() => setIsCashUpdateOpen(true)}>
        市值更新
      </Button>
    </div>
  ) : (
    <div className="flex gap-2" data-testid="platform-product-action-row">
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

  const tradesCard = (
    <section className="rounded-lg border border-border bg-card p-4" data-testid="platform-product-trades-card">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">交易记录</h3>
        <Link href={tradesLink} className="text-sm text-primary hover:underline">
          查看全部
        </Link>
      </div>
      {tradesError ? (
        <QueryErrorState error={tradesErr} onRetry={refetchTrades} className="mt-3" />
      ) : (tradesData?.items ?? []).length === 0 ? (
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
                    {/* 现金行：本页平台与产品都已固定，`卖出` 读不出任何信息 →
                        改资金流向标注，去处写在副行（#719） */}
                    {isCashLeg(trade)
                      ? cashRowLabel(trade)
                      : (TRADE_TYPE_LABELS[trade.trade_type] ?? trade.trade_type)}
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

  // 「查看该产品全部平台持仓」— R-5：现金产品详情页自 B-6 迁移后同样可达（?market= 形态），
  // 不再屏蔽现金侧，保证平台-产品页 → 产品详情页三级跳转链在现金也闭合
  const allPlatformsLink = product.platforms.length > 0 && (
    <Link
      href={productDetailLink}
      className="text-sm text-primary hover:underline"
      data-testid="view-all-platforms-link"
    >
      查看该产品全部平台持仓
    </Link>
  );

  // 全部平台持仓卡（D5 右栏 / M5 底部）；现金同样展示（各平台现金切片经 rowLinkPrefix 互跳）
  const allPlatformsCard = product.platforms.length > 1 && (
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
            {slice.platform_name || platformCode} · {product.product_name || codeLabel}
          </h1>
          <p className="text-sm text-muted-foreground">
            {codeLabel}{market ? ` · ${formatMarketName(market)}` : ""}
          </p>
        </div>
      </div>

      {overview}
      {actionRow}
      {allPlatformsLink && <div>{allPlatformsLink}</div>}

      {isMobile ? (
        <>
          {!isCash && (
            <ProductNavCards
              productCode={productCode}
              market={market}
              variant={variant}
              testIdPrefix="platform-product"
            />
          )}
          {tradesCard}
          {allPlatformsCard}
        </>
      ) : (
        <div className="grid grid-cols-[1fr_360px] gap-4">
          <div className="space-y-4">
            {!isCash && (
              <ProductNavCards
                productCode={productCode}
                market={market}
                variant={variant}
                testIdPrefix="platform-product"
              />
            )}
          </div>
          <div className="space-y-4">
            {tradesCard}
            {allPlatformsCard}
          </div>
        </div>
      )}

      {/* #595 §4.5：现金市值更新 Dialog（仅现金产品使用，预填本平台） */}
      {isCash && (
        <CashMarketValueUpdateDialog
          portfolioCode={portfolioCode}
          open={isCashUpdateOpen}
          onOpenChange={setIsCashUpdateOpen}
          defaultPlatformCode={platformCode}
        />
      )}

      {/* #595 §4.5/D-10：现金转移 Dialog（预填本平台 + 方向） */}
      {isCash && (
        <CashTransferDialog
          portfolioCode={portfolioCode}
          open={cashTransfer.open}
          onOpenChange={(open) => setCashTransfer((s) => ({ ...s, open }))}
          contextPlatformCode={platformCode}
          direction={cashTransfer.direction}
        />
      )}
    </div>
  );
}
