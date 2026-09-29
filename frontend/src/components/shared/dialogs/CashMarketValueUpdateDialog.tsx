"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { DatePicker } from "@/components/ui/date-picker";
import SearchablePlatformSelect from "@/components/shared/SearchablePlatformSelect";
import ConfirmDialog from "@/components/shared/dialogs/ConfirmDialog";
import { Loader2, AlertTriangle, ArrowRight } from "lucide-react";
import { formatCurrency, toDateOnly } from "@/lib/utils";
import { getErrorMessage } from "@/lib/api";
import { usePlatformList } from "@/hooks/usePlatform";
import {
  useUpdateCashPosition,
  useListCashOverrides,
  useDeleteCashOverride,
} from "@/hooks/usePosition";
import { useUIStore } from "@/stores/uiStore";
import type { Platform } from "@/types/platform";

/** 金额上限：后端 manual_market_value 列为 Numeric(15,4)（11 位整数 + 4 位小数，服务层再量化 2 位），
 * 超出撞 500 而非可读拒绝，前端是唯一闸门 */
const MAX_CASH_AMOUNT = 99_999_999_999.9999;

interface CashMarketValueUpdateDialogProps {
  portfolioCode: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 预填平台（来自平台-产品详情页）；undefined 表示需用户选择 */
  defaultPlatformCode?: string;
}

/**
 * #595 §4.5：现金市值更新共享 Dialog。
 * 替换 positions 子页两份复刻表单；双端共用。
 * - 填写最新余额 → POST cash-position（绝对覆盖 manual_market_value）
 * - 展示已有覆盖记录与自然计算值对照，支持撤销（二次确认）
 * - 提交后透出 warnings，引导到快照追平入口
 */
