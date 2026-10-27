"use client";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Loader2 } from "lucide-react";
import { ApiException } from "@/lib/api/client";
import { useCancelCashTransfer } from "@/hooks/useCashTransfer";
import { formatCurrency } from "@/lib/utils";

export interface CancelTransferTarget {
  transfer_group: string;
  from_platform: string;
  to_platform: string;
  amount: number;
  transfer_date: string;
}

interface CancelTransferDialogProps {
  portfolioCode: string;
  target: CancelTransferTarget | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * 撤销现金转移二次确认弹窗（issue #788）。
 * 展示转出/转入平台、金额、日期与影响说明；后端错误（快照保护 / 现金不足 /
 * 已取消幂等）在弹窗内 inline 给可操作提示，不裸 toast。
 */
export default function CancelTransferDialog({
  portfolioCode,
  target,
  open,
  onOpenChange,
}: CancelTransferDialogProps) {
  const cancelTransfer = useCancelCashTransfer(portfolioCode);

  const handleConfirm = () => {
    if (!target) return;
    cancelTransfer.mutate(target.transfer_group, {
      onSuccess: () => onOpenChange(false),
      // 错误留在弹窗内展示（error = mutation state），不关闭弹窗
    });
  };

  const error = cancelTransfer.error;
  const errorCode = error instanceof ApiException ? error.code : null;
  const errorMessage = error instanceof Error ? error.message : null;
  const errorDetails =
    error instanceof ApiException ? (error.details as { from_date?: string } | undefined) : undefined;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>撤销现金转移</DialogTitle>
          <DialogDescription>
            撤销后两腿标记为「已取消」并保留记录，两个平台的可用现金将回退到转移前。
          </DialogDescription>
        </DialogHeader>
        {target && (
          <div className="divide-y text-sm">
            <div className="flex justify-between py-2">
              <span className="text-muted-foreground">转出 → 转入</span>
              <span>
                {target.from_platform} → {target.to_platform}
              </span>
            </div>
            <div className="flex justify-between py-2">
              <span className="text-muted-foreground">金额</span>
              <span>{formatCurrency(target.amount)}</span>
            </div>
            <div className="flex justify-between py-2">
              <span className="text-muted-foreground">转移日期</span>
              <span>{target.transfer_date}</span>
            </div>
          </div>
        )}
        {error && (
          <Alert variant="destructive">
            <AlertDescription>
              {errorCode === "SNAPSHOT_DEPENDENCY"
                ? `转移日已被快照覆盖（${errorDetails?.from_date ?? "起始日"} 及之后），` +
                  "请先在快照页删除/重算该日期及之后的快照，再回来撤销。"
                : errorCode === "TRANSFER_ALREADY_CANCELLED"
                  ? "该转移已被撤销，列表即将刷新。"
                  : (errorMessage ?? "撤销失败，请稍后重试")}
            </AlertDescription>
          </Alert>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            variant="destructive"
            onClick={handleConfirm}
            disabled={cancelTransfer.isPending}
          >
            {cancelTransfer.isPending && <Loader2 className="mr-1 h-3 w-3 animate-spin" />}
            确认撤销
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
