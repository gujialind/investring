import { Button } from "@/components/ui/button";
import { getErrorMessage } from "@/lib/api";

interface QueryErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  /** 容器样式；缺省为页级失败态（垂直居中段落），卡内使用传紧凑值 */
  className?: string;
}

/**
 * 查询失败态共享渲染（#647）：失败文案 + 重试入口，不得伪装成空态。
 * 形态对齐各页 holdings 失败分支（「加载失败：{msg}」+ link Button 重试），
 * 单点持有供产品/平台-产品详情页的历史净值等子查询共用，不各写一份。
 */
export default function QueryErrorState({
  error,
  onRetry,
  className = "py-8 text-center",
}: QueryErrorStateProps) {
  return (
    <div className={className}>
      <p className="text-muted-foreground">
        加载失败：{getErrorMessage(error, "请刷新重试")}
      </p>
      {onRetry && (
        <Button variant="link" size="sm" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
}
