"use client";

// 根级错误边界（issue #407）：App Router 约定文件，必须为 Client Component，
// 且要自带 <html>/<body>——它接住的是**根 layout 自身**抛出的异常，此时
// src/app/layout.tsx 没有渲染成功，不能再假设它提供的外壳存在。
//
// 因此这里刻意只用最朴素的语义 token 类名（bg-background / text-foreground），
// 不引 Providers 与共享组件：把依赖面收到最小，才能保证「最坏情况下仍能渲染出
// 一个有信息的界面」而不是白屏。异常同样经 logger 留痕（前端 console）。
//
// globals.css 必须显式引：本文件替换的是**根 layout 的整个外壳**，不引则语义 token
// 与工具类全部未定义，兜底界面会以无样式纯文本呈现（等于另一种形式的白屏）。
import "./globals.css";

import { useEffect } from "react";

import { createLogger } from "@/lib/logger";

const log = createLogger("globalError");

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    log.error("根布局渲染异常", error);
  }, [error]);

  return (
    <html lang="zh-CN">
      <body className="bg-background text-foreground">
        <div className="flex min-h-screen items-center justify-center p-6">
          <div className="w-full max-w-md space-y-3 text-center">
            <h1 className="text-base font-semibold">应用出错了</h1>
            <p className="text-sm text-muted-foreground">
              应用外壳渲染时发生异常，数据未受影响。请重试一次。
            </p>
            {error.digest ? (
              <p className="text-xs text-muted-foreground">
                错误标识：<span className="font-mono">{error.digest}</span>
              </p>
            ) : null}
            <div className="flex justify-center pt-2">
              <button
                type="button"
                onClick={() => reset()}
                className="inline-flex h-9 items-center justify-center rounded-md border border-input bg-background px-3 text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              >
                重试
              </button>
            </div>
          </div>
        </div>
      </body>
    </html>
  );
}
