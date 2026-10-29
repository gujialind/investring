"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { InfoRow } from "@/components/shared/ConfirmInfoDialog";
import { EVENT_TYPE_LABELS } from "@/components/shared/event-confirm-dialog";
import {
  formatAmount4,
  formatCurrency,
  formatDate,
  formatDateTime,
  formatMarketName,
  formatNav,
  formatNumber,
  formatProductName,
  formatSharesUnit,
  getStatusBadgeVariant,
} from "@/lib/utils";
import type { ShareChangeEvent } from "@/types/share-change-event";
import type { EventType } from "@/types/common";

// 事件状态文案单点（#754：自 ShareChangeEventsContent 本地常量迁移，列表列与详情弹窗共用）
export const EVENT_STATUS_LABELS: Record<string, string> = {
  pending: "待确认",
  confirmed: "已确认",
  cancelled: "已取消",
};

const EVENT_SOURCE_LABELS: Record<string, string> = {
  manual: "手动",
  tushare: "Tushare",
};

interface EventDetailDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  event: ShareChangeEvent | null;
  platformNameMap: Map<string, string>;
}

/**
 * 份额变动事件只读详情弹窗（#754）：列表行「查看详情」入口，全状态可查（含
 * pending / confirmed / cancelled，父/子记录）。数据 = 被点击列表行已持有的对象，
 * **零新请求、不拉 /preview**——「确认将算出的值」仍由确认弹窗（#424）独占展示，
 * 详情只照实回查已落库字段（含存量异常值，如 reinvest_nav=0.0000，不美化不改写）。
 * 不适用当前事件类型的字段整行隐藏（spec §5.1）；适用但为 null 的显示 --，
 * formatter 兜底天然满足、不折 0.00。写操作一律回到列表操作列。
 */
