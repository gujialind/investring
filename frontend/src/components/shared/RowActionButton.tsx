import type { LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface RowActionButtonProps {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
  testId?: string;
  destructive?: boolean;
  disabled?: boolean;
  className?: string;
}

// 列表行操作单点（visual-spec §8 操作列控件形态，#823）：variant/size 锁死防漂移，
// title 与 aria-label 同值；文本例外（动态业务文案的状态推进）不走本组件。
export default function RowActionButton({
  icon: Icon,
  label,
  onClick,
  testId,
  destructive,
  disabled,
  className,
}: RowActionButtonProps) {
  return (
    <Button
      variant="ghost"
      size="sm"
      title={label}
      aria-label={label}
      data-testid={testId}
      disabled={disabled}
      onClick={onClick}
      className={cn(destructive && "text-destructive", className)}
    >
      <Icon className="h-4 w-4" />
    </Button>
  );
}
