"use client";

import { useState } from "react";
import { ConfirmInfoDialog, InfoRow } from "@/components/shared/ConfirmInfoDialog";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { DatePicker } from "@/components/ui/date-picker";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useShareChangeEventPreview } from "@/hooks/useShareChangeEvent";
import { ApiException, getErrorMessage } from "@/lib/api";
import { normalizePreviewOption } from "@/lib/queryKeys";
import type { ShareChangeEvent, ShareChangeEventConfirmSelection } from "@/types/share-change-event";
import type { EventType } from "@/types/common";
import {
  formatAmount4,
  formatCurrency,
  formatDate,
  formatNav,
  formatProductName,
  formatSharesUnit,
  parseDateOnly,
  toDateOnly,
} from "@/lib/utils";

export const EVENT_TYPE_LABELS: Record<EventType, string> = {
  cash_dividend: "现金分红",
  reinvest_dividend: "分红再投资",
  dividend_pending: "分红方式待定",
  share_split: "份额拆分",
  share_merge: "份额合并",
  bonus_share: "红股送股",
  forced_adjustment: "强制调整",
};

// 展示/筛选包含待定类型，人工新建仍只有原六种，不从展示标签反推可创建类型。
export const CREATABLE_EVENT_TYPES: Exclude<EventType, "dividend_pending">[] = [
  "cash_dividend", "reinvest_dividend", "share_split", "share_merge", "bonus_share", "forced_adjustment",
];

interface EventConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  event: ShareChangeEvent | null;
  platformNameMap: Map<string, string>;
  onConfirm: (selection?: ShareChangeEventConfirmSelection) => void;
  isConfirming?: boolean;
}

/** 关窗即卸载候选，换事件按 id 重建；不靠 effect 在首帧之后清空旧选择。 */
export function EventConfirmDialog(props: EventConfirmDialogProps) {
  if (!props.open || !props.event) return null;
  return <EventConfirmSession key={props.event.id} {...props} event={props.event} />;
}

