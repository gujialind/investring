# frontend/AGENTS.md — 前端模块指南

> 视觉规范（语义色/涨跌色/图表色/数字格式）见 `docs/design/visual-spec.md`——**写前端代码前必读**。

## 1. 架构

技术栈版本以 `frontend/package.json` 为准（Next.js + React + Tailwind + shadcn/ui + Zustand + react-query；E2E 用 Playwright）。

### 1.1 双端路由与 Proxy

* 移动端 `/m/` 前缀、PC 端根路径；`src/proxy.ts`（Next 16 起由 `middleware.ts` 更名）按 User-Agent 自动重定向；未登录（无 `token` cookie）重定向到对应登录页。页面清单直接看 `src/app/**/page.tsx`；移动端多为薄壳页，套 `MobileLayout` 后渲染共享内容组件。

### 1.2 组件复用

* 复用三层：完全共享（`hooks/`、`stores/`、`components/ui/`、`types/`）→ 共享业务组件（`components/shared/`，以 `variant: "desktop" | "mobile"` + `basePath` 适配双端）→ 端侧独立（`components/mobile/`、`desktop/`、`layout/`、`charts/`）。

* API 层 `src/lib/api/` 按域拆分、经 `index.ts` barrel 统一导出（`@/lib/api`）；`next.config.js` 将 `/api/:path*` rewrite 到 `API_BASE_URL`（默认 localhost:8000）。

* **确认类弹窗统一走对应 `/preview` 端点**：`TradeConfirmDialog`（`useTradePreview`）、`SubscriptionConfirmDialog`（`useSubscriptionPreview`）、`EventConfirmDialog`（`useShareChangeEventPreview`，#424）。**不要直接渲染列表行的计算字段**——「用户填的字段」落库了，「确认时才算的字段」在 pending 阶段是 NULL，直接渲染会被格式化兜底成误导性的 `0.00`（#424 的成因正是这里一次例外）。约定的四条：① hook 用 `retry: false` + `staleTime: 0`（弹窗重开必 refetch，预览值即确认值，不得基于过期值确认）；② 加载态取 `isLoading || isFetching`；③ 错误经 `ConfirmInfoDialog` 的 `error` 通道展示并禁用确认按钮（`getErrorMessage` 已解出后端 `detail.message`，**不要再叠「预览失败：」前缀**）；④ 内容区加 `data?.preview` 守卫，避免重开命中缓存时先渲染上一次的值。同域的**列表列**若该状态下方未计算，显示 `--` 而非 `0.00`。

* 版本号：设置页「系统信息」显示构建期注入的 `NEXT_PUBLIC_APP_VERSION`（`next.config.js` 读 `package.json` version；该值由发布流程从仓库根 `VERSION` 同步，勿手改，见 `docs/reference/versioning.md`）。

### 1.3 日志（issue #407）

* **业务代码禁止直调 `console.*`**：一律 `import { createLogger } from "@/lib/logger"` 并用 `createLogger("<模块>")` 取带 tag 的实例（前缀统一为 `[InvestRing][<tag>][<LEVEL>]`）。`debug` 在生产构建下自动 no-op，故排查语句可以留在代码里。
* 护栏在 `eslint.config.mjs`：`files: ["src/**"]` 上 `no-console: error`，**唯一豁免 `src/lib/logger.ts` 与其单测**（前者是唯一封装层、后者需对 console 做 spy；豁免登记见 `docs/design/visual-spec.md` §1.5）。作用域限定是因为 `scripts/visual-shot.mjs` 有 8 处脚本自身的 `console.*`。
* **渲染期异常**由 App Router 约定文件兜底：`src/app/error.tsx`（根）、`src/app/m/error.tsx`（移动端）、`src/app/global-error.tsx`（根 layout 自身抛错，自带 `<html>`/`<body>` 与 `globals.css`），共用 `src/components/shared/ErrorFallback.tsx`——改兜底 UI 要同时看这三处与 visual-spec。**不自造 `<ErrorBoundary>` 组件**：Next 的约定文件即边界。
* 级别口径、meta 处理与「为什么不做客户端上报」见 `docs/reference/logging.md` §4。

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
python3 backend/scripts/run_e2e_backend.py   # 1. 起本地后端（自动种子，监听 :8000；
                                             #    E2E_DB_PATH / E2E_PORT 可覆盖库与端口，
                                             #    见 backend/AGENTS.md「E2E 相关脚本」）
cd frontend && npm run build \
  && cp -r .next/static .next/standalone/.next/static \
  && cp -r public .next/standalone/public    # 2. 生产构建 + 组装 standalone