export function EventDetailDialog({ open, onOpenChange, event, platformNameMap }: EventDetailDialogProps) {
  if (!open || !event) return null;

  const t = event.event_type;
  const isCashDiv = t === "cash_dividend";
  const isReinvest = t === "reinvest_dividend";
  const isCapital = t === "share_split" || t === "share_merge" || t === "bonus_share";
  const isForced = t === "forced_adjustment";
  const isPendingMethod = t === "dividend_pending";
  // dividend_pending「输入可见、计算全 --」：方式确认时才选定，计算组恒 --（spec §5.1）
  const showCalc = isCashDiv || isReinvest || isCapital || isForced || isPendingMethod;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[560px]" data-testid="event-detail-dialog">
        <DialogHeader>
          <DialogTitle>事件详情</DialogTitle>
          <DialogDescription>只读视图 · 编辑 / 确认 / 取消请使用列表操作</DialogDescription>
        </DialogHeader>
        <div className="divide-y divide-border">
          {/* ---- 基本信息 ---- */}
          <div>
            <GroupHeading>基本信息</GroupHeading>
            <InfoRow
              label="事件类型"
              value={<span data-testid="detail-event-type">{EVENT_TYPE_LABELS[t as EventType] || t}</span>}
            />
            <InfoRow
              label="产品"
              value={
                <span data-testid="detail-product">
                  {formatProductName(event.product_name, event.product_code, event.display_code)}
                </span>
              }
            />
            <InfoRow
              label="市场"
              value={<span data-testid="detail-market">{formatMarketName(event.market)}</span>}
            />
            <InfoRow
              label="平台"
              value={
                <span data-testid="detail-platform">
                  {event.platform_code
                    ? platformNameMap.get(event.platform_code) ?? event.platform_code
                    : "全部"}
                </span>
              }
            />
            <InfoRow
              label="状态"
              value={
                <span data-testid="detail-status">
                  <Badge variant={getStatusBadgeVariant(event.status)}>
                    {EVENT_STATUS_LABELS[event.status] || event.status}
                  </Badge>
                </span>
              }
            />
            <InfoRow
              label="来源"
              value={
                <span data-testid="detail-event-source">
                  {EVENT_SOURCE_LABELS[event.event_source ?? ""] ?? event.event_source ?? "--"}
                </span>
              }
            />
            <InfoRow
              label="备注"
              value={
                <span data-testid="detail-notes" className="whitespace-pre-wrap break-words">
                  {event.notes || "--"}
                </span>
              }
              valueClassName="min-w-0"
            />
          </div>

          {/* ---- 日期 ---- */}
          <div>
            <GroupHeading>日期</GroupHeading>
            <InfoRow
              label="权益登记日"
              value={<span data-testid="detail-entitlement-date">{formatDate(event.entitlement_date)}</span>}
            />
            <InfoRow
              label="除息日"
              value={<span data-testid="detail-ex-date">{formatDate(event.ex_date)}</span>}
            />
            {isCashDiv && (
              <InfoRow
                label="现金到账日"
                value={
                  <span data-testid="detail-cash-pay-date">
                    <span className="whitespace-nowrap">{formatDate(event.cash_pay_date ?? "")}</span>
                    {event.cash_pay_date == null && (
                      // 值保持 --，注记带生效日：与列表列/确认弹窗展示的具体生效日对齐（#804 复审）
                      <span className="ml-1 text-xs text-muted-foreground">
                        （默认除息日 {formatDate(event.ex_date)}）
                      </span>
                    )}
                  </span>
                }
              />
            )}
            {isReinvest && (
              <InfoRow
                label="再投资份额确认日"
                value={
                  <span data-testid="detail-share-confirm-date">
                    <span className="whitespace-nowrap">{formatDate(event.share_confirm_date ?? "")}</span>
                    {event.share_confirm_date == null && (
                      <span className="ml-1 text-xs text-muted-foreground">
                        （默认除息日 {formatDate(event.ex_date)}）
                      </span>
                    )}
                  </span>
                }
              />
            )}
          </div>

          {/* ---- 输入 ---- */}
          {(isCashDiv || isReinvest || isCapital || isPendingMethod) && (
            <div>
              <GroupHeading>输入</GroupHeading>
              {(isCashDiv || isReinvest || isPendingMethod) && (
                <InfoRow
                  label="每份分红"
                  value={<span data-testid="detail-div-cash">{formatAmount4(event.div_cash)}</span>}
                />
              )}
              {isReinvest && (
                <InfoRow
                  label="再投资净值"
                  value={
                    <span data-testid="detail-reinvest-nav">
                      {/* 存量 0.0000 照实显示（详情价值 = 让录错的输入可见）；
                          null 仅 pending 注「确认时取除息日净值」（#744 口径） */}
                      <span className="whitespace-nowrap">{formatNav(event.reinvest_nav)}</span>
                      {event.reinvest_nav == null && event.status === "pending" && (
                        <span className="ml-1 text-xs text-muted-foreground">（确认时取除息日净值）</span>
                      )}
                    </span>
                  }
                />
              )}
              {isCapital && (
                <InfoRow
                  label="比例"
                  value={<span data-testid="detail-ratio">{formatNumber(event.ratio, 4)}</span>}
                />
              )}
            </div>
          )}

          {/* ---- 计算结果与时间 ---- */}
          <div>
            <GroupHeading>计算结果与时间</GroupHeading>
            {(isCashDiv || isReinvest || isPendingMethod) && (
              <InfoRow
                label="参与分红份额"
                value={<span data-testid="detail-entitlement-shares">{formatSharesUnit(event.entitlement_shares)}</span>}
              />
            )}
            {(isCashDiv || isReinvest || isPendingMethod) && (
              <InfoRow
                label="红利金额"
                value={<span data-testid="detail-dividend-amount">{formatCurrency(event.dividend_amount)}</span>}
              />
            )}
            {showCalc && (
              <InfoRow
                label="份额变化"
                value={<span data-testid="detail-shares-change">{formatSharesUnit(event.shares_change)}</span>}
              />
            )}
            {(isCashDiv || isReinvest || isCapital || isForced) && (
              <>
                <InfoRow
                  label="调整前份额"
                  value={<span data-testid="detail-shares-before">{formatSharesUnit(event.shares_before)}</span>}
                />
                <InfoRow
                  label="调整后份额"
                  value={<span data-testid="detail-shares-after">{formatSharesUnit(event.shares_after)}</span>}
                />
              </>
            )}
            {showCalc && (
              <InfoRow
                label="现金变化"
                value={<span data-testid="detail-cash-change">{formatCurrency(event.cash_change)}</span>}
              />
            )}
            <InfoRow
              label="创建时间"
              value={<span data-testid="detail-created-at">{event.created_at ? formatDateTime(event.created_at) : "--"}</span>}
            />
            <InfoRow
              label="确认时间"
              value={<span data-testid="detail-confirmed-at">{event.confirmed_at ? formatDateTime(event.confirmed_at) : "--"}</span>}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 组标题：visual-spec §5 辅助档（text-xs），不新造字号 */
function GroupHeading({ children }: { children: React.ReactNode }) {
  return <div className="pb-1 text-xs font-medium text-muted-foreground">{children}</div>;
}
