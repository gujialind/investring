"use client";

import { ConfirmInfoDialog, InfoRow } from "./ConfirmInfoDialog";
import { useTradePreview } from "@/hooks/useTrade";
import { getErrorMessage } from "@/lib/api";
import type { Trade } from "@/types/trade";
import {
  formatCurrency,
  formatSharesUnit,
  formatNav,
  formatDate,
  formatMarketName,
  formatProductName,
} from "@/lib/utils";

/**
 * 调仓交易确认信息核对弹窗（#248）：
 * 打开时拉取后端既有确认预览（与真实确认共用计算实现）。
 * 记录字段取自列表行（含读侧派生的 product_name），计算值取自预览响应。
 */
interface TradeConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 被确认的交易（列表行；现金平台由配对 CASH 腿派生后传入） */
  trade: Trade | null;
  cashPlatformCode?: string | null;
  platformNameMap: Map<string, string>;
  onConfirm: () => void;
  isConfirming?: boolean;
}

export function TradeConfirmDialog({
  open,
  onOpenChange,
  trade,
  cashPlatformCode,
  platformNameMap,
  onConfirm,
  isConfirming = false,
}: TradeConfirmDialogProps) {
  // staleTime=0：重开弹窗命中缓存时会后台 refetch，isFetching 期间同样视为加载中，
  // 防止基于过期预览值确认（预览==确认）
  const { data, isLoading, isFetching, error } = useTradePreview(trade?.id ?? null, open);
  const preview = data?.preview;
  const isBuy = trade?.trade_type === "buy";
  const productName = formatProductName(trade?.product_name, trade?.product_code);

  return (
    <ConfirmInfoDialog
      open={open}
      onOpenChange={onOpenChange}
      title={isBuy ? "确认买入" : "确认卖出"}
      description="请核对以下信息与预览值，确认后将不可直接修改"
      isLoading={isLoading || isFetching}
      error={error ? getErrorMessage(error, "预览请求失败") : null}
      onConfirm={onConfirm}
      isConfirming={isConfirming}
    >
      {trade && preview && (
        <>
          <InfoRow label="操作类型" value={isBuy ? "买入" : "卖出"} />
          <InfoRow label="产品" value={productName} />
          <InfoRow label="市场" value={formatMarketName(trade.market)} />
          <InfoRow
            label="交易平台"
            value={trade.platform_code ? platformNameMap.get(trade.platform_code) ?? trade.platform_code : "--"}
          />
          <InfoRow
            label="现金平台"
            value={cashPlatformCode ? platformNameMap.get(cashPlatformCode) ?? cashPlatformCode : "--"}
          />
          <InfoRow label="金额" value={formatCurrency(preview.amount)} />
          <InfoRow label="份额" value={formatSharesUnit(preview.shares)} />
          <InfoRow label="价格" value={formatNav(preview.price)} />
          <InfoRow label="手续费" value={formatCurrency(preview.fee)} />
          <InfoRow label="交易日期" value={formatDate(trade.trade_date)} />
          <InfoRow label="确认日期" value={preview.confirm_date ? formatDate(preview.confirm_date) : "--"} />
        </>
      )}
    </ConfirmInfoDialog>
  );
}
