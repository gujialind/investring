# frontend/AGENTS.md — 前端模块指南

> 前端任务先读本指南；涉及业务语义时按[业务规则](../docs/reference/business-constraints.md)定位对应领域。涉及布局、颜色、数字展示或兜底 UI 时加读[视觉规范](../docs/design/visual-spec.md)；非视觉交互仍遵守本指南的双端、预览与验证约束。改 E2E 或做纯测试重构时读 [E2E 指南](e2e/AGENTS.md)。文档维护遵循 [AI 文档规范](../docs/reference/documentation.md)。

## 1. 架构

技术栈版本以 `frontend/package.json` 为准（Next.js + React + Tailwind + shadcn/ui + Zustand + react-query；E2E 用 Playwright）。

### 1.1 双端路由与 Proxy

* 移动端 `/m/` 前缀、PC 端根路径；`src/proxy.ts`（Next 16 起由 `middleware.ts` 更名）按 User-Agent 自动重定向；未登录（无 `token` cookie）重定向到对应登录页。页面清单直接看 `src/app/**/page.tsx`；移动端多为薄壳页，套 `MobileLayout` 后渲染共享内容组件。

### 1.2 组件复用

* 复用三层：完全共享（`hooks/`、`stores/`、`components/ui/`、`types/`）→ 共享业务组件（`components/shared/`，以 `variant: "desktop" | "mobile"` + `basePath` 适配双端）→ 端侧独立（`components/mobile/`、`desktop/`、`layout/`、`charts/`）。

* **`basePath` 是「前缀不带尾斜杠」，类型为 `"" | "/m"`**（#658）：数据里的路径自带前导 `/`，`${basePath}${href}` 若 `basePath="/" ` 会拼成 `"//dashboard"`——浏览器按**协议相对 URL** 解析成名为 `dashboard` 的 host，链接离开本站、`pathname === href` 的激活判定也恒假。这类错误 lint/tsc/build/E2E 四层都碰不到（chromium project 固定 1280 宽、该导航 `lg:hidden`；mobile project 走 `/m` 拼接恰好正确），故把契约写进**类型**而不是注释，并在 `e2e/regression.spec.ts` 的「桌面窄屏底部导航」用例里以 900px 视口钉住。新增分端前缀时沿用 `"" | "/m"`，不要接受任意字符串。

* API 层 `src/lib/api/` 按域拆分、经 `index.ts` barrel 统一导出（`@/lib/api`）；`next.config.js` 将 `/api/:path*` rewrite 到 `API_BASE_URL`（默认 localhost:8000）。

* **确认类弹窗统一走对应 `/preview` 端点**：`TradeConfirmDialog`（`useTradePreview`）、`SubscriptionConfirmDialog`（`useSubscriptionPreview`）、`EventConfirmDialog`（`useShareChangeEventPreview`，#424）。**不要直接渲染列表行的计算字段**——「用户填的字段」落库了，「确认时才算的字段」在 pending 阶段是 NULL，直接渲染会被格式化兜底成误导性的 `0.00`（#424 的成因正是这里一次例外）。约定的四条：① hook 用 `retry: false` + `staleTime: 0`（弹窗重开必 refetch，预览值即确认值，不得基于过期值确认）；② 加载态取 `isLoading || isFetching`；③ 错误经 `ConfirmInfoDialog` 的 `error` 通道展示并禁用确认按钮（`getErrorMessage` 已解出后端 `detail.message`，**不要再叠「预览失败：」前缀**）；④ 内容区加 `data?.preview` 守卫，避免重开命中缓存时先渲染上一次的值；⑤ **弹窗内的业务输入必须进 preview 的 query key**（#493：调仓确认的到账日/到账平台经 `queryKeys.trades.previewWith(id, options)` 分键），否则用户改了输入仍读旧预览、把过期值当确认值提交；与后端缺省同值的选项要**归一为「不传」**，避免同一有效选项分裂出多个缓存键。同域的**列表列**若该状态下方未计算，显示 `--` 而非 `0.00`。

