"use client";

import { useEffect } from "react";

import { Button } from "@/components/ui/button";
import { createLogger } from "@/lib/logger";

const log = createLogger("errorBoundary");

/**
 * 渲染期异常的兜底面板（issue #407）：桌面与移动端两套 `error.tsx` 共用的 UI。
 *
 * 只用语义 token 与四个派生档位（docs/design/visual-spec.md §1.3 状态色 /
 * §1.4 中性色 / §5 字号四级 / §7 间距圆角），不写调色板类名与任意值类名。
 *
 * 异常在此留痕（stdout 级别的前端 console），并带上 Next 分配的错误 digest——
 * 它同时出现在浏览器与 Next 服务端日志里，是两条记录之间唯一的关联键。
 * 刻意不做客户端上报（总纲 #403 决策 3：不改 openapi 契约、不引上报端点）。
 */
export default function ErrorFallback({
  error,
  reset,
  scope = "页面",
}: {
  error: Error & { digest?: string };
  reset: () => void;
  scope?: string;
}) {
  useEffect(() => {
    log.error(`${scope}渲染异常`, error);
  }, [error, scope]);

  return (
    <div className="flex min-h-[60vh] items-center justify-center bg-background p-6">
      <div className="w-full max-w-md space-y-3 rounded-lg border bg-card p-6 text-center shadow-sm">
        <h1 className="text-base font-semibold text-foreground">
          {scope}出错了
        </h1>
        <p className="text-sm text-muted-foreground">
          页面渲染时发生异常，数据未受影响。可重试一次；若反复出现，请把错误摘要反馈给开发者。
        </p>
        {error.digest ? (
          <p className="text-xs text-muted-foreground">
            错误标识：<span className="font-mono">{error.digest}</span>
          </p>
        ) : null}
        <div className="flex justify-center pt-2">
          <Button type="button" variant="outline" size="sm" onClick={() => reset()}>
            重试
          </Button>
        </div>
      </div>
    </div>
  );
}
