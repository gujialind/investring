"use client";

import { useState, useEffect } from "react";
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DatePicker } from "@/components/ui/date-picker";
import SearchablePlatformSelect from "@/components/shared/SearchablePlatformSelect";
import { Loader2 } from "lucide-react";
import { toDateOnly } from "@/lib/utils";
import { getErrorMessage } from "@/lib/api";
import { usePlatformList } from "@/hooks/usePlatform";
import { useCreateLedgerFlow, useUpdateLedgerFlow } from "@/hooks/useLedger";
import { useUIStore } from "@/stores/uiStore";
import type { Platform } from "@/types/platform";
import type { LedgerFlow } from "@/types/ledger";

interface LedgerFlowDialogProps {
  portfolioCode: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 传入则编辑该笔进出（平台锁定）；不传则新建（同平台同日可多笔） */
  flow?: LedgerFlow;
}

/**
 * 台账本金进出登记 Dialog（issue #905 PR3，D2/S4）。
 * 新建：平台 + 日期 + 方向（流入/流出）+ 金额（>0）+ 备注；同平台同日可多笔。
 * 编辑：平台锁定，日期/方向/金额/备注可改。净入金为负时后端附 warning，hook 如实透出（不静默）。
 */
export default function LedgerFlowDialog({
  portfolioCode,
  open,
  onOpenChange,
  flow,
}: LedgerFlowDialogProps) {
  const addToast = useUIStore((state) => state.addToast);
  const { data: platformData } = usePlatformList({ page_size: 100 });
  const platforms: Platform[] = platformData?.items || [];
  const isEdit = !!flow;

  const [platform, setPlatform] = useState<string | null>(null);
  const [date, setDate] = useState<Date | undefined>(undefined);
  const [direction, setDirection] = useState("inflow");
  const [amount, setAmount] = useState("");
  const [note, setNote] = useState("");

  const createFlow = useCreateLedgerFlow(portfolioCode);
  const updateFlow = useUpdateLedgerFlow(portfolioCode);
  const pending = createFlow.isPending || updateFlow.isPending;

  useEffect(() => {
    if (open) {
      setPlatform(flow?.platform_code ?? null);
      setDate(flow ? new Date(flow.flow_date + "T00:00:00") : undefined);
      setDirection(flow?.direction ?? "inflow");
      setAmount(flow ? String(flow.amount) : "");
      setNote(flow?.note ?? "");
    }
  }, [open, flow]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!isEdit && !platform) {
      addToast({ type: "error", title: "输入错误", message: "请选择平台" });
      return;
    }
    if (!date) {
      addToast({ type: "error", title: "输入错误", message: "请选择日期" });
      return;
    }
    const amt = parseFloat(amount);
    if (amount === "" || !Number.isFinite(amt) || amt <= 0) {
      addToast({ type: "error", title: "输入错误", message: "请输入有效的金额（>0）" });
      return;
    }
    const onSuccess = () => onOpenChange(false);
    const onError = (error: unknown) =>
      addToast({ type: "error", title: isEdit ? "更新失败" : "登记失败", message: getErrorMessage(error, "请检查输入") });

    if (isEdit && flow) {
      updateFlow.mutate(
        { id: flow.id, data: { flow_date: toDateOnly(date), direction, amount: amt, note: note || null } },
        { onSuccess, onError },
      );
    } else {
      createFlow.mutate(
        {
          platform_code: platform!,
          flow_date: toDateOnly(date),
          direction,
          amount: amt,
          note: note || null,
        },
        { onSuccess, onError },
      );
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>{isEdit ? "编辑本金进出" : "登记本金进出"}</DialogTitle>
          <DialogDescription>
            {isEdit ? "修改该笔进出（平台不可改）" : "记录本金流入/流出，同平台同日可多笔"}
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="ledger-flow-platform">平台</Label>
            {isEdit ? (
              <Input id="ledger-flow-platform" value={flow?.platform_code ?? ""} disabled />
            ) : (
              <SearchablePlatformSelect
                platforms={platforms}
                value={platform}
                onChange={setPlatform}
                placeholder="请选择平台"
                id="ledger-flow-platform"
              />
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="ledger-flow-date">进出日期</Label>
            <DatePicker
              id="ledger-flow-date"
              date={date}
              onSelect={setDate}
              placeholder="选择日期（任意自然日）"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="ledger-flow-direction">方向</Label>
            <Select value={direction} onValueChange={setDirection}>
              <SelectTrigger id="ledger-flow-direction">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="inflow">流入（注资）</SelectItem>
                <SelectItem value="outflow">流出（撤资）</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="ledger-flow-amount">金额（元）</Label>
            <Input
              id="ledger-flow-amount"
              type="number"
              step="0.01"
              min="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              placeholder="请输入本金金额（>0）"
              required
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="ledger-flow-note">备注（可选）</Label>
            <Input
              id="ledger-flow-note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="选填"
            />
          </div>

          <DialogFooter className="flex-col sm:flex-row gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} className="w-full sm:w-auto">
              取消
            </Button>
            <Button type="submit" disabled={pending} className="w-full sm:w-auto">
              {pending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {isEdit ? "保存" : "登记"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}