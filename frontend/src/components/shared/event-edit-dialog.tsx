"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { DatePicker } from "@/components/ui/date-picker";
import { Loader2 } from "lucide-react";
import { toDateOnly, parseDateOnly, formatDate, formatMarketName, formatProductName } from "@/lib/utils";
import type { ShareChangeEvent, ShareChangeEventUpdate } from "@/types/share-change-event";
import { EVENT_TYPE_LABELS } from "@/components/shared/event-confirm-dialog";

/**
 * 份额变动事件编辑弹窗（#342）：仅 pending 父记录可编辑。
 * 可改字段以后端 ShareChangeEventUpdate 为准；event_type/产品/市场/平台只读
 * （后端不开放直改）。模式参照调仓/申赎编辑弹窗。
 */
interface EventEditDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  event: ShareChangeEvent | null;
  platformNameMap: Map<string, string>;
  isSaving: boolean;
  onSubmit: (payload: ShareChangeEventUpdate) => void;
}

interface EditFormState {
  ex_date: string;
  entitlement_date: string;
  cash_pay_date: string;
  /** 再投资份额确认日（#743）：空串 = 未填/清空 */
  share_confirm_date: string;
  shares_before: string;
  shares_change: string;
  shares_after: string;
  cash_change: string;
  div_cash: string;
  reinvest_nav: string;
  ratio: string;
  notes: string;
}

const numToStr = (v: number | null | undefined) => (v == null ? "" : String(v));

function toForm(event: ShareChangeEvent): EditFormState {
  return {
    ex_date: event.ex_date,
    entitlement_date: event.entitlement_date,
    cash_pay_date: event.cash_pay_date ?? "",
    share_confirm_date: event.share_confirm_date ?? "",
    shares_before: numToStr(event.shares_before),
    shares_change: numToStr(event.shares_change),
    shares_after: numToStr(event.shares_after),
    cash_change: numToStr(event.cash_change),
    div_cash: numToStr(event.div_cash),
    reinvest_nav: numToStr(event.reinvest_nav),
    ratio: numToStr(event.ratio),
    notes: event.notes ?? "",
  };
}

const NUM_FIELDS = [
  "shares_before",
  "shares_change",
  "shares_after",
  "cash_change",
  "div_cash",
  "reinvest_nav",
  "ratio",
] as const;

