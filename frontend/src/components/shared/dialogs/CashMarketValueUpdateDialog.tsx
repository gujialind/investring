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

interface CashMarketValueUpdateDialogProps {
  portfolioCode: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 预填平台（来自平台-产品详情页）；undefined 表示需用户选择 */
  defaultPlatformCode?: string;
  /** 预填日期；undefined 表示默认今天 */
  defaultDate?: Date;
}

/**
 * #595 §4.5：现金市值更新共享 Dialog。
 * 替换 positions 子页两份复刻表单；双端共用。
 * - 填写最新余额 → POST cash-position（绝对覆盖 manual_market_value）
 * - 展示已有覆盖记录与自然计算值对照，支持撤销
 * - 提交后透出 warnings，引导到快照追平入口
 */
export default function CashMarketValueUpdateDialog({
  portfolioCode,
  open,
  onOpenChange,
  defaultPlatformCode,
  defaultDate,
}: CashMarketValueUpdateDialogProps) {
  const addToast = useUIStore((state) => state.addToast);
  const { data: platformData } = usePlatformList();
  const platforms: Platform[] = platformData?.items || [];

  // Form state
  const [selectedPlatform, setSelectedPlatform] = useState<string | null>(
    defaultPlatformCode ?? null,
  );
  const [selectedDate, setSelectedDate] = useState<Date | undefined>(defaultDate);
  const [cashAmount, setCashAmount] = useState("");

  // Success feedback state
  const [submitResult, setSubmitResult] = useState<{
    warnings: string[];
    requiresSnapshotRegen: boolean;
  } | null>(null);

  const updateCashPosition = useUpdateCashPosition(portfolioCode);
  const deleteOverride = useDeleteCashOverride(portfolioCode);

  // Query existing overrides for selected platform/date
  const overrideParams = selectedPlatform
    ? { platform_code: selectedPlatform }
    : undefined;
  const { data: overridesData, refetch: refetchOverrides } = useListCashOverrides(
    portfolioCode,
    overrideParams,
    open && !!selectedPlatform,
  );

  // Reset form when dialog opens/closes
  useEffect(() => {
    if (open) {
      setSelectedPlatform(defaultPlatformCode ?? null);
      setSelectedDate(defaultDate);
      setCashAmount("");
      setSubmitResult(null);
    }
  }, [open, defaultPlatformCode, defaultDate]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (!selectedPlatform) {
      addToast({ type: "error", title: "输入错误", message: "请选择平台" });
      return;
    }
    if (!cashAmount || parseFloat(cashAmount) < 0) {
      addToast({ type: "error", title: "输入错误", message: "请输入有效的金额" });
      return;
    }

    updateCashPosition.mutate(
      {
        amount: parseFloat(cashAmount),
        platformCode: selectedPlatform,
        updateDate: selectedDate ? toDateOnly(selectedDate) : undefined,
      },
      {
        onSuccess: (result) => {
          setSubmitResult({
            warnings: result.warnings || [],
            requiresSnapshotRegen: result.requires_snapshot_regen,
          });
          refetchOverrides();
          if (result.warnings?.length === 0 && !result.requires_snapshot_regen) {
            addToast({ type: "success", title: "更新成功", message: "现金市值已更新" });
          }
        },
        onError: (error: unknown) => {
          addToast({ type: "error", title: "更新失败", message: getErrorMessage(error, "请检查输入") });
        },
      },
    );
  };

  const handleDeleteOverride = (platformCode: string, updateDate: string) => {
    deleteOverride.mutate(
      { platformCode, updateDate },
      { onSuccess: () => refetchOverrides() },
    );
  };

  // Find matching override for current platform/date selection
  const dateStr = selectedDate ? toDateOnly(selectedDate) : null;
  const matchingOverride = overridesData?.items?.find(
    (item) =>
      item.platform_code === selectedPlatform &&
      (!dateStr || item.update_date === dateStr),
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[560px] max-h-[90vh] overflow-y-auto">
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

          {/* Override comparison */}
          {selectedPlatform && matchingOverride && (
            <div className="rounded-md border border-border bg-muted/30 p-3 space-y-2">
              <p className="text-sm font-medium">当日已有覆盖记录</p>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div>
                  <span className="text-muted-foreground">覆盖值：</span>
                  <span className="font-mono">{formatCurrency(matchingOverride.manual_value)}</span>
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
                  handleDeleteOverride(matchingOverride.platform_code, matchingOverride.update_date)
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
                <Alert variant="destructive">
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
    </Dialog>
  );
}
