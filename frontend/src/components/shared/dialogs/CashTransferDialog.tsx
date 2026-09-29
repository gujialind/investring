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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { DatePicker } from "@/components/ui/date-picker";
import { Loader2 } from "lucide-react";
import SearchablePlatformSelect from "@/components/shared/SearchablePlatformSelect";
import { usePlatformList } from "@/hooks/usePlatform";
import { useCreateCashTransfer } from "@/hooks/useCashTransfer";
import { useUIStore } from "@/stores/uiStore";
import { toDateOnly, parseDateOnly } from "@/lib/utils";
import type { Platform } from "@/types/platform";

interface CashTransferDialogProps {
  portfolioCode: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 平台-产品详情页传入本平台（转出 = 本平台为 from，转入 = 本平台为 to）；
   *  产品详情页（现金聚合视角）不传，from/to 均由用户选择 */
  contextPlatformCode?: string;
  direction?: "in" | "out";
}

/**
 * 平台间现金转移创建 Dialog（#595 §4.5/D-10，双端共用）。
 * 复用 positions 子页内联表单的行为契约（标题/placeholder/互斥禁用/跨天到账），
 * 收敛为共享组件后接入产品详情页与平台-产品详情页的「转入/转出」操作行。
 * 现金转移走 cash-transfer 端点（两腿显式落账），REST 禁止直接创建 CASH 交易。
 */
export default function CashTransferDialog({
  portfolioCode,
  open,
  onOpenChange,
  contextPlatformCode,
  direction,
}: CashTransferDialogProps) {
  const addToast = useUIStore((state) => state.addToast);
  const { data: platformsData } = usePlatformList({ page_size: 100 });
  const platforms: Platform[] = platformsData?.items || [];

  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [amount, setAmount] = useState("");
  const [transferDate, setTransferDate] = useState(toDateOnly(new Date()));
  const [crossDay, setCrossDay] = useState(false);

  const createCashTransfer = useCreateCashTransfer(portfolioCode);

  // 打开时按方向预填本平台，并重置其余字段
  useEffect(() => {
    if (open) {
      setFrom(direction === "out" ? (contextPlatformCode ?? "") : "");
      setTo(direction === "in" ? (contextPlatformCode ?? "") : "");
      setAmount("");
      setTransferDate(toDateOnly(new Date()));
      setCrossDay(false);
    }
  }, [open, direction, contextPlatformCode]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!from || !to || from === to) {
      addToast({ type: "error", title: "输入错误", message: "请选择不同的转出和转入平台" });
      return;
    }
    if (!amount || parseFloat(amount) <= 0) {
      addToast({ type: "error", title: "输入错误", message: "请输入有效的转移金额" });
      return;
    }
    createCashTransfer.mutate(
      {
        from_platform: from,
        to_platform: to,
        amount: parseFloat(amount),
        cross_day: crossDay,
        transfer_date: transferDate,
      },
      { onSuccess: () => onOpenChange(false) },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange} modal={false}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>平台间现金转移</DialogTitle>
          <DialogDescription>将现金从一个平台转移到另一个平台</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit}>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label>转出平台</Label>
              <SearchablePlatformSelect
                platforms={platforms}
                value={from || null}
                onChange={(v) => setFrom(v ?? "")}
                placeholder="选择转出平台"
                isOptionDisabled={(p) => p.code === to}
              />
            </div>
            <div className="space-y-2">
              <Label>转入平台</Label>
              <SearchablePlatformSelect
                platforms={platforms}
                value={to || null}
                onChange={(v) => setTo(v ?? "")}
                placeholder="选择转入平台"
                isOptionDisabled={(p) => p.code === from}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="transfer_amount">转移金额（元）</Label>
              <Input
                id="transfer_amount"
                type="number"
                step="0.01"
                min="0.01"
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
                placeholder="请输入转移金额"
                required
              />
            </div>
            <div className="space-y-2">
              <Label>转移日期</Label>
              <DatePicker
                date={parseDateOnly(transferDate)}
                onSelect={(date) => setTransferDate(toDateOnly(date))}
              />
            </div>
            <div className="flex items-center space-x-2">
              <input
                type="checkbox"
                id="cross_day"
                checked={crossDay}
                onChange={(e) => setCrossDay(e.target.checked)}
                className="h-4 w-4"
              />
              <Label htmlFor="cross_day" className="text-sm">
                跨天到账（T+1 确认，适用于银行转账等场景）
              </Label>
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="submit" disabled={createCashTransfer.isPending}>
              {createCashTransfer.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              确认转移
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
