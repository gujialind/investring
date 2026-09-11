"use client";

// 根级错误边界（issue #407）：App Router 约定文件，必须为 Client Component。
// 覆盖 src/app/layout.tsx 之下、不被更多层嵌套 error.tsx 接住的所有渲染期异常
// （含 /m/* —— m/layout.tsx 只接住自身子树，m/error.tsx 更靠近故障点）。
//
// 走共享的 ErrorFallback：异常经 logger 留痕，UI 只用语义 token 与派生档位。
// 刻意不自造 <ErrorBoundary> 组件——Next 的约定文件即边界，再包一层不增加覆盖。
import ErrorFallback from "@/components/shared/ErrorFallback";

export default function RootError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorFallback error={error} reset={reset} scope="页面" />;
}
