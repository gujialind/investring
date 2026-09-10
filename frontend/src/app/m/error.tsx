"use client";

// 移动端错误边界（issue #407）：与根 error.tsx 同构，但更靠近 /m/* 的故障点——
// m/layout.tsx 是 Client Component，其子树抛错时由本文件接住，避免整棵树交给
// 根边界处理（也保证移动端用户看到与站点一致的兜底 UI，而不是浏览器白屏）。
import ErrorFallback from "@/components/shared/ErrorFallback";

export default function MobileError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorFallback error={error} reset={reset} scope="移动端页面" />;
}
