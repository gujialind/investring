import type { LucideIcon } from "lucide-react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface RowActionButtonProps {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
  testId?: string;
  destructive?: boolean;
  disabled?: boolean;
  /**
   * 禁用原因（如快照连续原则）。仅作 `title` 的长解释，`aria-label` 恒为短词 `label`；
   * 是 visual-spec §8「title 与 aria-label 同值」的唯一例外（#846）。
   */
  disabledHint?: string;
  /** 异步动作 pending 时以 `Loader2` 转圈替代图标，保留点击后的即时反馈（#846）。 */
  loading?: boolean;
  className?: string;
}

// 列表行操作单点（visual-spec §8 操作列控件形态，#823）：variant/size 锁死防漂移，
// title 与 aria-label 同值（禁用解释例外见 disabledHint）；文本例外（动态业务文案的状态推进）不走本组件。
export default function RowActionButton({
  icon: Icon,
  label,
  onClick,
  testId,
  destructive,
  disabled,
  disabledHint,
  loading,
  className,
}: RowActionButtonProps) {
  const Glyph = loading ? Loader2 : Icon;
  return (
    <Button
      variant="ghost"
      size="sm"
      title={disabled && disabledHint ? disabledHint : label}
      aria-label={label}
      aria-busy={loading || undefined}
      data-testid={testId}
      disabled={disabled}
      onClick={onClick}
      className={cn(destructive && "text-destructive", className)}
    >
      <Glyph className={cn("h-4 w-4", loading && "animate-spin")} />
    </Button>
  );
}
