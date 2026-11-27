"use client";

import { useMemo } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import EmptyState from "@/components/shared/EmptyState";
import InfoTip from "@/components/shared/InfoTip";
import QueryErrorState from "@/components/shared/QueryErrorState";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type {
  DashboardAggregateXirr,
  DashboardPortfolioSummary,
  DashboardSummary as DashboardSummaryData,
} from "@/lib/api";
import {
  cn,
  formatCurrency,
  formatProductCodeLabel,
  formatReturnRate,
  formatSharesUnit,
  formatSignedCurrency,
  getNumberCellClass,
  getReturnColorClass,
  getStatusBadgeVariant,
  getTradeStatusLabel,
} from "@/lib/utils";
import type { Subscription } from "@/types/subscription";
import type { Trade } from "@/types/trade";
import { cashLegOrigin, isCashLeg } from "@/lib/tradePairs";

const PENDING_PREVIEW_LIMIT = 6;

interface DashboardSummaryProps {
  summary: DashboardSummaryData;
  subscriptions: Subscription[];
  trades: Trade[];
  variant?: "desktop" | "mobile";
  basePath?: "" | "/m";
  isFetching?: boolean;
  summaryError?: unknown;
  onRetrySummary?: () => void;
  activityError?: unknown;
  onRetryActivity?: () => void;
}

type PendingItem =
  | { kind: "subscription"; id: number; item: Subscription }
  | { kind: "trade"; id: number; item: Trade };

type RecentItem = PendingItem & { date: string };

function xirrReasonText(xirr: DashboardAggregateXirr): string {
  if (xirr.reason === "short_history") return "持有期不足 90 天";
  if (xirr.reason === "insufficient_flows") return "本金进出现金流不足，无法计算";
  if (xirr.reason === "no_solution") return "当前现金流无法求得有效解";
  return "当前数据不足以可靠计算";
}

function rowXirrTitle(portfolio: DashboardPortfolioSummary): string | undefined {
  if (portfolio.annualization_reliable && portfolio.mwr !== null) return undefined;
  if (portfolio.holding_days !== null && portfolio.holding_days < 90) {
    return "持有期不足 90 天";
  }
  return "现金流不足或无法求得有效解";
}

function subscriptionAmount(subscription: Subscription): string {
  return subscription.sub_type === "subscribe"
    ? formatCurrency(subscription.amount)
    : formatSharesUnit(subscription.shares);
}

function tradeAmount(trade: Trade): string {
  return trade.trade_type === "buy" ? formatCurrency(trade.amount) : formatSharesUnit(trade.shares);
}

function PendingAgendaRow({ entry, basePath }: { entry: PendingItem; basePath: "" | "/m" }) {
  const portfolioCode = entry.item.portfolio_code;
  let title: string;
  let meta: string;
  let date: string;
  let amount: string;
  if (entry.kind === "subscription") {
    const item = entry.item;
    title = `${item.sub_type === "subscribe" ? "申购" : "赎回"} · ${portfolioCode}`;
    meta = `组合 ${portfolioCode} · 投资人 ${item.investor_code}`;
    date = item.apply_date;
    amount = subscriptionAmount(item);
  } else {
    const item = entry.item;
    title = `${item.trade_type === "buy" ? "买入" : "卖出"} · ${formatProductCodeLabel(
      item.display_code,
      item.product_code,
      item.market,
    )}`;
    meta = `组合 ${portfolioCode}`;
    date = item.trade_date;
    amount = tradeAmount(item);
  }
  const href = `${basePath}/portfolio/${portfolioCode}/${entry.kind === "subscription" ? "subscriptions" : "trades"}`;

  return (
    <div
      data-testid="dashboard-pending-item"
      data-kind={entry.kind}
      className="flex items-start justify-between gap-4 border-t border-warning/30 py-3 first:border-t-0"
    >
      <div className="min-w-0">
        <Link
          href={href}
          className="text-sm font-medium text-warning-foreground hover:underline"
        >
          {title}
        </Link>
        <p className="mt-1 text-xs text-warning-foreground">{meta}</p>
        <p className="mt-1 text-xs text-warning-foreground">{date}</p>
      </div>
      <div className="shrink-0 text-right">
        <p className="number-cell text-sm font-medium text-warning-foreground">{amount}</p>
        <Badge variant="warning" className="mt-1">待确认</Badge>
      </div>
    </div>
  );
}