* **读侧查询失败态单点 = `components/shared/QueryErrorState.tsx`**（#647/#655/#663）：入参 `error` + `onRetry` + 可选紧凑 `className`（卡内 `mt-3`、页级/区块级用缺省 `py-8 text-center`，与 `EmptyState` 同尺寸）。三条不得漂移的口径：① **失败不得伪装成空态**——`isError` 分支必须排在「长度为 0」之前，只解 `data`/`isPending` 而不解 `isError` 时，后端故障会被说成「这个区间没有净值」；② 组件不在 vitest 覆盖率分母内（分母是 `src/lib/**`），**改一处失败呈现必须同批改一条 E2E 断言**（`page.route` 造 500 → 断该卡含后端消息、不含同页空态文案、重试可见），否则判序被改回去不会让任何门禁变红；③ 判序**不可观测时不要顺手重排**——`ProductsContent` 保留 `products.length === 0 &&` 前件，因实测请求失败时 react-query 清空 `data`（旧行集不在屏上），重排不改变可见行为；真要改成失败优先，先造出「失败但保留旧数据」的形态再改。**例外已归零（#681）**：曾登记为台账的三处**整页**手写同形块已全部 import 本组件，页级/区块级不再有副本（三处的组件与行号落点见 issue #681 正文）；收敛时同批补的四条 E2E 在 `e2e/platform-detail.spec.ts`（三条）与 `e2e/product-detail.spec.ts`（一条）。其中平台详情那条**不能只断一个端点**：页级守卫是 `productError || platformError`，`onRetry` 必须 `refetchProduct()` 与 `refetchPlatform()` 一起发，只重发一条时整页不恢复（仍停在失败块、文案换成没重发那条），但不报错、也不让任何静态门禁变红，故该用例以「点击前放行 mock → 两个 `waitForRequest` → 断整页恢复」做联合归因（issue 原单 URL 配方在那种退化下依然绿）。**#681 归零的是「本组件同形副本」，#683 归零的是「从未接入失败态的那四处」**（A/B/C/D 的组件与卡形态见 issue #683 正文），**两者都不等于「全仓读侧失败路径已收敛」**：Dialog 内走 destructive inline Alert（口径见 `docs/design/visual-spec.md` §14）属既有分工；**同族已知至少六处未接入**（连失败 toast 都没有、完全无声），名单与逐处取数行、落地文案见 issue #683 正文「同类剩余面」表；**这份名单非穷尽，不要当穷尽基线对账**——#683 原写的「全量 grep `components/shared/`」经 PR #685 的 L2 独立扫描证伪（`SettingsContent` 即系漏项补入）。现状更正两处：#715 起原列的 `TasksContent` 随任务页下线删除、不再计入；`SettingsContent` 的「日志管理」Tab 读侧已由新组件 `LogsContent` 接入本组件（登录/审计/错误日志三面板 `isError` 分支排在 `length===0` 前，E2E 见 `e2e/regression.spec.ts` 的「日志管理 Tab 与批量同步迁移」块），但其数据源配置/交易日历同步两处读取仍无声。**页面级入口（`src/app/**`）与 `components/layout/` 本就不在该 grep 口径内**，另有四处同病症登记（仪表盘失败时**统计全 0** + 落「暂无活跃组合」、组合详情落「组合不存在」、持仓页（`src/app/**/positions`，页面级取数）与通知铃——均把后端故障说成「没有数据/不存在」），逐处行号与落地文案见 issue #683 正文「口径外新发现」，并维持该 issue「只留痕、不建条目」的决定。改这些时按口径①重新推导，不套用「已归零」，也不得据本条反向推定其余已收敛。卡片数据区的局部加载态（在途保留旧值 + 显式 `aria-busy` 状态标记，#683 D-1 外推）见 `docs/design/visual-spec.md` §14——规则正文在彼处，本条只留指针。

* **给 Dialog 限高只改内层容器，`DialogContent` 自身不得带 `overflow-*`**（#640 U-1）：`ui/dialog.tsx` 的 #191 方案 C 把 Dialog 内 Popover 的 Portal 容器**设为 DialogContent 节点本身**（`DialogPortalContainerContext`），滚动容器是它的**子节点** `div.grid max-h-[min(calc(100dvh_-_2rem),44rem)] overflow-y-auto`——那句「滚动容器与弹层平级，故不被 overflow 裁剪」只对**内层**成立。把 `max-h-[90vh] overflow-y-auto` 加到 DialogContent 自己身上，弹层就落进裁剪盒：月历底行被裁到 Dialog 盒底以下，那点落在遮罩上，**连人带表单一起把弹窗关掉**（不是「点了没反应」）。**裁剪区的可达性分端，不要写成全称**（#640 第四轮实测）：WebKit 下容器不产生溢出高度（`scrollHeight == clientHeight`），被裁的格子既点不到也滚不到；Chromium 下弹层自身把 `scrollHeight` 顶过 `clientHeight`，滚轮、以及 Playwright `click` 前的 `scrollIntoViewIfNeeded` 都能把它救回。故**同一缺陷双端表现不一致，承重端是 mobile/WebKit**——反证跑「桌面绿、移动红」不等于没坏，也不等于两端都坏。高度上限内层已备好，无需在 Content 上再加一遍。

