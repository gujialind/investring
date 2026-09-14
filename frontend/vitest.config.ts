import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// 单元测试层（issue #253）：node 环境纯逻辑测试，不引 jsdom/RTL
//（组件交互由 Playwright E2E 兜底）。手动 alias 对齐 tsconfig paths，不引 vite-tsconfig-paths。
//
// 覆盖率门禁（issue #464）：分母只圈 src/lib/** 纯逻辑层——src/lib/api/** 是类型化
// axios 薄封装（无数据转换逻辑，见 frontend/AGENTS.md §3），计入分母会稀释信号。
// 阈值 = 2026-09-13 实测基线（stmts 80.64 / branch 85.11 / funcs 66.33 / lines 81.35）
// 「取低 1pp 后下取整」→ 79/84/65/80，只升不降。按「实测 − 1pp」而非「实测下取整」
// 是有意的余量校验：阈值紧贴实测会让「新增一条未覆盖分支/函数」或「工具链升级
// （vitest 系）改了统计口径」这类与代码质量无关的变动把门禁弄红——本配置曾按
// 「实测下取整」定 80/85/65/81，branches 只余 0.11pp（≈0.4 条分支：新增 1 条未覆盖
// 分支即 263/310=84.84%<85 变红），故四项统一 −1pp。棘轮：实测超阈值 ≥1pp 时在
// 当次 PR 顺手上调，「上调目标」同样按实测 − 1pp 下取整（后端 fail_under 是上调到
// 实测下取整，前端分母小、刻意多留 1pp）。余量由补测逐步收回（缺口清单见 #484）。
// 全局阈值（非 perFile）：新文件 0% 会让总量下滑，正是要拦的「靠既有高覆盖掩护新
// 代码」。前端暂无「只看本 PR 改动行」的增量门禁（#485）。
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
        statements: 79,
        branches: 84,
        functions: 65,
        lines: 80,
      },
    },
  },
});
