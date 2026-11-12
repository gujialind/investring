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

interface DeletePortfolioDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  isPending?: boolean;
  /** 组合代码与名称：确认文案必须点名两者，删除不可恢复（issue #820 口径 Q4） */
  portfolioCode: string;
  portfolioName?: string;
}

/**
 * 删除空壳组合的二次确认弹窗（列表页与桌面/移动详情页共用，issue #820 扩围口径）。
 *
 * 刻意不在弹窗内预判「是否真的空壳」（不查持仓/申赎计数）：放行判据是
 * 「status ∈ {draft, closed} 且 7 张依赖表计数全为 0」，其中「unconfirm 会退回 draft 而
 * 申赎行仍在」这一形态前端无从完整推断；把判据复制到 UI 只会与后端漂移。
 * 真正的拦截由后端 409 给出点名表的 `details.dependencies`，失败经 toast 呈现。
 */
export default function DeletePortfolioDialog({
  open,
  onOpenChange,
  onConfirm,
  isPending,
  portfolioCode,
  portfolioName,
}: DeletePortfolioDialogProps) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除组合</AlertDialogTitle>
          <AlertDialogDescription>
            {portfolioName
              ? `确定删除组合「${portfolioName}」(${portfolioCode}) 吗？仅无任何关联记录的空壳可删除，操作不可恢复；同一 code 之后可重新创建。`
              : `确定删除组合 ${portfolioCode} 吗？仅无任何关联记录的空壳可删除，操作不可恢复；同一 code 之后可重新创建。`}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>取消</AlertDialogCancel>
          <AlertDialogAction
            // 阻止 Radix 的默认关闭动作，改由调用方在请求结束后收尾（与 ClosePortfolioDialog 同形态）
            onClick={(e) => {
              e.preventDefault();
              if (isPending) return;
              onConfirm();
            }}
            disabled={isPending}
            className="bg-destructive-solid text-white hover:bg-destructive-solid/90"
          >
            确认删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