* 版本号：设置页「系统信息」显示构建期注入的 `NEXT_PUBLIC_APP_VERSION`（`next.config.js` 读 `package.json` version；该值由发布流程从仓库根 `VERSION` 同步，勿手改，见 `docs/reference/versioning.md`）。

### 1.3 日志（issue #407）

* 业务日志只经 `@/lib/logger`，渲染异常由 Next 约定边界处理；级别、meta、护栏豁免与不做客户端上报的理由统一见[前端日志规范](../docs/reference/logging.md#logging-frontend)。
* 改兜底 UI 同时检查根、移动端、global-error 三个入口及共享 `ErrorFallback`，并加载视觉规范。

***

## 2. 质量门禁

```bash
../scripts/verify-frontend.sh        # 推送前本地门禁（与 CI frontend-check 的本地四步同口径）
../scripts/verify-frontend.sh --quick  # 跳过 build
```

等价于 `npm run lint` + `npx tsc --noEmit` + `npm run test` + `npm run build`；构建期强制 0 error。

依赖安装口径（#577）：**本地前端安装命令 = `npm ci`**，与 `ci.yml` / `e2e-stack.yml` 的 `Install dependencies` 步骤字面一致——lock 的 `resolved` 全部指向 `registry.npmjs.org`，防复发守门在 `scripts/tests/test_lock_registry.py`（外来 host 与 CI 安装步骤形态都会红）。`verify-frontend.sh` 内部的 `npm ci --allow-remote=all` 只是「本机 npm registry 配成镜像」时的兼容（npm ≥12 会把 lock 里的 npmjs tarball 视为 remote 而拒拉），不是标准安装命令，CI 不带该 flag。

## 3. 单元测试（Vitest，issue #253）

```bash
npm run test         # vitest run --coverage（全量，<10s；阈值不达标 exit 1）
npm run test:watch   # watch 模式（不收集覆盖率）
```

- 范围：**lib 层纯逻辑**（utils format 系 / tradePairs 结对 / allocation 聚合 / dimensions 维度 / validation 表单校验），node 环境，不引 jsdom/RTL——组件交互与运行时行为归 Playwright E2E（职责不重叠）。
- 约定：测试与源码 colocated（`src/lib/*.test.ts`），显式 `import { describe, it, expect } from "vitest"`（未开 globals）；alias `@` 在 `vitest.config.ts` 手动维护。
- 注意：`src/lib/api/` 是纯类型化 axios 薄封装（无数据转换逻辑），不在单测范围；新增 lib 纯函数应同步补测试。
- **覆盖率阈值（#464）**：分母圈定 `src/lib/**`（排除 `src/lib/api/**` 与测试自身），全局阈值（非 perFile）受 `vitest.config.ts` 的 `thresholds` 约束（口径与来源写在该文件注释，此处不复述数值），**只升不降**：全量实测超阈值 ≥1pp 时在当次 PR 顺手上调，**上调目标也按「实测取低 1pp」下取整**（后端 `fail_under` 自 #621 起同为此口径，两栈一致；后端侧的触发判据与余量理由见 `backend/AGENTS.md`「跑测试」节）。缺口已由补测三轮收回（#484 首轮、#501 死代码清理 + #503 零散分支、#507 `tradePairs` 异常 2 腿组回落 single），**当前残余为空**（#504 已落地：`quantizeAmount2` 非有限结果返回 `null`；`tradeAmounts` 的「到手无法量化」守卫经「毛额与手续费异号 + 量级越界」用例保持可达并已覆盖，未产生新残余）。
- **分母边界与已知缺口（#505 结论：维持现状、成文接受）**：`src/hooks/**` 与 `src/stores/**` **不在覆盖率分母内**，于是一行测试都没有也不会让任何门禁变红。这是记录在案、不当作 TODO 的缺口——它的存在方式是「E2E 兜底 + 人工评审」，不是「单测兜底」。两条扩张方案都按实测否决，**数据与推理只写在 `vitest.config.ts` 的「分母边界」注释**（一份正文一个持有者，改结论改那里）：并入 hooks 要把 99 的阈值整体重定；只并入 stores 在 node 下拿到的仍远低于阈值，而真正要验的那条契约（登录态持久化）在 node 下根本测不到。**重新评估的可判定触发条件**：① 出现任一「根因在 hooks 内的纯计算逻辑、且 E2E 定位不到具体分支」的线上缺陷；② 决定引入 jsdom/RTL（届时本条上方的「不引 jsdom/RTL」取舍与阈值口径须同批修订）。**动作约束**：`coverage.include` 是全局阈值与增量门禁共用的分母开关，改它必须同批改 `ci.yml` 的 `LCOV_SF_MIN` 与 `scripts/tests/test_ci_frontend_coverage.py` 的基准（#509）。
- **增量覆盖率门禁（#485）**：CI `frontend-check` 在 PR 事件对 `coverage/lcov.info` 跑 `diff-cover --compare-branch=<base.sha> --fail-under=<阈值>`（阈值以 `ci.yml` 的门禁步骤为单一来源；形态与 `backend-test` 的步骤同形），只约束**本 PR 在 `src/lib/**` 的改动行**；components/hooks/`lib/api`（有意排除）等不在分母内的路径无覆盖率数据、不算改动行，main 侧不拦（全局 `thresholds` 兜底）。两处口径要点：① **分母 = 改动行 ∩ 有覆盖率数据的行**（不是「本 PR 的全部改动行」，故 `lib/api` 与测试文件的改动不会被计量）；② 数据源是 `vitest.config.ts` 的 `lcovonly` reporter，其 `projectRoot: ".."` 相对 **cwd** 解析，须在 `frontend/` 下跑 `npm run test` 才会产出仓库根相对路径（否则 diff-cover 匹配不到改动行、门禁静默空转；CI 侧有分母棘轮与归因断言兜底，见下文「守卫自身的失效面」条）。**本地复现**：`cd frontend && npm run test` 产出 `coverage/lcov.info`，再从**仓库根**跑 `diff-cover frontend/coverage/lcov.info --compare-branch=origin/main`（加 `--fail-under=<与 ci.yml 一致的值>` 即完整判定；`diff-cover.md` 落在仓库根。本地无 diff-cover 时用 `uvx --from diff-cover diff-cover ...`）。
- CI 另产 `frontend/junit.xml`（PR 注解）、`frontend/coverage/` 与**仓库根** `diff-cover.md` + `diff-cover.json`（artifact + Step Summary 摘要；json 是归因断言的数据源，两者由同一步产出）。
- **守卫自身的失效面（#509）**：增量门禁的三段守卫各有独立的哑火形态，因此各有独立出口——① `Assert lcov data source (PR)` 里的**分母棘轮** `LCOV_SF_MIN`（收窄 `coverage.include` 会让覆盖率**变好**且门禁 exit 0，只有分母文件数看得见；改 include 必须同批改这个数字）；② `Warn on attribution gap (PR)` 用 `diff-cover.json` 的 `src_stats` 与「改动文件 ∩ lcov 有记录」做集合比对并**点名文件**，不再匹配 diff-cover 的 markdown 文案（升级改词即永久失配）也不再自己重算分母路径（目录改名时 `git diff` 不报错、added=0 会静默变哑）；③ `Append diff coverage summary` 在报告缺失时输出「未生成」而非静默 0 行。另有第 ④ 条：**守卫的档位本身要被钉住**——带 `if: always()` 的诊断步骤必须 `continue-on-error: true`（否则脚本自己崩会变成一个指向错误原因的第二个红），而唯一的分母守卫不得被 `continue-on-error` 卸成打印。四者的结构断言与反例集在 `scripts/tests/test_ci_frontend_coverage.py`（改判据必须连带改那里）。
- **vitest 系必须同版一起升**（#486）：`vitest` 与 `@vitest/coverage-v8` 在 `package.json` 里**都精确钉**同一版本（不带 `^`）。这不是风格选择而是上游事实——两者的 peer 互为精确锚（`@vitest/coverage-v8@X` 的 peer 是 `vitest: X`，反之亦然）；一精一 caret 时任何把 vitest 解析抬过钉版的操作（`npm update`、`npm i vitest@latest`、删 lock 重装）会撞 `ERESOLVE`，或 nest 出第二份 vitest，使**覆盖率数据与 CLI 版本分叉**（分母口径静默变一套、断言跑在另一套）。升版 = 两包同时改 + 先跑 `npm run test`（#402「工具链升级先跑其单测」的同源约束，此处是版本侧）。另注：`vite` 是 vitest 的对等依赖而本仓库**不声明**它（当前由 lock 解析到 8.2.2），升 vitest 时它会随 range 一起动，升完 `npm ls vite vitest @vitest/coverage-v8 --depth=0` 复看一次。

## 4. E2E（Playwright）

E2E 的运行入口与验收命令、本地栈前置检查、种子契约的 E2E 侧消费约束、导航/定位/浮层/日历/超时的反直觉结论，以及纯测试重构的归一化形态对比，全部在 [E2E 指南](e2e/AGENTS.md)。改 `frontend/e2e/**`、`playwright.config.ts`、`backend/tests/seed_base.py`、`backend/scripts/seed_e2e.py`、`scripts/e2e_normalize.py`、`scripts/tests/` 或做纯测试重构时读那份指南。

本文件仍持有前端通用约束：双端路由与 `basePath`（§1.1）、组件复用与确认类弹窗/读侧失败态（§1.2）、日志（§1.3）、质量门禁（§2）、Vitest 单测（§3）与目检（§5）。

## 5. 目检（视觉验证）

运行前置（依赖安装、端口独占、构建锁、产物目录）与 E2E 共用，见 [E2E 指南的运行前置检查](e2e/AGENTS.md#2-运行前置检查)。


```bash
../scripts/visual-verify.sh                                              # 双端截三个流水列表（默认 E2E_ACTIVE，恒有行）
../scripts/visual-verify.sh --device mobile --path /portfolio/E2E_PORT/positions   # 参数原样转给 visual-shot.mjs
```

- **第三条验证层**：§2 门禁（lint/tsc/build）看不到运行时，§3 单测只覆盖 lib 纯函数，E2E 断言定位与文本、**不断言像素**——列宽挤压、CJK 竖排换行、双行单元格错位、结对行 `colSpan` 对不齐这类问题只有人眼看图能拦（#355「市场」列窄到「A股场内」四字竖排是 issue 里人眼发现的，e2e 全程绿灯）。改 `components/shared/*Content.tsx` 的表格/图表列结构时按本节目检。
- `scripts/visual-verify.sh` 是 `local_stack.py visual` 的薄壳，负责独占栈与构建；也可从仓库根运行 `.venv/bin/python scripts/verify.py run visual -- <参数>` 取得结构化结果。截图参数见 `frontend/scripts/visual-shot.mjs`；托管入口的 `--out` 是新建运行目录的父目录，`--base` 由所选端口固定、不可覆盖。脚本的直接模式仍支持自选地址，但调用者自行承担资源归属。
- **视口与 E2E 同口径**：桌面 `Desktop Chrome` 1280×720、移动 `iPhone 13`（webkit），故 `--path` 只写桌面路径，靠 `src/proxy.ts` 按 UA 重定向到 `/m`（同 `portfolioPath` 的道理，不必写两条）。1280 是**保守值**——越窄越容易暴露挤压。每个 `--path` 出两张图：`*-<device>.png` 整页（fullPage）与 `*-<device>-table.png` 表格裁剪（放大读列布局）。
- **空表目检等于没目检**：优先使用恒有行的 `E2E_ACTIVE` 交易/申赎页；目检与 E2E 的库按运行隔离，不复用、不清理别的运行数据。份额变动事件在 `E2E_ACTIVE` 无种子行，需在本次独占库先造一条：`ex_date` 取晚于最新快照日的交易日、`entitlement_date` 取其前一交易日（先查 `snapshots` 与 `trading-calendar` 定日期），截图后删。**目检同样禁止对 `E2E_ACTIVE` 跑 recalculate/catch-up/generate-next**（红线正文见 [E2E 指南](e2e/AGENTS.md#1-e2eplaywright)）。