npm run test:e2e                             # 3. 跑测试
```

- **本地默认只跑影响面 spec，全量由 CI 兜底**（`frontend-e2e` job 合入前强制跑全套）：`npx playwright test e2e/regression.spec.ts` 或 `--grep "关键词"` 圈定；质量门禁（`verify-frontend.sh`）仍必须本地过。影响面拿不准就宁宽勿窄。注意：门禁只是静态层（lint/tsc/build），**运行时行为（水合、API 联调、交互流程）只有 E2E 能拦**（历史 P0 均如此）；且 CI 种子含 draft `E2E_PORT` 与 active `E2E_ACTIVE` 两个组合，快照/持仓/编辑交易类用例在 CI 真跑（#354 前因只有 draft 组合而恒 skip）——动交互流程的改动至少要本地跑对应 spec。
- **webServer 是 production standalone**（`node .next/standalone/server.js`），**不是 `npm run dev`**——dev 按需编译竞态是历史 flaky 根因（issue #171）。端口由 `use.baseURL` 派生（`webServer.port`，不设 `BASE_URL` 时即 :3000），故需要隔离栈的调用方把 `BASE_URL` 指向自己的端口后，Playwright 不会再在 :3000 上另起一份它控制不了的服务。
- **数据依赖**（种子见 `backend/tests/seed_base.py`）：登录 ADMIN/admin@2026（`auth.setup.ts`，storageState `e2e/.auth/admin.json`）；两个种子组合是契约——draft 组合 `E2E_PORT`（零交易/申赎/快照，承载表单交互与首购激活类用例）+ active 组合 `E2E_ACTIVE`（#354：首购确认 + 已确认场内交易 + 连续 2 日快照 + 1 笔 pending 场内交易，承载快照/持仓/编辑交易类用例）；另有 4 平台 + 产品（含 161017 LOF 双市场种子）。**日期锚定**（#468）：交易日经 `/api/trading-calendar` 取「today 起最近一个交易日」，不写死年份、不假设周末（日历终点随 today 滚动，固定日期会随时间失效）。
- **按 code 直达，不再 `.first()`**（#354）：所有业务 spec 经 `e2e/helpers.ts` 按组合 code 导航（`gotoPortfolioDetail` / `gotoPortfolioSubpage` / `portfolioPath`），不再经组合列表 `.first()`——`list_portfolios` 无 ORDER BY，新增组合后「首个」不确定。**两个组合是种子契约：缺组合或形态退化即硬失败，helper 不做优雅 skip**（旧惯例下种子退化会让用例在 CI 静默全 skip、覆盖无声蒸发，正是 #354 要消除的）。`portfolioPath` 恒返回桌面路径，mobile project 靠 `src/proxy.ts` 按 UA 重定向到 `/m`，结构性消除 `href^="/portfolio/"` 类只在桌面成立的定位。
- **`test.skip` 只留给真正条件性数据与两类合法端专属**：平台数 < 2、无平台/产品数据、LOF 双市场种子缺失；端专属仅「功能确实缺」「输入设备语义缺」两类（见下条）。「同一控件两端各测一次、互为镜像」属去重、不是端专属理由，skip 文案须点名镜像用例。**禁止对 `E2E_ACTIVE` 跑 recalculate/catch-up/generate-next**——auto_confirm 会吃掉那笔 pending 交易、破坏「编辑交易」用例契约。改种子时对照 `e2e/*.spec.ts` 头部「数据说明」注释与 `backend/tests/integration/test_seed_contract.py`。
- **「移动端无此页/无此路由」不是端专属理由，是待证前提**（#371）：`components/shared/*Content.tsx` 桌面/移动共用同一组件与同一 Dialog，`variant` 一般只改栅格列数、筛选栏折叠与控件宽度，故桌面断言多能 1:1 移植；移动路由是否存在以 `src/app/m/portfolio/[code]/` 为准，不靠印象。曾有两处据此丢弃移动端覆盖（`datepicker-in-dialog` 用例 6 的 `if (!isMobile)`、`platform-select-search` 用例 11 的 `test.skip`），实测路由存在、放开后双端直接通过——覆盖无声蒸发了很久，而两端都是绿的。合法的端专属 skip 只有两类：**功能确实缺**（移动端无现金转移）、**输入设备语义缺**（移动端无物理键盘）。#383 已把剩余 9 处「桌面断言仅针对桌面项目」skip 逐条复核完毕，**前提全为假**：5 处表单类（platform-select-search 用例 2/3/4/5/10）双端共用同一 Dialog，直接删 skip；4 处筛选栏类（用例 1/8/9/12）经 `helpers.openFilterPanelIfMobile` 展开移动端折叠面板后双端同断言；该 spec 端专属 skip 只剩用例 6/7/13。**「桌面断言仅针对桌面项目」这句文案自此不得再出现**——要 skip 就点名是哪一类合法理由，否则等于把未证前提写进代码。
- **选择框弹层交互一律走 `e2e/helpers.ts`**（#372）：平台/产品选择框的触发器、弹层、选项行与「无数据优雅 skip」的等待+文案由 helpers 单点持有，spec 内禁止再复制 `xpath=ancestor::div[@role="dialog"][1]` 与「环境中没有平台/产品数据」文案——三份拷贝曾各自漂移（同一批产品选项行，一处按 Tailwind `div.cursor-pointer`、两处按 `data-testid="product-option"` 定位）。**注意「首行 waitFor 失败即 skip」与「读全量后按条数 skip」是两种不同触发条件**（platform-select-search 用例 6/10 属后者），不要互相改写。
- **定位器契约由 eslint 结构性拦截**（#382）：`e2e/**/*.ts` 作用域的 `no-restricted-syntax`（`eslint.config.mjs` 的 `locatorContractSelectors`）禁止三类写法——按 lucide 图标类名定位、在 xpath 里按 class 匹配祖先/后代、按 Tailwind 工具类定位；需要锚点就在组件侧补 `data-testid`。此前所有 `no-restricted-syntax` 块都是 `src/**` 作用域，结构上覆盖不到 e2e/，而 #217 是以「grep 零残留」验收的、从无机器保障，故残留长期存在。库公开类名 API 不在拦截面内（datepicker 的 `rdp-day_button` 真正锚点是 `data-day` 属性，勿改）。
- `auth.spec.ts` 三用例必须通过（登录是硬依赖）；platform-select-search 部分用例还需 ≥2 平台/≥2 投资人/产品。
- projects：setup / chromium（桌面）/ mobile（iPhone 13 webkit）；端专属用例只剩上述两类合法理由与「镜像去重」（platform-select-search 用例 7 / 13 互为镜像），另一端 skip 属预期。

### E2E / 目检前置检查

跑 `npm run test:e2e` 或 `scripts/visual-verify.sh` 前，先核对监听进程归属：

```bash
ss -tlnp | grep -E ":8000|:3000"
readlink /proc/<pid>/cwd  # 看是否当前 worktree
```

- cwd 已 `(deleted)` → 死会话残留，可安全 `kill <pid>`（脚本会自起新服务）
- cwd 属其他工作树 → 不要动（可能干扰并行会话）
- cwd 是当前工作树 → 正常，复用即可

不检查的后果：`playwright.config.ts` 本地 `reuseExistingServer: !process.env.CI` 会复用旧服务，测到旧代码或 ECONNREFUSED；`visual-verify.sh` 同样复用已监听服务。

### 目检（视觉验证）

```bash
../scripts/visual-verify.sh                                              # 双端截三个流水列表（默认 E2E_ACTIVE，恒有行）
../scripts/visual-verify.sh --device mobile --path /portfolio/E2E_PORT/positions   # 参数原样转给 visual-shot.mjs
```

- **第三条验证层**：§2 门禁（lint/tsc/build）看不到运行时，§3 单测只覆盖 lib 纯函数，E2E 断言定位与文本、**不断言像素**——列宽挤压、CJK 竖排换行、双行单元格错位、结对行 `colSpan` 对不齐这类问题只有人眼看图能拦（#355「市场」列窄到「A股场内」四字竖排是 issue 里人眼发现的，e2e 全程绿灯）。改 `components/shared/*Content.tsx` 的表格/图表列结构时按本节目检。
- 两个文件分工：`scripts/visual-verify.sh` 管服务与构建（复用已监听的 :8000/:3000，否则起后端 → `npm run build` → 组装 standalone → 起 `server.js`），`frontend/scripts/visual-shot.mjs` 管登录态与截图。参数（`--path` 可重复 / `--device desktop|mobile` 可重复 / `--out` / `--base`）以 `visual-shot.mjs` 头部注释为单一事实来源，勿在此处另立清单。
- **视口与 E2E 同口径**：桌面 `Desktop Chrome` 1280×720、移动 `iPhone 13`（webkit），故 `--path` 只写桌面路径，靠 `src/proxy.ts` 按 UA 重定向到 `/m`（同 `portfolioPath` 的道理，不必写两条）。1280 是**保守值**——越窄越容易暴露挤压。每个 `--path` 出两张图：`*-<device>.png` 整页（fullPage）与 `*-<device>-table.png` 表格裁剪（放大读列布局）。
- **空表目检等于没目检，但造数会污染 e2e 同一个库**：目检与 `npm run test:e2e` 共用 `/tmp/ir_e2e.db`，脚本因此优先复用已运行的后端（重启即清库重灌）。用完的临时记录要么 `DELETE` 掉，要么 kill 后端让下次 e2e 重灌种子，否则多出来的行会打脏行数 / `.first()` 类断言。份额变动事件在 `E2E_ACTIVE` 无种子行，需先造一条：`ex_date` 取晚于最新快照日的交易日、`entitlement_date` 取其前一交易日（先查 `snapshots` 与 `trading-calendar` 定日期），截图后删。**目检同样禁止对 `E2E_ACTIVE` 跑 recalculate/catch-up/generate-next**（红线见上一节）。

### E2E 归一化对比（纯测试重构验证）

纯测试重构 PR（如 #372 helper 收敛、#371/#383 skip 复核）需验证 pass/skip 形态未变——由 CI 的 `e2e-compare` 系 job 兜底（issue #410；原本地脚本 `verify-e2e-pr.sh` 已随其隔离层整删，演进决策见该 issue）。

- **触发**：PR 改动命中 `frontend/e2e/**`、`frontend/playwright.config.ts`、`backend/tests/seed_base.py`、`backend/scripts/seed_e2e.py`、`scripts/e2e_normalize.py`、`scripts/tests/` 或 `ci.yml` 时自动跑——baseline/candidate 两 job 各跑一遍**全量** E2E，归一化成 TSV 后 diff。**两侧构建源同为 PR head，唯一变量是 `frontend/e2e/` 目录**（baseline 侧 `rm -rf frontend/e2e` 后 checkout base 版本；src 侧改动被两侧同等看到，故 #401 那种「重构 + 补 data-testid」的 PR diff 依然可读）。
- **期望空 diff；非空 diff 硬 fail**。形态变化属预期时（增删用例/调整 skip）给 PR 打 `e2e-morph-expected` 标签——labeled 事件自动重跑 CI，compare 转为 warning 放行。diff 全文在 compare job 的 Summary，raw JSON 与失败证据在 `e2e-raw-*` artifact。
- **采集口径**：`--retries=0 --workers=2`（retry 会把 flaky 洗成 passed、形态对比失真）；归一化逻辑在 `scripts/e2e_normalize.py`（stdlib-only），`scripts/tests/test_e2e_normalize.py` 锁定 JSON reporter 形态假设——**Playwright 升级后先跑这个单测**（#402 教训：1.62 把 title 挪到 spec 节点自身）。TSV 列：`[spec, project, 用例标题, status, 结果, skip 文案]`，按行排序；第 4 列 `tests[].status` ∈ `expected|unexpected|flaky|skipped`（`passed` 永不出现），第 5 列 `results[-1].status` ∈ `passed|failed|skipped|timedOut`；`setup` project 的行已滤除。
- **已知噪音**：两 capture 若跨 0 点起跑，`seed_e2e_active` 的 `date.today()` 锚点不同会产假 diff——重跑即愈。

本地手工菜谱（排查 CI compare 红时的临时对比）：先按本节开头三步起一个栈，然后在两个 worktree（base 内容与 PR 内容）里各采集一次再 diff：

```bash
# worktree A（base 内容）里：
cd frontend && CI= PLAYWRIGHT_JSON_OUTPUT_FILE=/tmp/e2e-raw-a.json \
  npx playwright test --retries=0 --reporter=list,json   # rc 非 0 不拦，fail 进 JSON
python3 ../scripts/e2e_normalize.py --input /tmp/e2e-raw-a.json --output /tmp/a.tsv \
  --projects chromium,mobile --side base
# worktree B（PR 内容）里同法产 /tmp/b.tsv，然后：
diff /tmp/a.tsv /tmp/b.tsv
```

同一栈同一数据是可比性来源；`CI=` 置空让 `reuseExistingServer` 复用已起栈。两跑之间重启一次后端（重灌种子）可消除上一跑的数据残留。

CI compare 红的重跑语义（#414 实踩）：因 capture 失败而被 skip 的 compare **无法单独重跑**——`gh run rerun --job <compare>` 返回 `cannot be rerun`，且新 attempt 产生后旧 attempt 的失败 job 也不可再重跑；须重跑失败的 capture job（会级联带起 compare + CI OK）或 `gh run rerun <run-id>` 整 run 重跑。