export function EventEditDialog({
  open,
  onOpenChange,
  event,
  platformNameMap,
  isSaving,
  onSubmit,
}: EventEditDialogProps) {
  const [form, setForm] = useState<EditFormState | null>(null);
  const [navError, setNavError] = useState<string | null>(null);
  const [shareConfirmDateError, setShareConfirmDateError] = useState<string | null>(null);

  useEffect(() => {
    if (open && event) setForm(toForm(event));
    if (!open) setForm(null);
    setNavError(null);
    setShareConfirmDateError(null);
  }, [open, event]);

  if (!event || !form) return null;

  const setField = (patch: Partial<EditFormState>) => setForm({ ...form, ...patch });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    // 仅组装非空数字字段（后端 exclude_unset 语义，空串不入 payload 避免误清）；
    // 登记/除息日期恒带；notes 恒带（空串可清空备注）
    const payload: ShareChangeEventUpdate = {
      ex_date: form.ex_date,
      entitlement_date: form.entitlement_date,
      notes: form.notes,
    };
    if (event.event_type === "cash_dividend") {
      payload.cash_pay_date = form.cash_pay_date || null;
    }
    for (const field of NUM_FIELDS) {
      const raw = form[field].trim();
      if (raw !== "") payload[field] = parseFloat(raw);
    }
    if (event.event_type === "reinvest_dividend") {
      // #744：再投资净值留空 = 确认时取除息日净值，需显式发 null 才能清掉存量值（如历史 0）
      const raw = form.reinvest_nav.trim();
      if (raw === "") {
        payload.reinvest_nav = null;
      } else if (!(parseFloat(raw) > 0)) {
        setNavError("再投资净值必须大于 0；净值未发布可留空");
        return;
      }
      // #743：份额确认日（份额到账日）留空 = 按除息日生效，清空同样要显式发 null；
      // 非空时须晚于除息日（与后端 INVALID_DATE_ORDER 同口径，交易日判据交后端）
      const confirmRaw = form.share_confirm_date.trim();
      if (confirmRaw !== "" && !(confirmRaw > form.ex_date)) {
        setShareConfirmDateError("再投资份额确认日必须晚于除息日");
        return;
      }
      payload.share_confirm_date = confirmRaw || null;
    }
    onSubmit(payload);
  };

  const productName = formatProductName(event.product_name, event.product_code, event.display_code);
  const platformName = event.platform_code
    ? platformNameMap.get(event.platform_code) ?? event.platform_code
    : "全部";

  const gridCls = "grid grid-cols-1 gap-4 sm:grid-cols-2";

  return (
    <Dialog open={open} onOpenChange={onOpenChange} modal={false}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>编辑份额变动事件</DialogTitle>
          <DialogDescription>事件类型、产品、市场、平台不可修改</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit}>
          <div className="space-y-4 py-4">
            {/* 只读摘要：后端 Update schema 不开放的字段 */}
            <div className={gridCls}>
              <div className="space-y-2">
                <Label>事件类型</Label>
                <p className="text-sm text-muted-foreground">
                  {EVENT_TYPE_LABELS[event.event_type] || event.event_type}
                </p>
              </div>
              <div className="space-y-2">
                <Label>产品</Label>
                <p className="text-sm text-muted-foreground">{productName}</p>
              </div>
              <div className="space-y-2">
                <Label>市场</Label>
                <p className="text-sm text-muted-foreground">{formatMarketName(event.market)}</p>
              </div>
              <div className="space-y-2">
                <Label>平台</Label>
                <p className="text-sm text-muted-foreground">{platformName}</p>
              </div>
            </div>

            {/* 日期字段顺序 = 业务时序（#355）：权益登记日先于除息日，与新建表单及确认弹窗一致 */}
            <div className={gridCls}>
              <div className="space-y-2">
                <Label htmlFor="edit_entitlement_date">权益登记日</Label>
                <DatePicker
                  id="edit_entitlement_date"
                  date={parseDateOnly(form.entitlement_date)}
                  onSelect={(date) => setField({ entitlement_date: toDateOnly(date) })}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="edit_ex_date">除息日</Label>
                <DatePicker
                  id="edit_ex_date"
                  date={parseDateOnly(form.ex_date)}
                  onSelect={(date) => setField({ ex_date: toDateOnly(date) })}
                />
              </div>
            </div>

            {event.event_type === "cash_dividend" && (
              <div className="space-y-2" data-testid="cash-pay-date-field">
                <Label htmlFor="edit_cash_pay_date">现金到账日（可选）</Label>
                <DatePicker
                  id="edit_cash_pay_date"
                  date={parseDateOnly(form.cash_pay_date)}
                  onSelect={(date) => setField({ cash_pay_date: toDateOnly(date) })}
                  placeholder="默认除息日"
                />
                <p className="text-xs text-muted-foreground">
                  有效到账日：{formatDate(form.cash_pay_date || form.ex_date)}
                  {!form.cash_pay_date && "（默认除息日）"}。清空后按除息日到账；可选非交易日，不得早于除息日。
                </p>
              </div>
            )}

            {/* 按事件类型渲染可编辑数值字段（镜像新建弹窗字段集） */}
            {(event.event_type === "cash_dividend" || event.event_type === "reinvest_dividend") && (
              <div className={gridCls}>
                <div className="space-y-2">
                  <Label htmlFor="edit_div_cash">每份分红金额（元）</Label>
                  <Input
                    id="edit_div_cash"
                    type="number"
                    step="0.0001"
                    value={form.div_cash}
                    onChange={(e) => setField({ div_cash: e.target.value })}
                  />
                </div>
                {event.event_type === "reinvest_dividend" && (
                  <div className="space-y-2">
                    <Label htmlFor="edit_reinvest_nav">再投资净值</Label>
                    <Input
                      id="edit_reinvest_nav"
                      type="number"
                      step="0.0001"
                      min="0"
                      placeholder="留空则确认时取除息日净值（需先同步）"
                      value={form.reinvest_nav}
                      onChange={(e) => {
                        setNavError(null);
                        setField({ reinvest_nav: e.target.value });
                      }}
                    />
                    {navError && <p className="text-xs text-destructive">{navError}</p>}
                  </div>
                )}
              </div>
            )}

            {/* 份额确认日：仅红利再投资可编辑（#743，其他类型后端 422 INVALID_PARAM） */}
            {event.event_type === "reinvest_dividend" && (
              <div className="space-y-2" data-testid="share-confirm-date-field">
                <Label htmlFor="edit_share_confirm_date">再投资份额确认日（可选）</Label>
                <DatePicker
                  id="edit_share_confirm_date"
                  date={parseDateOnly(form.share_confirm_date)}
                  onSelect={(date) => {
                    setShareConfirmDateError(null);
                    setField({ share_confirm_date: date ? toDateOnly(date) : "" });
                  }}
                  placeholder="默认除息日到账"
                />
                {shareConfirmDateError && (
                  <p className="text-xs text-destructive">{shareConfirmDateError}</p>
                )}
                <p className="text-xs text-muted-foreground">
                  份额到账日：须为交易日且晚于除息日；清空则份额按除息日到账。填写后除息日到确认日之间
                  红利记应收股利（计市值、不计份额），权益登记日也可与除息日同日。
                </p>
              </div>
            )}

            {(event.event_type === "share_split" ||
              event.event_type === "share_merge" ||
              event.event_type === "bonus_share") && (
              <div className="space-y-2">
                <Label htmlFor="edit_ratio">比例</Label>
                <Input
                  id="edit_ratio"
                  type="number"
                  step="0.0001"
                  value={form.ratio}
                  onChange={(e) => setField({ ratio: e.target.value })}
                  placeholder="如：拆分比例 2.0 表示1份变2份"
                />
              </div>
            )}

            {event.event_type === "forced_adjustment" && (
              <div className={gridCls}>
                <div className="space-y-2">
                  <Label htmlFor="edit_shares_before">变动前份额</Label>
                  <Input
                    id="edit_shares_before"
                    type="number"
                    step="0.01"
                    value={form.shares_before}
                    onChange={(e) => setField({ shares_before: e.target.value })}
                    placeholder="可选"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="edit_shares_change">份额变化</Label>
                  <Input
                    id="edit_shares_change"
                    type="number"
                    step="0.01"
                    value={form.shares_change}
                    onChange={(e) => setField({ shares_change: e.target.value })}
                    placeholder="正数增加，负数减少（份额 2 位小数）"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="edit_shares_after">变动后份额</Label>
                  <Input
                    id="edit_shares_after"
                    type="number"
                    step="0.01"
                    value={form.shares_after}
                    onChange={(e) => setField({ shares_after: e.target.value })}
                    placeholder="可选"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="edit_cash_change">现金变化</Label>
                  <Input
                    id="edit_cash_change"
                    type="number"
                    step="0.01"
                    value={form.cash_change}
                    onChange={(e) => setField({ cash_change: e.target.value })}
                    placeholder="正数增加，负数减少"
                  />
                </div>
              </div>
            )}

            <div className="space-y-2">
              <Label htmlFor="edit_notes">备注</Label>
              <Input
                id="edit_notes"
                value={form.notes}
                onChange={(e) => setField({ notes: e.target.value })}
                placeholder="可选"
              />
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="submit" disabled={isSaving}>
              {isSaving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
