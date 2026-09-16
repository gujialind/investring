"use client";

import { useEffect, useState } from "react";
import { ConfirmInfoDialog, InfoRow } from "./ConfirmInfoDialog";
import { DatePicker } from "@/components/ui/date-picker";
import { Label } from "@/components/ui/label";
import SearchablePlatformSelect from "@/components/shared/SearchablePlatformSelect";
import { useTradePreview } from "@/hooks/useTrade";
import { getErrorMessage } from "@/lib/api";
import type { Platform } from "@/types/platform";
import type { Trade, TradeConfirmParams } from "@/types/trade";
import {
  formatCurrency,
  formatSharesUnit,
  formatNav,
  formatDate,
  formatMarketName,
  formatProductName,
  parseDateOnly,
  toDateOnly,
} from "@/lib/utils";

/** 平台 code → 名称；未加载/未命中时回落 code 本身（与列表平台列同口径） */
function platformName(platforms: Platform[], code?: string | null): string {
  if (!code) return "--";
  return platforms.find((p) => p.code === code)?.name ?? code;
}

/**
 * 调仓交易确认信息核对弹窗（#248，卖出到账信息 #493）：
 * 打开时拉取后端既有确认预览（与真实确认共用计算实现）。
 *
 * 记录字段取自基金腿自身的读侧派生值（`cash_platform_code` / `cash_confirm_date` 由
 * 后端按配对 CASH 腿批量派生，**不再从当前页里寻找配对 CASH 行**——分页/筛选拆散
 * 或半确认组时同样完整）；计算值取自预览响应。
 *
 * 卖出额外提供**到账日期**与**到账平台**录入，两者都进预览 query key：
 * 用户一改即重新预览，「预览值 == 确认值」在新增输入维度上仍成立。
 * 缺省（用户未选）不传参、由后端取缺省值 A = C、平台 = 基金腿平台，并以预览回传的
 * 有效值回显；用户选回与缺省相同的值时归一为「不传」——同一有效选项只对应一个缓存键。
 */
interface TradeConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 被确认的基金腿（列表行；含读侧派生的 product_name 与现金信息） */
  trade: Trade | null;
  /** 到账平台选择框数据源（与列表筛选同一份平台全量列表） */
  platforms: Platform[];
  onConfirm: (params: TradeConfirmParams) => void;
  isConfirming?: boolean;
}

export function TradeConfirmDialog({
  open,
  onOpenChange,
  trade,
  platforms,
  onConfirm,
  isConfirming = false,
}: TradeConfirmDialogProps) {
  // 本地到账选择（#493 §3.4.2）：undefined = 用户未显式选择 → 交给后端取缺省并回显
  const [cashChoice, setCashChoice] = useState<{
    date?: string;
    platform?: string;
  }>({});

  // 切换交易或重开弹窗一律重置本地选择：否则上一笔卖出的到账日/到账平台会静默带到
  // 下一笔，在「确认另一笔」时提交出错误的到账信息。依赖 [open, trade?.id] 覆盖两种入口
  // （关闭再开 → open 变化；确认后被父级清空 → trade.id 变化）。
  useEffect(() => {
    if (!open) return;
    setCashChoice({});
  }, [open, trade?.id]);

  const isBuy = trade?.trade_type === "buy";
  // 归一：与后端缺省同值时按「未传」处理，query key 与请求参数都收敛到唯一形态
  const defaultCashDate = trade?.cash_confirm_date ?? undefined;
  const defaultCashPlatform = trade?.cash_platform_code ?? undefined;
  const requestedDate =
    cashChoice.date !== undefined && cashChoice.date !== defaultCashDate
      ? cashChoice.date
      : undefined;
  const requestedPlatform =
    cashChoice.platform !== undefined && cashChoice.platform !== defaultCashPlatform
      ? cashChoice.platform
      : undefined;

  const { data, isLoading, isFetching, error } = useTradePreview(trade?.id ?? null, open, {
    cash_confirm_date: requestedDate,
    cash_platform_code: requestedPlatform,
  });

  const preview = data?.preview;
  const productName = formatProductName(trade?.product_name, trade?.product_code);
  // 有效值以预览回传为准（用户未选时即后端缺省：A = C、平台 = 基金腿平台）
  const shownDate = requestedDate ?? preview?.cash_confirm_date ?? defaultCashDate;
  const shownPlatform =
    requestedPlatform ?? preview?.cash_platform_code ?? defaultCashPlatform;

  return (
    <ConfirmInfoDialog
      open={open}
      onOpenChange={onOpenChange}
      title={isBuy ? "确认买入" : "确认卖出"}
      description="请核对以下信息与预览值，确认后将不可直接修改"
      isLoading={isLoading || isFetching}
      error={error ? getErrorMessage(error, "预览请求失败") : null}
      onConfirm={() =>
        onConfirm({
          cash_confirm_date: requestedDate,
          cash_platform_code: requestedPlatform,
        })
      }
      isConfirming={isConfirming}
    >
      {trade && preview && (
        <>
          <InfoRow label="操作类型" value={isBuy ? "买入" : "卖出"} />
          <InfoRow label="产品" value={productName} />
          <InfoRow label="市场" value={formatMarketName(trade.market)} />
          <InfoRow label="交易平台" value={platformName(platforms, trade.platform_code)} />
          <InfoRow
            label={isBuy ? "扣款平台" : "到账平台"}
            value={platformName(platforms, shownPlatform)}
          />
          <InfoRow label="金额" value={formatCurrency(preview.amount)} />
          <InfoRow label="份额" value={formatSharesUnit(preview.shares)} />
          <InfoRow label="价格" value={formatNav(preview.price)} />
          <InfoRow label="手续费" value={formatCurrency(preview.fee)} />
          <InfoRow label="交易日期" value={formatDate(trade.trade_date)} />
          <InfoRow
            label="确认日期"
            value={preview.confirm_date ? formatDate(preview.confirm_date) : "--"}
          />
          {isBuy ? (
            // 买入扣款日创建期即固定为下单日 T，此处只读回显、不提供修改入口
            <InfoRow
              label="扣款日期"
              value={preview.cash_confirm_date ? formatDate(preview.cash_confirm_date) : "--"}
            />
          ) : (
            <div className="space-y-2 py-2">
              <Label htmlFor="cash_confirm_date">到账日期</Label>
              <DatePicker
                id="cash_confirm_date"
                date={shownDate ? parseDateOnly(shownDate) : undefined}
                onSelect={(date) =>
                  // 清除日期（X）返回空串 → 归一为 undefined（= 交回后端缺省 A = C）
                  setCashChoice((prev) => ({ ...prev, date: toDateOnly(date) || undefined }))
                }
                placeholder="到账日期"
                showTradingDays
              />
              <p className="text-xs text-muted-foreground">
                缺省为基金确认日当天；到账日之前该笔资金计入「卖出在途」，不计入可用现金。
              </p>
              <Label htmlFor="cash_platform_code">到账平台</Label>
              <SearchablePlatformSelect
                platforms={platforms}
                value={shownPlatform ?? null}
                onChange={(v) =>
                  setCashChoice((prev) => ({ ...prev, platform: v ?? undefined }))
                }
                specialOptionLabel="同交易平台"
                id="cash_platform_code"
              />
            </div>
          )}
        </>
      )}
    </ConfirmInfoDialog>
  );
}
