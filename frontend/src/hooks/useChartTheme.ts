import { useTheme } from "next-themes";
import type { ChartTheme } from "@/lib/colors";

/**
 * 图表取色主题（#872 PR2）：resolvedTheme 未 mounted 时回落 light，
 * 与 SSR/首帧一致避免 hydration mismatch；recharts 内容本身客户端测量后才渲染。
 * 纯选择逻辑在 lib/colors.ts（theme 访问器），本 hook 只做 React 侧桥接。
 */
export function useChartTheme(): ChartTheme {
  const { resolvedTheme } = useTheme();
  return resolvedTheme === "dark" ? "dark" : "light";
}
