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
// 上调，「上调目标」同样按实测 − 1pp 下取整（后端 fail_under 自 #621 起同为此口径，两栈
// 一致；此前后端写的是「上调到实测下取整」，在 9050 units 的分母上只余 0.06pp ≈ 5 units，
// 而同一 commit 两次运行的噪声就有 5 units、base 漂移一天 0.27pp）。三轮收口：
// #484 首轮补测把基线从 80.64/85.11/66.33/81.35
// 抬到 90.02/88.02/93.06/91.86；#501（删 utils.ts 的 7 个全仓零调用死代码）+ #503
// （补齐 tradePairs/tradeAmounts/colors/logger 及 utils.ts 的零散分支）后到
// 100/99.65/100/100；#507 收紧 tradePairs 的异常 2 腿组规则——均非 CASH、双 CASH 均 sell
// 由 pair 改为回落 single（与既有的均 buy 兜底对齐，规则注释的边界即契约），分支补齐
// 后到全 100、阈值随棘轮 98→99。
// 残余：无。#504 已落地（quantizeAmount2 非有限结果返回 null）：tradeAmounts 的「到手无法
// 量化」守卫经「毛额与手续费异号 + 量级越界」用例（9e18, 1, -9e18）保持可达并已覆盖，
// 未产生新残余；本段与 frontend/AGENTS.md §3 已同批复核。
// 分母边界（#505 结论：维持 src/lib，hooks/stores 的缺口**成文接受**）：src/hooks/**
// （17 文件 / 2226 行，≈2 倍分母）与 src/stores/**（2 文件 / 146 行）不在 include 内，
// 于是一行测试都没有也不会让任何门禁变红——这是被记录下来、而不是被修掉的已知缺口。
// 两条扩张方案按实测否决：① 直接并入 → 分母 1141→3513 行，现有测试一行覆盖不到
// hooks/stores，四项上限即 1141/3513≈32%，99 的阈值须整体重定（等于用「先补 2200 行
// 测试」换一条当下不可达的门禁；且 16/17 个 hook 依赖 @tanstack/react-query，在没有
// DOM/Provider 的 environment: "node" 下连调用都起不来）。口径注：1141 / 2226 / 3513
// 都是 `wc -l` 的物理行数，只用来比量级；覆盖率真正的分母是 lcov 的可执行行（当前
// LF=280），所以「≈32%」是按体积比例的估计，不是能直接代进 thresholds 的百分比。
// ② 只并入 stores → 探针实测：一个断言 login/logout 契约且通过的 node 测试下，
// authStore.ts 仍只有 语句 36.4% / 分支 22.2% / 函数 37.5% / 行 40%，未覆盖的全是
// `typeof window` 守卫内的浏览器持久化路径（:7/:14 是 `=== "undefined"` 早退，:38/:46
// 是 `!== "undefined"` 守卫，落盘与 cookie 写在守卫内）。机制在 zustand 5.0.15 的
// persist：取不到 storage 时（node 下默认的 `window.localStorage` 抛错、被
// createJSONStorage catch 成 undefined）整体退化为「每次 set 只 console.warn、不落盘」，
// 所以并入换来的是**低覆盖**（会红）而非假覆盖，但真正要验的那条契约「登录态持久化」
// 在 node 下永远测不到。要收它先得有浏览器环境，即回到「不引 jsdom/RTL」的取舍。
// 职责边界不变：hooks 与组件交互归 Playwright E2E（§4）。重新评估的触发条件（可判定，
// 不是「以后再看」）：① 出现任一「根因在 hooks 内的纯计算逻辑、且 E2E 定位不到具体
// 分支」的线上缺陷——已知形态即 useDashboardStats 里 filter/reduce 出来的 totalValue
// 与 avgReturn：算式是纯的，却包在四个 react-query hook 底下，出错只在页面上表现为
// 「一个数看着不对」；② 决定引入 jsdom/RTL（届时 §3「不引 jsdom/RTL」的取舍与本段
// 阈值口径须同批修订）。
// 与 #509 的联动：include 是全局阈值与增量门禁**共用**的分母开关，改它必须同批改
// ci.yml 的 LCOV_SF_MIN（见下方 include 上方注释与 scripts/tests/test_ci_frontend_coverage.py）。
// 全局阈值（非 perFile）：新文件 0% 会让总量下滑，正是要拦的「靠既有高覆盖掩护新
// 代码」。另有一条「只看本 PR 改动行」的增量门禁（#485）：CI frontend-check 在 PR
// 事件对 coverage/lcov.info 跑 diff-cover（阈值以 ci.yml 的门禁步骤为单一来源，形态
// 与后端同），故 reporter 里有 lcovonly——其 projectRoot 必须置 ".."：该值相对 **cwd**
// 解析（不写时默认为 vitest root），而 diff-cover 把 LCOV 里的相对 SF 按 **git root**
// 解析，所以只有 cwd == <git root>/frontend 时（CI 由 job 的 working-directory 保证、
// 本地由 `cd frontend && npm run test` 保证）才得到 `frontend/src/...` 前缀。三种形态
// 均实测：cwd 在 frontend/ 且 projectRoot ".." → `frontend/src/lib/x.ts`（唯一可用）；
// 同 cwd 但删掉 projectRoot → `src/lib/x.ts`；从仓库根跑 `npx vitest --root frontend`
// （配置不动）→ `feature+…/frontend/src/lib/x.ts`。后两种 diff-cover 都匹配不到改动行、
// 门禁静默空转，且都不满足 ci.yml `Assert lcov data source (PR)` 的 `^SF:frontend/` 锚点
// （那里判红，另有 `Warn on attribution gap (PR)` 兜底）。
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
      // ⚠️ 下面这三行是**全局阈值与 CI 增量门禁共用的分母开关**（#509）：diff-cover 只
      // 忠实汇报 lcov 给它的那部分，所以收窄/删除 include 的失效方向是「门禁变松 + 覆盖
      // 率数字变好」——实测收到只剩一个文件时四项全 100%、门禁 exit 0，没有任何其他守卫
      // 会红。唯一能看见这件事的是 ci.yml `Assert lcov data source (PR)` 里的分母文件数
      // 下限（LCOV_SF_MIN），改这里必须同批改那里。
      // include 之外还有一个隐式前提：vitest 5 的 `coverage.all` 默认为真，故**未被任何
      // 测试 import 的分母文件也会以 0% 进入 lcov**（2026-09-19 实测：新建 src/lib 文件、
      // 无测试引用 → SF 10→11、LF=2/LH=0）。但 all 给的是**可见性**、不是必然判红：进了
      // lcov 之后仍按加权判定——全局侧当前 LF=280，加 2 行未覆盖仍是 99.29%（≥99 绿），
      // 到第 3 行才落到 98.94%；增量侧 `--fail-under=80` 比的是本 PR **全部**计量行的聚合
      // 比例（不是逐文件），小文件会被同批其它改动摊平。反过来，显式写 `all: false` 关掉
      // 的是可见性本身：该文件同时从 D 与 C∩D 消失，除分母棘轮外没有任何守卫知道它存在过。
      // 所以这里不写 all，而由守门断言钉住「不得显式置假」
      // （scripts/tests/test_ci_frontend_coverage.py）。
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
