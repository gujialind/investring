import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// 单元测试层（issue #253）：node 环境纯逻辑测试，不引 jsdom/RTL
//（组件交互由 Playwright E2E 兜底）。手动 alias 对齐 tsconfig paths，不引 vite-tsconfig-paths。
//
// 覆盖率门禁（issue #464）：分母只圈 src/lib/** 纯逻辑层——src/lib/api/** 是类型化
// axios 薄封装（无数据转换逻辑，见 frontend/AGENTS.md §3），计入分母会稀释信号。
// 阈值 = 2026-09-16 实测（stmts 100 / branch 99.65 / funcs 100 / lines 100）「取低 1pp
// 后下取整」→ 99/98/99/99，只升不降。按「实测 − 1pp」而非「实测下取整」是有意的余量
// 校验：阈值紧贴实测会让「新增一条未覆盖分支/函数」或「工具链升级（vitest 系）改了
// 统计口径」这类与代码质量无关的变动把门禁弄红——本配置曾按「实测下取整」定
// 80/85/66/81，branches 只余 0.11pp（≈0.4 条分支：新增 1 条未覆盖分支即
// 263/310=84.84%<85 变红），#464 评审遂要求四项统一改按「实测 − 1pp」定（functions
// 先落到 65）。棘轮：实测超阈值 ≥1pp 时在当次 PR 顺手
// 上调，「上调目标」同样按实测 − 1pp 下取整（后端 fail_under 是上调到实测下取整，前端
// 分母小、刻意多留 1pp）。两轮收口：#484 首轮补测把基线从 80.64/85.11/66.33/81.35
// 抬到 90.02/88.02/93.06/91.86；#501（删 utils.ts 的 7 个全仓零调用死代码）+ #503
// （补齐 tradePairs/tradeAmounts/colors/logger 及 utils.ts 的零散分支）后到上列基线。
// 残余：tradePairs.ts 的 groupTradeRows 双 CASH 守卫（L49；前置 43-44 行的基金腿判定
// 已排除其余组合，条件恒真、false 路不可达）——保留该冗余守卫，防未来判定逻辑引入
// 第三类产品时被静默当作现金转移处理。⚠️ 该「唯一」会随 #504 落地失效（quantizeAmount2
// 修好后，tradeAmounts 的「到手无法量化」守卫失去触发路径、残余变两处），届时同步复核
// 本段与 frontend/AGENTS.md §3。
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
        statements: 99,
        branches: 98,
        functions: 99,
        lines: 99,
      },
    },
  },
});