function RecentActivityRow({ entry }: { entry: RecentItem }) {
  let title: string;
  let identity: string;
  let amount: string;
  if (entry.kind === "subscription") {
    const item = entry.item;
    title = item.sub_type === "subscribe" ? "申购" : "赎回";
    identity = `${item.portfolio_code} · ${item.investor_code}`;
    amount = subscriptionAmount(item);
  } else {
    const item = entry.item;
    title = item.trade_type === "buy" ? "买入" : "卖出";
    identity = `${formatProductCodeLabel(
      item.display_code,
      item.product_code,
      item.market,
    )} · ${item.portfolio_code}`;
    amount = tradeAmount(item);
  }

  return (
    <div
      data-testid="dashboard-recent-item"
      data-kind={entry.kind}
      className="flex items-center justify-between gap-4 py-3"
    >
      <div className="min-w-0">
        <p className="text-sm font-medium">
          {title}
          <Badge className="ml-2" variant={getStatusBadgeVariant(entry.item.status)}>
            {getTradeStatusLabel(entry.item.status)}
          </Badge>
        </p>
        <p className="mt-1 break-words text-xs text-muted-foreground">{identity}</p>
      </div>
      <div className="shrink-0 text-right">
        <p className="number-cell text-sm font-medium">{amount}</p>
        <p className="mt-1 text-xs text-muted-foreground">{entry.date}</p>
      </div>
    </div>
  );
}

