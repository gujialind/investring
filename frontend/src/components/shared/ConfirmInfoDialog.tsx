"use client";

import type { ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * 确认信息核对弹窗共享壳（#248）：
 * 三态内容区（加载骨架 / 后端错误 / 字段内容）+ 二次确认按钮。
 * 预览加载中或失败时禁用确认按钮，失败错误直接展示在弹窗内（不静默吞错）。
 *
 * 内容区是**定长两槽**（状态分支 + `inputSlot`），不是三选一的整体三元——后者会让
 * `inputSlot` 跨态重挂载，见该 prop 的注释（#551 方案 A）。
 */
interface ConfirmInfoDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  /** 预览请求进行中 */
  isLoading?: boolean;
  /** 预览请求失败的后端错误信息 */
  error?: string | null;
  onConfirm: () => void;
  /** 确认请求进行中 */
  isConfirming?: boolean;
  confirmLabel?: string;
  /**
   * **常驻输入槽位**：渲染在内容区的固定位置，loading / error / 就绪三态下**同一父节点、
   * 同一子索引**，因而跨态复用实例、不重挂载。
   *
   * 前身是 `errorSlot`（#493 评审加固：预览 422 时让用户**就地改正**业务输入——改了即换
   * query key 重新预览，而不是关窗重开这条死路）。当时它只在 error 分支渲染，与 `children`
   * 分属两棵**树路径不同**的子树（error 态在 `div.space-y-3 > div.divide-y` 的 depth 2，
   * 就绪态在 `div.divide-y` 的 depth 1）⇒ React 永不跨分支复用 ⇒ 确认弹窗每跑一次后台
   * refetch（`isLoading` 取 `isLoading || isFetching`）就把整块输入 destroy + recreate：
   * 用户正开着的 Radix 弹层被连带关闭、组件本地 state（搜索词、展开态）归零（#551 方案 A）。
   *
   * 契约不变的部分：只承载「用户输入 + 记录自身字段」，**不得**渲染预览计算值
   * （那会重蹈 #424 覆辙——拿上一次预览值冒充本次结果）。
   */
  inputSlot?: ReactNode;
  children: ReactNode;
}

export function ConfirmInfoDialog({
  open,
  onOpenChange,
  title,
  description,
  isLoading = false,
  error = null,
  onConfirm,
  isConfirming = false,
  confirmLabel = "确认",
  inputSlot,
  children,
}: ConfirmInfoDialogProps) {
  const blocked = isLoading || !!error || isConfirming;
  // 内容区三态的「状态分支」槽位：加载/失败各出自己的提示，就绪时整段交给 children。
  // 刻意算成一个变量、只占固定的一个子索引，与下方常驻输入槽互不影响（#551 方案 A）。
  const statusBranch = isLoading ? (
    <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" />
      正在计算预览…
    </div>
  ) : error ? (
    <div className="rounded-md bg-destructive-soft px-3 py-2 text-sm text-destructive-foreground">
      预览失败：{error}
    </div>
  ) : (
    children
  );
  return (
    // 确认请求在途时禁止 Esc/遮罩关闭（与已禁用的取消按钮一致，防止关窗后并发确认）
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen && isConfirming) return;
        onOpenChange(nextOpen);
      }}
    >
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? <DialogDescription>{description}</DialogDescription> : null}
        </DialogHeader>

        {/* 定长两槽：[0]=状态分支、[1]=常驻输入。三态下父节点与子索引恒定 ⇒ React 跨态
            复用 [1] 的实例树，弹窗内的用户输入与浮层开合状态得以存活（#551 方案 A）。
            旧写法把输入塞进 error 分支再套一层 `div.divide-y`，与 `children` 的树路径不同，
            于是每次 refetch 翻转分支都重挂载一次。 */}
        <div className="divide-y divide-border">
          {statusBranch}
          {inputSlot ?? null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isConfirming}>
            取消
          </Button>
          <Button onClick={onConfirm} disabled={blocked}>
            {isConfirming && <Loader2 className="mr-1 h-4 w-4 animate-spin" />}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 字段行：左侧灰色标签 + 右侧数值（金融数字由调用方经 lib/utils 格式化） */
export function InfoRow({
  label,
  value,
  valueClassName,
}: {
  label: string;
  value: ReactNode;
  valueClassName?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5 text-sm">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className={cn("text-right font-medium tabular-nums", valueClassName)}>{value}</span>
    </div>
  );
}
