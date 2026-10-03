"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import LoadingState from "@/components/shared/LoadingState";
import EmptyState from "@/components/shared/EmptyState";
import QueryErrorState from "@/components/shared/QueryErrorState";
import HoldingProductCard from "./HoldingProductCard";
import { TRADE_DIRECTION_COLORS } from "@/lib/colors";
import { CASH_PRODUCT_CODE } from "@/lib/allocation";
import { getErrorMessage } from "@/lib/api";
import { groupTradeRows, cashSubMeta, cashLegArrived, isCashLeg, cashOrphanLabel } from "@/lib/tradePairs";
import {
  formatCurrency,
  formatDate,
  formatNav,
  formatSharesUnit,
  formatSignedCurrency,
  getReturnColorClass,
  getStatusBadgeVariant,
  getTradeStatusLabel,
  largestRemainderPercents,
} from "@/lib/utils";
import { useHoldingsByProduct, useHoldingsByPlatform } from "@/hooks/usePosition";
import { useTradeList } from "@/hooks/useTrade";
import type { Trade } from "@/types/trade";

const TRADE_TYPE_LABELS: Record<string, string> = { buy: "买入", sell: "卖出" };

interface PlatformDetailContentProps {
  basePath: string;
  variant: "desktop" | "mobile";
}

/**
 * #595 步骤④ 平台详情页（M4/D4，双端共享）：
 * 概览卡 → 操作行（买入/卖出/事件，trades 平台预填）→ 持仓明细（该平台产品卡 + 现金卡）→ 交易记录。
 * 数据源：holdings/by-product（按 platforms 含本平台过滤）+ holdings/by-platform（概览指标）+
 * trades 列表 platform_code 过滤。
 * 交易记录经 groupTradeRows 结对展示（CASH 腿折叠为子行，与 TradesContent 同口径）。
 */
