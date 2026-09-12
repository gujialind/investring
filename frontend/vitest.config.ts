import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// 单元测试层（issue #253）：node 环境纯逻辑测试，不引 jsdom/RTL
//（组件交互由 Playwright E2E 兜底）。手动 alias 对齐 tsconfig paths，不引 vite-tsconfig-paths。
//
// 覆盖率门禁（issue #464）：分母只圈 src/lib/** 纯逻辑层——src/lib/api/** 是类型化
// axios 薄封装（无数据转换逻辑，见 frontend/AGENTS.md §3），计入分母会稀释信号。
// 阈值 = 2026-09-13 实测基线下取整（stmts 80.64 / branch 85.11 / funcs 66.33 /
// lines 81.35 → 80/85/66/81），只升不降（棘轮规则同后端 fail_under）：实测超阈值
// ≥1pp 时在当次 PR 顺手上调。全局阈值（非 perFile）：新文件 0% 会让总量下滑，
// 正是要拦的「靠既有高覆盖掩护新代码」。
// CI 另产 JUnit XML 供 PR 注解（本地保持默认 reporter，不落文件）。
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
    reporters: process.env.CI
      ? ["default", ["junit", { outputFile: "junit.xml" }]]
      : ["default"],
    coverage: {
      provider: "v8",
      include: ["src/lib/**/*.{ts,tsx}"],
      exclude: ["src/lib/api/**", "**/*.test.{ts,tsx}"],
      reporter: ["text", "json-summary"],
      thresholds: {
        statements: 80,
        branches: 85,
        functions: 66,
        lines: 81,
      },
    },
  },
});