export default function CashMarketValueUpdateDialog({
  portfolioCode,
  open,
  onOpenChange,
  defaultPlatformCode,
}: CashMarketValueUpdateDialogProps) {
  const addToast = useUIStore((state) => state.addToast);
  // page_size 100 与仓内其余平台下拉调用点对齐（后端默认 20，超出部分选不到）
  const { data: platformData } = usePlatformList({ page_size: 100 });
  const platforms: Platform[] = platformData?.items || [];

  // Form state
  const [selectedPlatform, setSelectedPlatform] = useState<string | null>(
    defaultPlatformCode ?? null,
  );
  const [selectedDate, setSelectedDate] = useState<Date | undefined>(undefined);
  const [cashAmount, setCashAmount] = useState("");

  // Success feedback state
  const [submitResult, setSubmitResult] = useState<{
    warnings: string[];
    requiresSnapshotRegen: boolean;
  } | null>(null);

  // 撤销二次确认目标（visual-spec §13：危险操作统一 AlertDialog 二次确认）
  const [pendingDelete, setPendingDelete] = useState<{
    platformCode: string;
    valueDate: string;
  } | null>(null);

  const updateCashPosition = useUpdateCashPosition(portfolioCode);
  const deleteOverride = useDeleteCashOverride(portfolioCode);

  // Query existing overrides for selected platform/date
  const overrideParams = selectedPlatform
    ? { platform_code: selectedPlatform }
    : undefined;
  const {
    data: overridesData,
    isError: overridesError,
    error: overridesErr,
    refetch: refetchOverrides,
  } = useListCashOverrides(portfolioCode, overrideParams, open && !!selectedPlatform);

  // Reset form when dialog opens/closes
  useEffect(() => {
    if (open) {
      setSelectedPlatform(defaultPlatformCode ?? null);
      setSelectedDate(undefined);
      setCashAmount("");
      setSubmitResult(null);
    }
  }, [open, defaultPlatformCode]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (!selectedPlatform) {
      addToast({ type: "error", title: "输入错误", message: "请选择平台" });
      return;
    }
    const amount = parseFloat(cashAmount);
    if (
      cashAmount === "" ||
      !Number.isFinite(amount) ||
      amount < 0 ||
      amount > MAX_CASH_AMOUNT
    ) {
      addToast({ type: "error", title: "输入错误", message: "请输入有效的金额" });
      return;
    }

    updateCashPosition.mutate(
      {
        amount,
        platformCode: selectedPlatform,
        // R-1：写入日显式取客户端今天（与面板匹配、撤销目标同一时钟）。缺省送 undefined
        // 时后端按 date.today() 兜底（position_service 全仓唯一一处），服务端/客户端时钟
        // 错开（容器 UTC vs 用户 UTC+8）会把覆盖写到相邻日、面板却显示「当日无覆盖」
        updateDate: toDateOnly(selectedDate ?? new Date()),
      },
      {
        onSuccess: (result) => {
          setSubmitResult({
            warnings: result.warnings || [],
            requiresSnapshotRegen: result.requires_snapshot_regen,
          });
          // 写入成功即提示并清空金额（与 requires_snapshot_regen 解耦，重算只是附加引导）；
          // 不清平台——同平台连续修正是常见操作，换平台时触发器已有显式选择动作
          setCashAmount("");
          addToast({ type: "success", title: "更新成功", message: "现金市值已更新" });
          refetchOverrides();
        },
        onError: (error: unknown) => {
          addToast({ type: "error", title: "更新失败", message: getErrorMessage(error, "请检查输入") });
        },
      },
    );
  };

  // Find matching override for current platform/date selection。
  // 未选日期按客户端今天参与过滤——与写入日（handleSubmit 显式送出）同一时钟
  const dateStr = toDateOnly(selectedDate ?? new Date());
  const matchingOverride = overridesData?.items?.find(
    (item) => item.platform_code === selectedPlatform && item.value_date === dateStr,
  );

  const confirmDeleteOverride = () => {
    if (!pendingDelete) return;
    deleteOverride.mutate(
      { platformCode: pendingDelete.platformCode, updateDate: pendingDelete.valueDate },
      {
        onSuccess: () => refetchOverrides(),
        onSettled: () => setPendingDelete(null),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* 高度上限交给 dialog.tsx 的内层滚动容器，**不得**在本节点加 overflow-y-auto：
          #191 方案 C 把日历弹层 Portal 注入 DialogContent 自身，父级 overflow 会把
          月历底行裁进裁剪盒——那点落在遮罩上，连人带表单一起关（#640 U-1）。 */}
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>更新现金市值</DialogTitle>
          <DialogDescription>
            手工填写最新余额，按 (组合, 平台, 日期) 绝对覆盖自然计算值
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Platform select */}
          <div className="space-y-2">
            <Label htmlFor="cash-platform">平台</Label>
            <SearchablePlatformSelect
              platforms={platforms}
              value={selectedPlatform}
              onChange={(v) => {
                setSelectedPlatform(v);
                setSubmitResult(null);
              }}
              placeholder="请选择平台"
              id="cash-platform"
            />
          </div>

          {/* Date picker */}
          <div className="space-y-2">
            <Label>更新日期</Label>
            <DatePicker
              date={selectedDate}
              onSelect={(d) => {
                setSelectedDate(d);
                setSubmitResult(null);
              }}
              placeholder="选择日期（默认今天）"
            />
            <p className="text-xs text-muted-foreground">
              只能选择交易日，非交易日将被拒绝
            </p>
          </div>

          {/* Amount input */}
          <div className="space-y-2">
            <Label htmlFor="cash-amount">当前金额（元）</Label>
            <Input
              id="cash-amount"
              type="number"
              step="0.01"
              min="0"
              value={cashAmount}
              onChange={(e) => setCashAmount(e.target.value)}
              placeholder="请输入当前现金金额"
              required
            />
          </div>

          {/* Override comparison：读取失败必须可见（§1.2③），与「无覆盖记录」可区分 */}
          {selectedPlatform && overridesError && (
            <div className="rounded-md border border-destructive/30 bg-destructive-soft p-3">
              <p className="text-sm text-destructive-foreground">
                覆盖记录读取失败：{getErrorMessage(overridesErr, "请稍后重试")}
              </p>
              <Button
                type="button"
                variant="link"
                size="sm"
                onClick={() => refetchOverrides()}
              >
                重试
              </Button>
            </div>
          )}
          {selectedPlatform && !overridesError && matchingOverride && (
            <div className="rounded-md border border-border bg-muted/30 p-3 space-y-2">
              <p className="text-sm font-medium">当日已有覆盖记录</p>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div>
                  <span className="text-muted-foreground">覆盖值：</span>
                  <span className="font-mono">{formatCurrency(matchingOverride.market_value)}</span>
                </div>
                <div>
                  <span className="text-muted-foreground">自然值：</span>
                  <span className="font-mono">
                    {matchingOverride.computed_value != null
                      ? formatCurrency(matchingOverride.computed_value)
                      : "--"}
                  </span>
                </div>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() =>
                  setPendingDelete({
                    platformCode: matchingOverride.platform_code,
                    valueDate: matchingOverride.value_date,
                  })
                }
                disabled={deleteOverride.isPending}
              >
                {deleteOverride.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
                撤销覆盖
              </Button>
            </div>
          )}

          {/* Submit result feedback */}
          {submitResult && (
            <div className="space-y-2">
              {submitResult.warnings.length > 0 && (
                <Alert className="border-warning/30 bg-warning-soft text-warning-foreground">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription>
                    <ul className="list-disc pl-4 text-sm">
                      {submitResult.warnings.map((w, i) => (
                        <li key={i}>{w}</li>
                      ))}
                    </ul>
                  </AlertDescription>
                </Alert>
              )}
              {submitResult.requiresSnapshotRegen && (
                <Alert>
                  <AlertDescription className="flex items-center gap-2 text-sm">
                    覆盖已写入，需重新生成快照才能在持仓中生效。
                    <Button asChild variant="outline" size="sm">
                      <Link href={`/portfolio/${portfolioCode}/snapshots`}>
                        前往快照管理 <ArrowRight className="ml-1 h-3 w-3" />
                      </Link>
                    </Button>
                  </AlertDescription>
                </Alert>
              )}
              {!submitResult.requiresSnapshotRegen && submitResult.warnings.length === 0 && (
                <Alert>
                  <AlertDescription className="text-sm text-success">
                    ✓ 更新成功
                  </AlertDescription>
                </Alert>
              )}
            </div>
          )}

          <DialogFooter className="flex-col sm:flex-row gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              className="w-full sm:w-auto"
            >
              取消
            </Button>
            <Button
              type="submit"
              disabled={updateCashPosition.isPending}
              className="w-full sm:w-auto"
            >
              {updateCashPosition.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              确认更新
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>

      {/* 撤销覆盖二次确认（危险操作，visual-spec §13） */}
      <ConfirmDialog
        open={!!pendingDelete}
        onOpenChange={(o) => !o && setPendingDelete(null)}
        title="撤销现金覆盖"
        description={`将删除 ${pendingDelete?.valueDate} 的手动覆盖记录，该日现金回退到自然计算值；若已生成快照需重算才生效。`}
        confirmText="确认撤销"
        onConfirm={confirmDeleteOverride}
      />
    </Dialog>
  );
}
