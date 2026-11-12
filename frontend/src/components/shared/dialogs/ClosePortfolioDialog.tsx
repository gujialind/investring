"use client";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Loader2 } from "lucide-react";
import { usePortfolioInvestors, usePositionList } from "@/hooks/usePortfolio";

interface ClosePortfolioDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  isPending?: boolean;
  /** 组合代码：弹窗内部惰性查询持仓与投资人（仅 open 时发请求，issue #99） */
  portfolioCode: string;
}

/**
 * 关闭组合确认弹窗（桌面列表页 / 移动端共用，issue #99）。
 * 打开时才惰性查询持仓（page_size=1 取 total）与投资人，展示关闭前校验提示：
 * 持仓数、投资人数、不可逆操作。仅两个请求，无 N+1。
 */
export default function ClosePortfolioDialog({
  open,
  onOpenChange,
  onConfirm,
  isPending,
  portfolioCode,
}: ClosePortfolioDialogProps) {
  const { data: positionsData, isLoading: positionsLoading } = usePositionList(
    portfolioCode,
    { page_size: 1 },
    { enabled: open }
  );
  const { data: investors, isLoading: investorsLoading } = usePortfolioInvestors(
    portfolioCode,
    { enabled: open }
  );

  const checking = open && (positionsLoading || investorsLoading);
  const positionCount = positionsData?.total ?? positionsData?.items?.length ?? 0;

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>关闭组合</AlertDialogTitle>
          <AlertDialogDescription>关闭组合前请确认以下事项：</AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-2 text-sm">
          {checking ? (
            <p className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在检查持仓与投资人…
            </p>
          ) : (
            <>
              {positionCount > 0 && (
                <p className="text-warning">
                  当前仍有 {positionCount} 个持仓，关闭后持仓数据将被保留但不可操作
                </p>
              )}
              {investors && investors.length > 0 && (
                <p className="text-warning">
                  当前有 {investors.length} 位投资人持有份额
                </p>
              )}
              <p className="text-destructive">关闭后无法再进行申购/赎回/调仓操作</p>
            </>
          )}
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction
            onClick={(e) => {
              e.preventDefault();
              onConfirm();
            }}
            disabled={isPending || checking}
            className="bg-destructive-solid text-white hover:bg-destructive-solid/90"
          >
            {isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            确认关闭
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