function EventConfirmSession({
  open,
  onOpenChange,
  event,
  platformNameMap,
  onConfirm,
  isConfirming = false,
}: EventConfirmDialogProps & { event: ShareChangeEvent }) {
  const canChooseMethod = event.status === "pending" && !event.parent_event_id && !!event.platform_code &&
    ["dividend_pending", "cash_dividend", "reinvest_dividend"].includes(event.event_type);
  const [choice, setChoice] = useState<ShareChangeEventConfirmSelection | undefined>(() => {
    if (!canChooseMethod) return undefined;
    if (event.event_type === "cash_dividend") {
      return { event_type: event.event_type, cash_pay_date: event.cash_pay_date };
    }
    if (event.event_type === "reinvest_dividend") {
      return { event_type: event.event_type, share_confirm_date: event.share_confirm_date };
    }
    return undefined;
  });
  // 带方式即完整候选，原有适用日期必须主动回填。现金到账日=除息日与省略同义；
  // 显式份额确认日=除息日却是非法输入，不能归一掉以绕过后端日期校验。
  const selection: ShareChangeEventConfirmSelection | undefined = choice && (
    choice.event_type === "cash_dividend"
      ? {
          event_type: choice.event_type,
          cash_pay_date: normalizePreviewOption(choice.cash_pay_date || undefined, event.ex_date),
        }
      : { event_type: choice.event_type, share_confirm_date: choice.share_confirm_date || undefined }
  );
  const needsMethod = event.event_type === "dividend_pending" && !selection;
  const { data, isLoading, isFetching, isSuccess, error } = useShareChangeEventPreview(
    event.id,
    open && !needsMethod,
    selection,
  );
  // 缓存重取也算 loading：只显示当前候选的成功结果，不能用上一候选/重开前的值确认。
  const loading = isLoading || isFetching;
  const preview = !needsMethod && !loading && isSuccess ? data?.preview : undefined;
  const isDividend = preview?.event_type === "cash_dividend" || preview?.event_type === "reinvest_dividend";
  const previewError = error && !needsMethod
    ? getErrorMessage(error, "预览请求失败") +
      (error instanceof ApiException && error.code === "MISSING_NAV"
        ? "，请在「产品」页同步该产品净值后重试"
        : "")
    : null;

  const methodInputs = canChooseMethod && (
    <div className="space-y-3 py-3">
      <div className="space-y-2">
        <Label htmlFor="confirm_dividend_method">分红方式</Label>
        <Select
          value={choice?.event_type ?? ""}
          disabled={isConfirming}
          onValueChange={(value) => {
            // 切换方式清掉原方式的专属日期，切回来也不恢复已经放弃的候选。
            if (value === "cash_dividend" || value === "reinvest_dividend") {
              setChoice({ event_type: value });
            }
          }}
        >
          <SelectTrigger id="confirm_dividend_method">
            <SelectValue placeholder="请选择分红方式" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="cash_dividend">现金分红</SelectItem>
            <SelectItem value="reinvest_dividend">分红再投资</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {choice?.event_type === "cash_dividend" && (
        <div className="space-y-2" data-testid="confirm-cash-pay-date-field">
          <Label htmlFor="confirm_cash_pay_date">现金到账日（可选）</Label>
          <DatePicker
            id="confirm_cash_pay_date"
            date={parseDateOnly(choice.cash_pay_date ?? "")}
            onSelect={(date) => setChoice({ ...choice, cash_pay_date: toDateOnly(date) || undefined })}
            disabled={isConfirming}
            placeholder="默认除息日"
          />
          <p className="text-xs text-muted-foreground">
            留空按除息日到账；可选非交易日，不得早于除息日。
          </p>
        </div>
      )}
      {choice?.event_type === "reinvest_dividend" && (
        <div className="space-y-2" data-testid="confirm-share-confirm-date-field">
          <Label htmlFor="confirm_share_confirm_date">再投资份额确认日（可选）</Label>
          <DatePicker
            id="confirm_share_confirm_date"
            date={parseDateOnly(choice.share_confirm_date ?? "")}
            onSelect={(date) => setChoice({ ...choice, share_confirm_date: toDateOnly(date) || undefined })}
            disabled={isConfirming}
            placeholder="默认除息日到账"
            showTradingDays
          />
          <p className="text-xs text-muted-foreground">
            须为交易日且晚于除息日；留空按除息日到账。同日登记与除息须填写份额确认日。
          </p>
        </div>
      )}
      <p className="text-xs text-muted-foreground">方式和日期仅在确认成功后保存，取消不会修改事件。</p>
    </div>
  );

  return (
    <ConfirmInfoDialog
      open={open}
      onOpenChange={onOpenChange}
      title="确认份额变动事件"
      description="请核对以下信息与预览值，确认后按对应日期生效"
      isLoading={loading}
      error={previewError}
      confirmDisabled={!preview || needsMethod}
      onConfirm={() => {
        if (preview && !needsMethod && !isConfirming) onConfirm(selection);
      }}
      isConfirming={isConfirming}
      inputSlot={methodInputs}
    >
      {needsMethod && (
        <Alert>
          <AlertDescription>请先选择分红方式，再核对预览并确认。</AlertDescription>
        </Alert>
      )}
      {preview && (
        <div data-testid="event-confirm-preview">
          <InfoRow label="事件类型" value={EVENT_TYPE_LABELS[preview.event_type as EventType] || preview.event_type} />
          <InfoRow label="产品" value={formatProductName(event.product_name, event.product_code, event.display_code)} />
          <InfoRow
            label="平台"
            value={event.platform_code ? platformNameMap.get(event.platform_code) ?? event.platform_code : "全部"}
          />
          {isDividend && (
            <>
              <InfoRow label="参与分红份额" value={formatSharesUnit(preview.entitlement_shares)} />
              <InfoRow label="每份分红" value={formatAmount4(preview.div_cash)} />
              <InfoRow label="红利金额" value={formatCurrency(preview.dividend_amount)} />
            </>
          )}
          {preview.event_type === "reinvest_dividend" && (
            <InfoRow
              label="再投资净值"
              value={
                <>
                  <span className="whitespace-nowrap">{formatNav(preview.reinvest_nav)}</span>
                  {preview.nav_date && (
                    <span className="ml-1 text-xs text-muted-foreground">
                      （{formatDate(preview.nav_date)} 净值）
                    </span>
                  )}
                </>
              }
            />
          )}
          <InfoRow label="份额变化" value={formatSharesUnit(preview.shares_change)} />
          <InfoRow label="现金变化" value={formatCurrency(preview.cash_change)} />
          <InfoRow label="权益登记日" value={formatDate(event.entitlement_date)} />
          <InfoRow label="除息日" value={formatDate(event.ex_date)} />
          {preview.event_type === "reinvest_dividend" && (
            <InfoRow
              label="再投资份额确认日"
              value={<span className="whitespace-nowrap">{formatDate(preview.share_effective_date)}</span>}
            />
          )}
          {preview.event_type === "cash_dividend" && (
            <InfoRow
              label="现金到账日"
              value={
                <>
                  <span className="whitespace-nowrap">{formatDate(preview.cash_effective_date)}</span>
                  {preview.cash_effective_date === event.ex_date && (
                    <span className="ml-1 text-xs text-muted-foreground">（默认除息日）</span>
                  )}
                </>
              }
            />
          )}
        </div>
      )}
    </ConfirmInfoDialog>
  );
}
