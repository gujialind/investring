# frontend/AGENTS.md — 前端模块指南

> 视觉规范（语义色/涨跌色/图表色/数字格式）见 `docs/design/visual-spec.md`——**写前端代码前必读**。

## 1. 架构

技术栈版本以 `frontend/package.json` 为准（Next.js + React + Tailwind + shadcn/ui + Zustand + react-query；E2E 用 Playwright）。

### 1.1 双端路由与 Proxy

* 移动端 `/m/` 前缀、PC 端根路径；`src/proxy.ts`（Next 16 起由 `middleware.ts` 更名）按 User-Agent 自动重定向；未登录（无 `token` cookie）重定向到对应登录页。页面清单直接看 `src/app/**/page.tsx`；移动端多为薄壳页，套 `MobileLayout` 后渲染共享内容组件。

### 1.2 组件复用

* 复用三层：完全共享（`hooks/`、`stores/`、`components/ui/`、`types/`）→ 共享业务组件（`components/shared/`，以 `variant: "desktop" | "mobile"` + `basePath` 适配双端）→ 端侧独立（`components/mobile/`、`desktop/`、`layout/`、`charts/`）。

* API 层 `src/lib/api/` 按域拆分、经 `index.ts` barrel 统一导出（`@/lib/api`）；`next.config.js` 将 `/api/:path*` rewrite 到 `API_BASE_URL`（默认 localhost:8000）。

* 版本号：设置页「系统信息」显示构建期注入的 `NEXT_PUBLIC_APP_VERSION`（`next.config.js` 读 `package.json` version；该值由发布流程从仓库根 `VERSION` 同步，勿手改，见 `docs/reference/versioning.md`）。

***

## 2. 质量门禁

```bash
../scripts/verify-frontend.sh        # 推送前本地门禁（与 CI frontend-check 同口径）
../scripts/verify-frontend.sh --quick  # 跳过 build
```

等价于 `npm run lint` + `npx tsc --noEmit` + `npm run test` + `npm run build`；构建期强制 0 error。

## 3. 单元测试（Vitest，issue #253）

```bash
npm run test         # vitest run（全量，<10s）
npm run test:watch   # watch 模式
```

- 范围：**lib 层纯逻辑**（utils format 系 / tradePairs 结对 / allocation 聚合 / dimensions 维度 / validation 表单校验），node 环境，不引 jsdom/RTL——组件交互与运行时行为归 Playwright E2E（职责不重叠）。
- 约定：测试与源码 colocated（`src/lib/*.test.ts`），显式 `import { describe, it, expect } from "vitest"`（未开 globals）；alias `@` 在 `vitest.config.ts` 手动维护。
- 注意：`src/lib/api/` 是纯类型化 axios 薄封装（无数据转换逻辑），不在单测范围；新增 lib 纯函数应同步补测试。

## 4. E2E（Playwright）

```bash
python3 backend/scripts/run_e2e_backend.py   # 1. 起本地后端（自动种子，监听 :8000）
cd frontend && npm run build \
  && cp -r .next/static .next/standalone/.next/static \
  && cp -r public .next/standalone/public    # 2. 生产构建 + 组装 standalone
npm run test:e2e                             # 3. 跑测试
```