export default function DashboardSummary({
  summary,
  subscriptions,
  trades,
  variant = "desktop",
  basePath = "",
  isFetching = false,
  summaryError,
  onRetrySummary,
  activityError,
  onRetryActivity,
}: DashboardSummaryProps) {
  const isMobile = variant === "mobile";
  const xirr = summary.aggregate_xirr;
  const reliableXirr = xirr.reliable && xirr.value !== null;
  const xirrValue = reliableXirr ? xirr.value : null;
  const pendingCount = summary.pending_subscription_count + summary.pending_trade_count;
  const pendingItems: PendingItem[] = [
    ...subscriptions
      .filter((subscription) => subscription.status === "pending")
      .map((item) => ({ kind: "subscription" as const, id: item.id, item })),
    ...trades
      .filter((trade) => trade.status === "pending")
      .map((item) => ({ kind: "trade" as const, id: item.id, item })),
  ];
  const recentItems: RecentItem[] = useMemo(() => {
    const items: RecentItem[] = [
      ...subscriptions.map((item) => ({
        kind: "subscription" as const,
        id: item.id,
        item,
        date: item.apply_date,
      })),
      ...trades
        .filter((item) => !(isCashLeg(item) && cashLegOrigin(item) === "subscription"))
        .map((item) => ({
          kind: "trade" as const,
          id: item.id,
          item,
          date: item.trade_date,
        })),
    ];
    return items.sort((a, b) => b.date.localeCompare(a.date)).slice(0, 5);
  }, [subscriptions, trades]);

  return (
    <div data-testid="dashboard-summary" aria-busy={isFetching} className="space-y-6">
      <section className="relative border-b pb-6" aria-label="家庭财务汇总">
        {isFetching && (
          <Loader2
            data-testid="dashboard-summary-refreshing"
            aria-label="正在更新首页汇总"
            className="absolute right-0 top-0 h-4 w-4 animate-spin text-muted-foreground"
          />
        )}
        {Boolean(summaryError) && (
          <div
            data-testid="dashboard-summary-error"
            role="alert"
            className="mt-3 border border-destructive/30 bg-destructive-soft p-3 text-sm"
          >
            <p className="font-medium text-destructive-foreground">汇总更新失败，当前显示上次数据</p>
            <QueryErrorState
              error={summaryError}
              onRetry={onRetrySummary}
              className="mt-1 text-left"
            />
          </div>
        )}
        <div
          className={cn(
            "flex gap-8",
            isMobile ? "flex-col" : "md:flex-row md:items-end md:justify-between",
          )}
        >
          <div className="min-w-0">
            <p className="sr-only">总资产</p>
            <p
              data-testid="dashboard-summary-total_value"
              className={cn(
                "amount-hero whitespace-nowrap leading-none",
                !isMobile && "lg:text-5xl",
              )}
            >
              {formatCurrency(summary.total_value)}
            </p>
            <p className="mt-3 text-sm text-muted-foreground">
              所有活跃组合合计，所有金额均为人民币
            </p>
          </div>

          <div
            className={cn(
              isMobile ? "mt-6 divide-y divide-border border-t" : "md:w-80 md:shrink-0 md:border-l md:pl-8",
            )}
          >
            <div className={cn("flex items-center justify-between gap-4", isMobile && "py-3")}>
              <span className="text-sm text-muted-foreground">累计收益</span>
              <p
                data-testid="dashboard-summary-total_profit"
                className={cn(
                  "whitespace-nowrap text-2xl font-semibold tabular-nums",
                  isMobile && "text-lg font-bold",
                  getReturnColorClass(summary.total_profit),
                )}
              >
                {formatSignedCurrency(summary.total_profit)}
              </p>
            </div>
            <div className={cn("flex items-center justify-between gap-4", isMobile && "py-3")}>
              <div className="flex min-w-0 items-center">
                <span className="whitespace-nowrap text-sm text-muted-foreground">
                  年化收益率（XIRR）
                </span>
                <InfoTip
                  label="合计 XIRR 计算口径"
                  content="合并全部活跃组合的外部本金进出现金流，并以各组合期末市值作为终端现金流统一求解；不是组合收益率的平均值，也不是总资产复合增长率。持有期不足 90 天时不展示年化值。"
                />
              </div>
              <div className="shrink-0 text-right">
                <p
                  data-testid="dashboard-summary-aggregate_xirr"
                  className={cn(
                    "whitespace-nowrap text-2xl font-semibold tabular-nums",
                    isMobile && "text-lg font-bold",
                    reliableXirr ? getReturnColorClass(xirrValue) : "text-muted-foreground",
                  )}
                >
                  {formatReturnRate(xirrValue)}
                </p>
                {!reliableXirr && (
                  <p
                    data-testid="dashboard-summary-xirr-reason"
                    className="mt-1 text-xs text-muted-foreground"
                  >
                    {xirrReasonText(xirr)}
                  </p>
                )}
              </div>
            </div>
          </div>
        </div>
      </section>

      {pendingCount > 0 && (
        <Card className="border-warning/30 bg-warning-soft" data-testid="dashboard-pending-agenda">
          <CardContent className={isMobile ? "p-3" : "p-6"}>
            <div className="flex items-center justify-between gap-4">
              <h2 className="flex items-center gap-2 text-base font-semibold leading-6 text-warning-foreground">
                <span className="h-2 w-2 rounded-full bg-warning" />
                待确认事项
              </h2>
              <span className="text-xs text-warning-foreground">{pendingCount} 笔待确认</span>
            </div>
            <div className="mt-3">
              {pendingItems.length === 0 ? (
                <p className="text-sm text-warning-foreground">
                  {activityError
                    ? "待确认明细加载失败，请重试或在组合页查看"
                    : "当前列表未显示待确认明细，请前往组合页查看"}
                </p>
              ) : (
                <>
                  {pendingItems.slice(0, PENDING_PREVIEW_LIMIT).map((entry) => (
                    <PendingAgendaRow
                      key={`${entry.kind}-${entry.id}`}
                      entry={entry}
                      basePath={basePath}
                    />
                  ))}
                  {pendingCount > Math.min(pendingItems.length, PENDING_PREVIEW_LIMIT) && (
                    <p className="mt-2 text-xs text-warning-foreground">
                      还有 {pendingCount - Math.min(pendingItems.length, PENDING_PREVIEW_LIMIT)} 笔，请前往组合页查看
                    </p>
                  )}
                </>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      <section aria-labelledby="dashboard-active-portfolios">
        <div className="mb-3">
          <h2 id="dashboard-active-portfolios" className="text-base font-semibold leading-6">
            活跃组合
          </h2>
          <p className="text-sm text-muted-foreground">当前活跃的投资组合</p>
        </div>
        {summary.portfolios.length === 0 && (
          <EmptyState message="暂无活跃组合，请先创建组合" />
        )}
        {summary.portfolios.length > 0 && isMobile && (
          <div className="space-y-2">
            {summary.portfolios.map((portfolio) => {
              const rowXirr = portfolio.annualization_reliable ? portfolio.mwr : null;
              return (
                <Link
                  key={portfolio.code}
                  href={`${basePath}/portfolio/${portfolio.code}`}
                  data-testid="dashboard-portfolio-row"
                  data-portfolio-code={portfolio.code}
                  className="block rounded-lg focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  <Card className="transition-colors hover:bg-accent">
                    <CardContent className="p-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="break-words text-sm font-medium">{portfolio.name}</p>
                          <p className="mt-1 text-xs text-muted-foreground">{portfolio.code}</p>
                        </div>
                        <p
                          data-testid="dashboard-portfolio-value"
                          className="number-cell shrink-0 text-sm font-medium"
                        >
                          {formatCurrency(portfolio.total_value)}
                        </p>
                      </div>
                      <div className="mt-3 grid grid-cols-2 gap-3 border-t pt-3">
                        <div>
                          <p className="text-xs text-muted-foreground">累计收益</p>
                          <p
                            data-testid="dashboard-portfolio-profit"
                            className={cn(
                              "number-cell mt-1 text-sm font-medium",
                              getReturnColorClass(portfolio.total_profit),
                            )}
                          >
                            {formatSignedCurrency(portfolio.total_profit)}
                          </p>
                        </div>
                        <div>
                          <p className="text-xs text-muted-foreground">年化收益率（XIRR）</p>
                          <p
                            data-testid="dashboard-portfolio-xirr"
                            title={rowXirrTitle(portfolio)}
                            className={cn(
                              "number-cell mt-1 text-sm font-medium",
                              getReturnColorClass(rowXirr),
                            )}
                          >
                            {formatReturnRate(rowXirr)}
                          </p>
                          {rowXirr === null && (
                            <p className="mt-1 text-xs text-muted-foreground">
                              {rowXirrTitle(portfolio)}
                            </p>
                          )}
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                </Link>
              );
            })}
          </div>
        )}
        {summary.portfolios.length > 0 && !isMobile && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>组合 / 代码</TableHead>
                <TableHead className="text-right">总资产</TableHead>
                <TableHead className="text-right">累计收益</TableHead>
                <TableHead className="text-right">年化收益率（XIRR）</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {summary.portfolios.map((portfolio) => {
                const rowXirr = portfolio.annualization_reliable ? portfolio.mwr : null;
                return (
                  <TableRow
                    key={portfolio.code}
                    data-testid="dashboard-portfolio-row"
                    data-portfolio-code={portfolio.code}
                  >
                    <TableCell>
                      <Link
                        href={`${basePath}/portfolio/${portfolio.code}`}
                        className="block rounded-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                      >
                        <p className="break-words text-sm font-medium">{portfolio.name}</p>
                        <p className="text-xs text-muted-foreground">{portfolio.code}</p>
                      </Link>
                    </TableCell>
                    <TableCell data-testid="dashboard-portfolio-value" className={getNumberCellClass()}>
                      {formatCurrency(portfolio.total_value)}
                    </TableCell>
                    <TableCell
                      data-testid="dashboard-portfolio-profit"
                      className={cn(getNumberCellClass(), getReturnColorClass(portfolio.total_profit))}
                    >
                      {formatSignedCurrency(portfolio.total_profit)}
                    </TableCell>
                    <TableCell
                      data-testid="dashboard-portfolio-xirr"
                      title={rowXirrTitle(portfolio)}
                      className={cn(getNumberCellClass(), getReturnColorClass(rowXirr))}
                    >
                      {formatReturnRate(rowXirr)}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </section>

      <section aria-labelledby="dashboard-recent-activity">
        <div className="mb-3">
          <h2 id="dashboard-recent-activity" className="text-base font-semibold leading-6">
            最近交易
          </h2>
          <p className="text-sm text-muted-foreground">最近的投资操作记录</p>
        </div>
        {recentItems.length > 0 && (
          <div data-testid="dashboard-recent-activity" className="divide-y divide-border border-y">
            {recentItems.map((entry) => (
              <RecentActivityRow key={`${entry.kind}-${entry.id}`} entry={entry} />
            ))}
          </div>
        )}
        {Boolean(activityError) && (
          <QueryErrorState
            error={activityError}
            onRetry={onRetryActivity}
            className="mt-3"
          />
        )}
        {recentItems.length === 0 && !activityError && <EmptyState message="暂无交易记录" />}
      </section>
    </div>
  );
}
