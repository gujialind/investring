"use client";

import { ConfirmInfoDialog, InfoRow } from "@/components/shared/ConfirmInfoDialog";
import { useShareChangeEventPreview } from "@/hooks/useShareChangeEvent";
import { getErrorMessage } from "@/lib/api";
import type { ShareChangeEvent } from "@/types/share-change-event";
import type { EventType } from "@/types/common";
import { formatCurrency, formatSharesUnit, formatDate, formatProductName } from "@/lib/utils";

export const EVENT_TYPE_LABELS: Record<EventType, string> = {
  cash_dividend: "现金分红",
  reinvest_dividend: "分红再投资",
  share_split: "份额拆分",
  share_merge: "份额合并",
  bonus_share: "红股送股",
  forced_adjustment: "强制调整",
};

/**
 * 份额变动事件确认信息核对弹窗（#248 → #424）：
 * 打开时拉取后端确认预览（与真实确认共用计算实现），确认按钮二次点击才发起确认。
 *
 * #424 修正的前提：自动计算型事件（分红再投资/拆分/合并/送股）的
 * shares_change/cash_change **不是**录入时落库的，而在 confirm 时才计算，
 * 故 pending 行这两列为 NULL——直接渲染会兜底成误导性的 `0.00`。
 * 记录字段取自列表行（含读侧派生的 product_name），计算值取自预览响应。
 */
interface EventConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  event: ShareChangeEvent | null;
  platformNameMap: Map<string, string>;
  onConfirm: () => void;
  isConfirming?: boolean;
}

export function EventConfirmDialog({
  open,
  onOpenChange,
  event,
  platformNameMap,
  onConfirm,
  isConfirming = false,
}: EventConfirmDialogProps) {
  // staleTime=0：重开弹窗命中缓存时会后台 refetch，isFetching 期间同样视为加载中，
  // 防止基于过期预览值确认（预览==确认）
  const { data, isLoading, isFetching, error } = useShareChangeEventPreview(
    event?.id ?? null,
    open,
  );
  const preview = data?.preview;
  // 产品名直接取列表行自带的 product_name（#342 后端读侧派生）：
  // 替代原按单条事件懒加载 useProduct 的权宜（#257）；缺失时回退裸代码
  const getProductName = () => formatProductName(event?.product_name, event?.product_code);

  return (
    <ConfirmInfoDialog
      open={open}
      onOpenChange={onOpenChange}
      title="确认份额变动事件"
      description="请核对以下信息与预览值，确认后将生效"
      isLoading={isLoading || isFetching}
      error={error ? getErrorMessage(error, "预览请求失败") : null}
      onConfirm={onConfirm}
      isConfirming={isConfirming}
    >
      {/* preview 守卫：重开弹窗命中缓存时先渲染的是上一次的 data，无守卫会串台显示旧事件的值 */}
      {event && preview && (
        <>
          <InfoRow label="事件类型" value={EVENT_TYPE_LABELS[event.event_type] || event.event_type} />
          <InfoRow label="产品" value={getProductName()} />
          <InfoRow
            label="平台"
            value={
              event.platform_code
                ? platformNameMap.get(event.platform_code) ?? event.platform_code
                : "--"
            }
          />
          <InfoRow label="份额变化" value={formatSharesUnit(preview.shares_change)} />
          <InfoRow label="现金变化" value={formatCurrency(preview.cash_change)} />
          <InfoRow label="权益登记日" value={formatDate(event.entitlement_date)} />
          <InfoRow label="除息日" value={formatDate(event.ex_date)} />
        </>
      )}
    </ConfirmInfoDialog>
  );
}
