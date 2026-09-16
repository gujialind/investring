import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// 单元测试层（issue #253）：node 环境纯逻辑测试，不引 jsdom/RTL
//（组件交互由 Playwright E2E 兜底）。手动 alias 对齐 tsconfig paths，不引 vite-tsconfig-paths。
//
// 覆盖率门禁（issue #464）：分母只圈 src/lib/** 纯逻辑层——src/lib/api/** 是类型化
// axios 薄封装（无数据转换逻辑，见 frontend/AGENTS.md §3），计入分母会稀释信号。
// 阈值 = 2026-09-16 实测（stmts 100 / branch 100 / funcs 100 / lines 100）「取低 1pp
// 后下取整」→ 99/99/99/99，只升不降。按「实测 − 1pp」而非「实测下取整」是有意的余量
// 校验：阈值紧贴实测会让「新增一条未覆盖分支/函数」或「工具链升级（vitest 系）改了
// 统计口径」这类与代码质量无关的变动把门禁弄红——本配置曾按「实测下取整」定
// 80/85/66/81，branches 只余 0.11pp（≈0.4 条分支：新增 1 条未覆盖分支即
// 263/310=84.84%<85 变红），#464 评审遂要求四项统一改按「实测 − 1pp」定（functions
// 先落到 65）。棘轮：实测超阈值 ≥1pp 时在当次 PR 顺手
// 上调，「上调目标」同样按实测 − 1pp 下取整（后端 fail_under 是上调到实测下取整，前端
// 分母小、刻意多留 1pp）。三轮收口：#484 首轮补测把基线从 80.64/85.11/66.33/81.35
// 抬到 90.02/88.02/93.06/91.86；#501（删 utils.ts 的 7 个全仓零调用死代码）+ #503
// （补齐 tradePairs/tradeAmounts/colors/logger 及 utils.ts 的零散分支）后到
// 100/99.65/100/100；#507 收紧 tradePairs 的异常 2 腿组规则——均非 CASH、双 CASH 均 sell
// 由 pair 改为回落 single（与既有的均 buy 兜底对齐，规则注释的边界即契约），分支补齐
// 后到全 100、阈值随棘轮 98→99。
// 残余：无。⚠️ #504 落地后 tradeAmounts 的「到手无法量化」守卫将失去触发路径、产生
// 新残余，届时同步复核本段与 frontend/AGENTS.md §3。
// 全局阈值（非 perFile）：新文件 0% 会让总量下滑，正是要拦的「靠既有高覆盖掩护新
// 代码」。另有一条「只看本 PR 改动行」的增量门禁（#485）：CI frontend-check 在 PR
// 事件对 coverage/lcov.info 跑 diff-cover（阈值以 ci.yml 的门禁步骤为单一来源，形态
// 与后端同），故 reporter 里有 lcovonly——其 projectRoot 必须置 ".."：该值是相对
// **cwd** 解析的（默认即 cwd 的 vitest 根），而 diff-cover 把 LCOV 的相对 SF 路径按
// **git root** 解析，只有从仓库根的 frontend/ 下运行（CI 由 job 的 working-directory
// 保证）才会得到 frontend/src/... 前缀；否则（如从仓库根跑 npx vitest --root frontend）
// SF 会变成 src/lib/x.ts、匹配不到任何改动行、门禁静默空转（CI 侧有 warning 守卫与
// lcov 数据源断言兜底，见 ci.yml 的 `Warn on unmapped diff (PR)` 与 `Assert lcov data source (PR)`）。
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
      reporter: ["text", "json-summary", ["lcovonly", { projectRoot: ".." }]],
      thresholds: {
        statements: 99,
        branches: 99,
        functions: 99,
        lines: 99,
      },
    },
  },
});