- **本地默认只跑影响面 spec，全量由 CI 兜底**（`frontend-e2e` job 合入前强制跑全套）：`npx playwright test e2e/regression.spec.ts` 或 `--grep "关键词"` 圈定；质量门禁（`verify-frontend.sh`）仍必须本地过。影响面拿不准就宁宽勿窄。注意：门禁只是静态层（lint/tsc/build），**运行时行为（水合、API 联调、交互流程）只有 E2E 能拦**（历史 P0 均如此）；且 CI 种子含 draft `E2E_PORT` 与 active `E2E_ACTIVE` 两个组合，快照/持仓/编辑交易类用例在 CI 真跑（#354 前因只有 draft 组合而恒 skip）——动交互流程的改动至少要本地跑对应 spec。
- **webServer 是 production standalone**（`node .next/standalone/server.js`，:3000），**不是 `npm run dev`**——dev 按需编译竞态是历史 flaky 根因（issue #171）。
- **数据依赖**（种子见 `backend/tests/seed_base.py`）：登录 ADMIN/admin@2026（`auth.setup.ts`，storageState `e2e/.auth/admin.json`）；两个种子组合是契约——draft 组合 `E2E_PORT`（零交易/申赎/快照，承载表单交互与首购激活类用例）+ active 组合 `E2E_ACTIVE`（#354：首购确认 + 已确认场内交易 + 连续 2 日快照 + 1 笔 pending 场内交易，承载快照/持仓/编辑交易类用例）；另有 4 平台 + 产品（含 161017 LOF 双市场种子）。
- **按 code 直达，不再 `.first()`**（#354）：所有业务 spec 经 `e2e/helpers.ts` 按组合 code 导航（`gotoPortfolioDetail` / `gotoPortfolioSubpage` / `portfolioPath`），不再经组合列表 `.first()`——`list_portfolios` 无 ORDER BY，新增组合后「首个」不确定。**两个组合是种子契约：缺组合或形态退化即硬失败，helper 不做优雅 skip**（旧惯例下种子退化会让用例在 CI 静默全 skip、覆盖无声蒸发，正是 #354 要消除的）。`portfolioPath` 恒返回桌面路径，mobile project 靠 `src/proxy.ts` 按 UA 重定向到 `/m`，结构性消除 `href^="/portfolio/"` 类只在桌面成立的定位。
- **`test.skip` 只留给真正条件性数据**：平台数 < 2、无平台/产品数据、LOF 双市场种子缺失、端专属用例（另一端 skip 属预期）。**禁止对 `E2E_ACTIVE` 跑 recalculate/catch-up/generate-next**——auto_confirm 会吃掉那笔 pending 交易、破坏「编辑交易」用例契约。改种子时对照 `e2e/*.spec.ts` 头部「数据说明」注释与 `backend/tests/integration/test_seed_contract.py`。
- `auth.spec.ts` 三用例必须通过（登录是硬依赖）；platform-select-search 部分用例还需 ≥2 平台/≥2 投资人/产品。
- projects：setup / chromium（桌面）/ mobile（iPhone 13 webkit）；部分用例是端专属（另一端内 skip，属预期）。

### 目检（视觉验证）

```bash
../scripts/visual-verify.sh                                              # 双端截三个流水列表（默认 E2E_ACTIVE，恒有行）
../scripts/visual-verify.sh --device mobile --path /portfolio/E2E_PORT/positions   # 参数原样转给 visual-shot.mjs
```

- **第三条验证层**：§2 门禁（lint/tsc/build）看不到运行时，§3 单测只覆盖 lib 纯函数，E2E 断言定位与文本、**不断言像素**——列宽挤压、CJK 竖排换行、双行单元格错位、结对行 `colSpan` 对不齐这类问题只有人眼看图能拦（#355「市场」列窄到「A股场内」四字竖排是 issue 里人眼发现的，e2e 全程绿灯）。改 `components/shared/*Content.tsx` 的表格/图表列结构时按本节目检。
- 两个文件分工：`scripts/visual-verify.sh` 管服务与构建（复用已监听的 :8000/:3000，否则起后端 → `npm run build` → 组装 standalone → 起 `server.js`），`frontend/scripts/visual-shot.mjs` 管登录态与截图。参数（`--path` 可重复 / `--device desktop|mobile` 可重复 / `--out` / `--base`）以 `visual-shot.mjs` 头部注释为单一事实来源，勿在此处另立清单。
- **视口与 E2E 同口径**：桌面 `Desktop Chrome` 1280×720、移动 `iPhone 13`（webkit），故 `--path` 只写桌面路径，靠 `src/proxy.ts` 按 UA 重定向到 `/m`（同 `portfolioPath` 的道理，不必写两条）。1280 是**保守值**——越窄越容易暴露挤压。每个 `--path` 出两张图：`*-<device>.png` 整页（fullPage）与 `*-<device>-table.png` 表格裁剪（放大读列布局）。
- **空表目检等于没目检，但造数会污染 e2e 同一个库**：目检与 `npm run test:e2e` 共用 `/tmp/ir_e2e.db`，脚本因此优先复用已运行的后端（重启即清库重灌）。用完的临时记录要么 `DELETE` 掉，要么 kill 后端让下次 e2e 重灌种子，否则多出来的行会打脏行数 / `.first()` 类断言。份额变动事件在 `E2E_ACTIVE` 无种子行，需先造一条：`ex_date` 取晚于最新快照日的交易日、`entitlement_date` 取其前一交易日（先查 `snapshots` 与 `trading-calendar` 定日期），截图后删。**目检同样禁止对 `E2E_ACTIVE` 跑 recalculate/catch-up/generate-next**（红线见上一节）。