export default function PlatformDetailContent({ basePath, variant }: PlatformDetailContentProps) {
  const params = useParams();
  const portfolioCode = params.code as string;
  const platformCode = params.platformCode as string;
  const isMobile = variant === "mobile";

  const { data: productData, isLoading: productLoading, isError: productError, error: productErr, refetch: refetchProduct } =
    useHoldingsByProduct(portfolioCode);
  const { data: platformData, isLoading: platformLoading, isError: platformError, error: platformErr, refetch: refetchPlatform } =
    useHoldingsByPlatform(portfolioCode);

  const platform = platformData?.platforms.find(
    (p) => p.platform_code === platformCode
  );

  // 过滤出包含本平台的非现金产品
  const platformProducts = useMemo(() => {
    if (!productData) return [];
    return productData.products.filter((p) => {
      if (p.product_code === CASH_PRODUCT_CODE) return false;
      return p.platforms.some((s) => s.platform_code === platformCode);
    });
  }, [productData, platformCode]);

  // 本平台现金切片
  const cashProduct = useMemo(() => {
    if (!productData) return null;
    const cash = productData.products.find((p) => p.product_code === CASH_PRODUCT_CODE);
    if (!cash) return null;
    const slice = cash.platforms.find((s) => s.platform_code === platformCode);
    return slice ? { ...cash, _slice: slice } : null;
  }, [productData, platformCode]);

  // 行级占比（最大余数法，§4）
  const productPercents = useMemo(() => {
    const values = platformProducts.map((p) => {
      const slice = p.platforms.find((s) => s.platform_code === platformCode);
      return slice?.market_value ?? 0;
    });
    if (cashProduct?._slice) values.push(cashProduct._slice.market_value);
    return largestRemainderPercents(values);
  }, [platformProducts, cashProduct, platformCode]);

  const {
    data: tradesData,
    isError: tradesError,
    error: tradesErr,
    refetch: refetchTrades,
  } = useTradeList({
    portfolio_code: portfolioCode,
    platform_code: platformCode,
    page: 1,
    page_size: 5,
  });

  const isLoading = productLoading || platformLoading;
  const isError = productError || platformError;

  if (isLoading) return <LoadingState />;
  if (isError) {
    const msg = getErrorMessage(productErr ?? platformErr, "请刷新重试");
    return (
      <div className="py-8 text-center">
        <p className="text-muted-foreground">加载失败：{msg}</p>
        <Button variant="link" size="sm" onClick={() => { refetchProduct(); refetchPlatform(); }}>
          重试
        </Button>
      </div>
    );
  }
  if (!platform) {
    return (
      <EmptyState
        message="未找到该平台持仓"
        description="平台可能无持仓或不属于当前组合"
      />
    );
  }

  const snapshotDate = productData?.snapshot_date ?? platformData?.snapshot_date;
  const tradesLink = `${basePath}/${portfolioCode}/trades?platform=${encodeURIComponent(platformCode)}`;
  // #646：操作行「事件」落事件录入页（原 href 是不带参数的裸 tradesLink，与「查看全部」
  // 完全相同，跳到没有事件录入的调仓列表）
  const eventsLink = `${basePath}/${portfolioCode}/share-change-events?platform=${encodeURIComponent(platformCode)}&action=create`;

  // 在途资金口径披露（#641 恢复首轮 S5）：概览市值含本平台在途、下方持仓明细
  // 不含——数字取本平台平台卡的 in_transit_market_value，不得借组合级字段
  //（PR #639 B2 教训：组合级的数会把 A 平台的在途写到 B 平台名下）
  const inTransitValue = platform.in_transit_market_value ?? 0;

  const overview = (
    <Card data-testid="platform-overview-card">
      <CardContent className="pt-4">
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>持仓市值</span>
          <span>最新快照 {snapshotDate ? formatDate(snapshotDate) : "--"}</span>
        </div>
        <div className="mt-1 text-2xl font-semibold number-cell">
          {formatCurrency(platform.market_value)}
        </div>
        <div className={`mt-3 grid gap-3 ${isMobile ? "grid-cols-2" : "grid-cols-4"}`}>
          <div>
            <div className="text-sm text-muted-foreground">持有收益</div>
            <div className={`text-sm font-medium tabular-nums ${getReturnColorClass(platform.holding_profit)}`}>
              {formatSignedCurrency(platform.holding_profit)}
            </div>
          </div>
          <div>
            <div className="text-sm text-muted-foreground">现金余额</div>
            <div className="text-sm font-medium tabular-nums">
              {formatCurrency(platform.cash_balance)}
            </div>
          </div>
          <div>
            <div className="text-sm text-muted-foreground">持仓产品数</div>
            <div className="text-sm font-medium tabular-nums">
              {platform.product_count}
            </div>
          </div>
          <div>
            <div className="text-sm text-muted-foreground">占组合比</div>
            <div className="text-sm font-medium tabular-nums">
              {platform.ratio !== null && platform.ratio !== undefined
                ? `${(platform.ratio * 100).toFixed(1)}%`
                : "--"}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );

  const actionRow = (
    <div className="flex gap-2" data-testid="platform-action-row">
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

  const holdingsSection = (
    <section data-testid="platform-holdings-section">
      <h3 className="text-lg font-semibold">持仓明细</h3>
      {platformProducts.length === 0 && !cashProduct ? (
        <p className="mt-3 text-sm text-muted-foreground">暂无持仓</p>
      ) : (
        <>
          <div className={`mt-2 ${isMobile ? "space-y-2.5" : "grid gap-3 [grid-template-columns:repeat(auto-fill,minmax(350px,1fr))]"}`}>
            {platformProducts.map((p, i) => {
              const slice = p.platforms.find((s) => s.platform_code === platformCode)!;
              // N1：slice 由 filter 保证存在，直接取切片字段（null 交给格式化函数 → "--"）
              const cardProduct = {
                ...p,
                market_value: slice.market_value,
                shares: slice.shares,
                holding_profit: slice.holding_profit,
                daily_profit: slice.daily_profit,
              };
              return (
                <Link
                  key={`${p.product_code}-${p.market}`}
                  href={`${basePath}/${portfolioCode}/platforms/${platformCode}/products/${encodeURIComponent(p.product_code)}?market=${encodeURIComponent(p.market)}`}
                  className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <HoldingProductCard
                    product={cardProduct}
                    percent={productPercents[i] ?? 0}
                    snapshotDate={snapshotDate}
                  />
                </Link>
              );
            })}
            {cashProduct && (
              <Link
                href={`${basePath}/${portfolioCode}/platforms/${platformCode}/products/${CASH_PRODUCT_CODE}?market=`}
                className="block rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <div
                  data-testid="platform-cash-card"
                  className="rounded-lg border border-border bg-card px-4 py-3"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold">现金</span>
                    <span className="text-xs number-cell text-muted-foreground">
                      {productPercents[platformProducts.length]?.toFixed(1) ?? "0.0"}%
                    </span>
                  </div>
                  <div className="mt-2 text-xs text-muted-foreground">现金余额</div>
                  <div className="mt-0.5 text-lg font-bold number-cell text-foreground">
                    {formatCurrency(cashProduct._slice.cash_amount ?? cashProduct._slice.market_value)}
                  </div>
                </div>
              </Link>
            )}
          </div>
          {/* S5/#641：在途资金口径说明——概览市值含在途，明细卡片不含。
              testid 供 E2E 按元素断言（#654 L2 S2：整段 toContainText 将来可能变松） */}
          {inTransitValue > 0 && (
            <p data-testid="platform-in-transit-note" className="mt-2 text-xs text-muted-foreground">
              *持仓市值含在途资金 ¥{formatCurrency(inTransitValue).replace("¥", "")}，上方卡片不含在途
            </p>
          )}
        </>
      )}
    </section>
  );

  const tradeRows = groupTradeRows(tradesData?.items ?? []);

  // R4-N2：主行 JSX 提取为局部函数，pair 主行与 single 行共用（同 TradesContent renderMainRow 模式）
  const renderTradeMainRow = (trade: Trade) => (
    <div className="flex items-center justify-between gap-2">
      <div className="min-w-0">
        <div className="flex items-center gap-1.5 text-sm">
          <span
            className="h-1.5 w-1.5 shrink-0 rounded-full"
            style={{ background: TRADE_DIRECTION_COLORS[trade.trade_type === "buy" ? "buy" : "sell"] }}
          />
          <span className="truncate">
            {/* R4-S1：CASH 孤儿行走 cashOrphanLabel（「现金 · 申赎确认」等），与 TradesContent 同口径 */}
            {isCashLeg(trade)
              ? cashOrphanLabel(trade)
              : `${TRADE_TYPE_LABELS[trade.trade_type] ?? trade.trade_type} · ${trade.product_name ?? trade.product_code ?? "--"}`}
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
    </div>
  );

  const tradesCard = (
    <section className="rounded-lg border border-border bg-card p-4" data-testid="platform-trades-card">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">交易记录</h3>
        <Link href={tradesLink} className="text-sm text-primary hover:underline">
          查看全部
        </Link>
      </div>
      {tradesError ? (
        <QueryErrorState error={tradesErr} onRetry={refetchTrades} className="mt-3" />
      ) : tradeRows.length === 0 ? (
        <p className="mt-3 text-sm text-muted-foreground">暂无交易记录</p>
      ) : (
        <ul className="mt-2">
          {tradeRows.map((row) => {
            if (row.kind === "pair") {
              const { main, sub } = row;
              const meta = cashSubMeta(main, { arrived: cashLegArrived(sub) });
              return (
                <li key={main.id} data-testid="platform-trade-row" className="border-t border-border py-2 first:border-t-0">
                  {renderTradeMainRow(main)}
                  {/* 子行（配对现金腿） */}
                  <div className="mt-1 flex items-center justify-between gap-2 pl-3">
                    <span className="text-xs text-muted-foreground">
                      {meta.label}{meta.sign === "+" ? ` +${formatCurrency(sub.amount ?? 0)}` : ` -${formatCurrency(sub.amount ?? 0)}`}
                      {sub.confirm_date ? ` · ${formatDate(sub.confirm_date)}` : ""}
                    </span>
                  </div>
                </li>
              );
            }
            return (
              <li
                key={row.trade.id}
                data-testid="platform-trade-row"
                className="border-t border-border py-2 first:border-t-0"
              >
                {renderTradeMainRow(row.trade)}
              </li>
            );
          })}
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
          <h1 className="text-lg font-semibold">
            {platform.platform_name || platform.platform_code || "--"}
          </h1>
          <p className="text-sm text-muted-foreground">
            {platform.platform_code}
            {platform.platform_type ? ` · ${platform.platform_type}` : ""}
          </p>
        </div>
      </div>

      {overview}
      {actionRow}

      {isMobile ? (
        <>
          {holdingsSection}
          {tradesCard}
        </>
      ) : (
        <div className="grid grid-cols-[1fr_360px] gap-4">
          <div className="space-y-4">
            {holdingsSection}
          </div>
          <div className="space-y-4">
            {tradesCard}
          </div>
        </div>
      )}
    </div>
  );
}
