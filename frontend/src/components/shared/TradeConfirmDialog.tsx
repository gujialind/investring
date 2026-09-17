"use client";

import { useEffect, useState } from "react";
import { ConfirmInfoDialog, InfoRow } from "./ConfirmInfoDialog";
import { DatePicker } from "@/components/ui/date-picker";
import { Label } from "@/components/ui/label";
import SearchablePlatformSelect from "@/components/shared/SearchablePlatformSelect";
import { useTradePreview } from "@/hooks/useTrade";
import { getErrorMessage } from "@/lib/api";
import { normalizePreviewOption } from "@/lib/queryKeys";
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
 * 有效值回显；用户选回与**后端本次缺省**相同的值时归一为「不传」——同一有效选项
 * 只对应一个缓存键（基准见下方 `defaultCashDate` / `defaultCashPlatform`）。
 *
 * 到账输入块同时经 `ConfirmInfoDialog::errorSlot` 渲染：用户选中非交易日或早于 C 的
 * 日期会 422，此时**输入块不随错误一起消失**，可就地改到合法值（改完即重新预览）；
 * 日历侧再把非交易日/早于 C 的日期硬禁用，把 422 提前到选择时。
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
  // 归一基准用的「本次有效确认日 C」（#493 评审加固）：由预览回传、在此记住，
  // 因为 query key 必须在 useTradePreview **之前**算出、不能读同一 hook 的 data。
  const [effectiveConfirmDate, setEffectiveConfirmDate] = useState<string | undefined>();

  // 切换交易或重开弹窗一律重置本地选择：否则上一笔卖出的到账日/到账平台会静默带到
  // 下一笔，在「确认另一笔」时提交出错误的到账信息。依赖 [open, trade?.id] 覆盖两种入口
  // （关闭再开 → open 变化；确认后被父级清空 → trade.id 变化）。
  // 记住的 C 同属「上一笔的缺省」，一并清掉（否则会拿上一笔的确认日当基准）。
  useEffect(() => {
    if (!open) return;
    setCashChoice({});
    setEffectiveConfirmDate(undefined);
  }, [open, trade?.id]);

  const isBuy = trade?.trade_type === "buy";

  // 归一基准 = **后端本次会用的缺省**（#493 评审加固），不是 trade 上读侧派生的
  // `cash_confirm_date` / `cash_platform_code`——那是「已存在配对现金腿」的派生值，
  // 而本弹窗只对 pending 卖出开放、pending 卖出恒无现金腿 → 基准恒为 undefined，
  // 归一永不触发（多一次多余 preview + 同一有效选项分裂成两个缓存键）。
  // 缺省来源：到账平台 = 基金腿平台（后端 `resolve_cash_leg_plan` 的
  // `cash_platform_code or trade.platform_code`）；到账日 = 本次有效确认日 C。
  // C 只由交易的 confirm_date 决定、与本弹窗的输入无关，故记住的上一次回传值不会漂移；
  // preview 尚未回来时保持 undefined = 不归一（不臆造缺省，避免把用户的选择误归一掉）。
  const defaultCashDate = effectiveConfirmDate;
  const defaultCashPlatform = trade?.platform_code ?? undefined;
  const requestedDate = normalizePreviewOption(cashChoice.date, defaultCashDate);
  const requestedPlatform = normalizePreviewOption(cashChoice.platform, defaultCashPlatform);

  const { data, isLoading, isFetching, error } = useTradePreview(trade?.id ?? null, open, {
    cash_confirm_date: requestedDate,
    cash_platform_code: requestedPlatform,
  });

  const preview = data?.preview;
  // 只在预览成功回传 C 时更新：预览失败（如用户选中非法到账日）时 preview 为 undefined，
  // 此处刻意**保留**上一次的 C——到账日下界不该随一次失败一起消失（那正是「弹窗内无路可退」）。
  // 依赖含 `open, trade?.id`（#525）：本组件常驻挂载（Dialog 关闭不卸载 state），上方
  // reset effect 会在重开时把 C 清成 undefined，而同一笔的 C 值未变 → 只按值比较的
  // 依赖不会重跑，C 再也回不来（`dayDisabled` 下界与 `defaultCashDate` 归一双双失效）。
  // 声明序在 reset 之后 ⇒ 同一次提交内先清后回填，最终值即回填值。
  useEffect(() => {
    if (preview?.confirm_date) setEffectiveConfirmDate(preview.confirm_date);
  }, [preview?.confirm_date]); // TEMP-REVERT-B

  const productName = formatProductName(trade?.product_name, trade?.product_code);
  // 有效值以预览回传为准（用户未选时即后端缺省：A = C、平台 = 基金腿平台）
  const shownDate = requestedDate ?? preview?.cash_confirm_date ?? defaultCashDate;
  const shownPlatform =
    requestedPlatform ?? preview?.cash_platform_code ?? defaultCashPlatform;

  // 到账日下限 C（#493 评审加固）：A 早于 C 或落在非交易日，后端一律 422
  // （`resolve_cash_leg_plan`），故提前到选择时挡住。C 未知（preview 未回来）时不加
  // 下限、保持现状（不臆造）；非交易日由 DatePicker 的交易日历独立硬禁用。
  const dayDisabled = (day: Date) =>
    !!effectiveConfirmDate && toDateOnly(day) < effectiveConfirmDate;

  // 卖出到账输入块（到账日期 + 到账平台）：既作正常态内容，也作 error 态的输入槽位——
  // 见 ConfirmInfoDialog::errorSlot。两处是同一个节点，任一时刻只渲染一处。
  const arrivalInputs = trade && !isBuy && (
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
        dayDisabled={dayDisabled}
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
  );

  return (
    <ConfirmInfoDialog
      open={open}
      onOpenChange={onOpenChange}
      title={isBuy ? "确认买入" : "确认卖出"}
      description="请核对以下信息与预览值，确认后将不可直接修改"
      isLoading={isLoading || isFetching}
      error={error ? getErrorMessage(error, "预览请求失败") : null}
      errorSlot={arrivalInputs}
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
          {/* 卖出侧不重复渲染「到账平台」：下方 arrivalInputs 的可编辑选择框取的是同一个值，
              两处同名易被误读成两个字段；买入侧无输入块，故仍在此只读回显扣款平台 */}
          {isBuy && (
            <InfoRow label="扣款平台" value={platformName(platforms, shownPlatform)} />
          )}
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
            arrivalInputs
          )}
        </>
      )}
    </ConfirmInfoDialog>
  );
}
