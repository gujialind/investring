"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Loader2, AlertCircle } from "lucide-react";
import { useTrade } from "@/hooks/useTrade";
import { usePlatformList } from "@/hooks/usePlatform";
import {
  formatCurrency,
  formatDate,
  formatNav,
  formatSharesUnit,
  getStatusBadgeVariant,
  getTradeStatusLabel,
} from "@/lib/utils";
import { isCashLeg } from "@/lib/tradePairs";
import type { TradePairedLeg } from "@/types/trade";

interface TradeDetailDialogProps {
  portfolioCode: string;
  tradeId: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function DetailRow({ k, children }: { k: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 py-2.5" data-testid="detail-row">
      <span className="text-muted-foreground shrink-0">{k}</span>
      <div className="text-right min-w-0">{children}</div>
    </div>
  );
}

/**
 * 只读交易详情弹窗（issue #789）：现金流水页「关联业务」与调仓交易页行内入口共用。
 * 弹窗内**没有任何编辑/确认/删除按钮**——写操作一律回到调仓交易页生命周期矩阵。
 * CASH 腿按现金语义渲染（不出现「份额 @ 净值」）；`paired_leg` 为 null（`sub_`
 * 申赎腿、空组号、组内行数 ≠ 2 三情形）时优雅降级为「无配对对手腿」。
 */
export default function TradeDetailDialog({
  portfolioCode,
  tradeId,
  open,
  onOpenChange,
}: TradeDetailDialogProps) {
  const { data: trade, isLoading, isError, error, refetch, isFetching } = useTrade(
    tradeId ?? 0,
    portfolioCode
  );
  const { data: platforms } = usePlatformList();
  const platformName = (code?: string | null) =>
    code ? (platforms?.items.find((p) => p.code === code)?.name ?? code) : "--";

  const cash = trade ? isCashLeg(trade) : false;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[560px]" data-testid="trade-detail-dialog">
        <DialogHeader>
          <DialogTitle>交易详情</DialogTitle>
          <DialogDescription>
            只读视图 · 写操作请前往调仓交易页
          </DialogDescription>
        </DialogHeader>
        {isLoading ? (
          <div className="flex items-center justify-center py-10 text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            加载中...
          </div>
        ) : isError ? (
          // 加载失败 destructive inline Alert + 重试（#789 验收）；不整页兜底出弹窗
          <Alert variant="destructive" data-testid="trade-detail-error">
            <AlertCircle className="h-4 w-4" />
            <AlertTitle>交易详情加载失败</AlertTitle>
            <AlertDescription className="flex items-center gap-3">
              <span className="min-w-0 break-all">
                {error instanceof Error ? error.message : "请稍后重试"}
              </span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => refetch()}
                disabled={isFetching}
              >
                {isFetching && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                重试
              </Button>
            </AlertDescription>
          </Alert>
        ) : trade ? (
          <div className="divide-y text-sm" data-testid="trade-detail-body">
            <DetailRow k="产品">
              <div className="font-medium">
                {trade.product_name ?? trade.product_code}
              </div>
              {trade.display_code && trade.display_code !== trade.product_name && (
                <div className="text-xs text-muted-foreground font-mono">
                  {trade.display_code}
                  {trade.market ? ` · ${trade.market}` : ""}
                </div>
              )}
            </DetailRow>
            <DetailRow k="平台">{platformName(trade.platform_code)}</DetailRow>
            <DetailRow k="方向">
              <Badge variant="neutral">
                {trade.trade_type === "buy" ? "买入" : "卖出"}
              </Badge>
            </DetailRow>
            <DetailRow k="状态">
              <div>
                <Badge variant={getStatusBadgeVariant(trade.status)}>
                  {getTradeStatusLabel(trade.status)}
                </Badge>
                {trade.status === "pending" && trade.confirm_date && (
                  <div className="text-xs text-muted-foreground mt-0.5">
                    预计 {formatDate(trade.confirm_date)} 确认
                  </div>
                )}
              </div>
            </DetailRow>
            <DetailRow k="金额">
              <span className="number-cell font-medium">
                {formatCurrency(trade.amount)}
              </span>
            </DetailRow>
            {/* CASH 腿无份额/净值语义（同列表口径 #732）：这两行整行不渲染，
                不把 1.0000 记账单位渲成现金的「净值」 */}
            {!cash && (
              <>
                <DetailRow k="份额">
                  <span className="number-cell">
                    {formatSharesUnit(trade.shares)}
                  </span>
                </DetailRow>
                <DetailRow k="价格">
                  <span className="number-cell">{formatNav(trade.price)}</span>
                </DetailRow>
              </>
            )}
            <DetailRow k="手续费">
              <span className="number-cell">{formatCurrency(trade.fee)}</span>
            </DetailRow>
            <DetailRow k="交易日期">{formatDate(trade.trade_date)}</DetailRow>
            <DetailRow k="确认日期">
              {trade.confirm_date ? formatDate(trade.confirm_date) : "--"}
            </DetailRow>
            {/* 到账日只服务已确认/在途的基金卖出腿（#493 读侧 cash_confirm_date）；
                CASH 腿自身不回填、买入的到账日=下单日，均无此行 */}
            {!cash && trade.trade_type === "sell" && (
              <DetailRow k="到账日期">
                {trade.cash_confirm_date ? formatDate(trade.cash_confirm_date) : "--"}
              </DetailRow>
            )}
            {trade.notes ? (
              <DetailRow k="备注">
                <span className="whitespace-pre-wrap break-words">{trade.notes}</span>
              </DetailRow>
            ) : null}
            <DetailRow k="配对腿">
              {trade.paired_leg ? (
                <PairedLegBlock leg={trade.paired_leg} platformName={platformName} />
              ) : (
                <span className="text-xs text-muted-foreground">无配对对手腿</span>
              )}
            </DetailRow>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function PairedLegBlock({
  leg,
  platformName,
}: {
  leg: TradePairedLeg;
  platformName: (code?: string | null) => string;
}) {
  return (
    <div className="space-y-0.5" data-testid="paired-leg-block">
      <div className="font-medium">
        {leg.product_name ?? leg.product_code}
        <span className="ml-2 font-normal text-xs text-muted-foreground">
          {leg.trade_type === "buy" ? "买入" : "卖出"} ·{" "}
          {platformName(leg.platform_code)}
        </span>
      </div>
      <div className="text-xs text-muted-foreground">
        <span className="number-cell">{formatCurrency(leg.amount)}</span>
        {leg.shares != null && (
          <span className="ml-2">份额 {formatSharesUnit(leg.shares)}</span>
        )}
        {leg.confirm_date && <span className="ml-2">确认 {formatDate(leg.confirm_date)}</span>}
        <Badge variant={getStatusBadgeVariant(leg.status)} className="ml-2">
          {getTradeStatusLabel(leg.status)}
        </Badge>
      </div>
    </div>
  );
}
