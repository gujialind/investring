# 现金流水模块（方案 C）设计

> 状态：**PR-1（后端聚合查询，PR #726）与 PR-2（前端双端页面，PR #727 含 L2 评审处置）均已实现（2026-10-18），尚未合入 `main`**（issue #725）。PR-3（收尾）交付于 `feature/725-cash-flows-closeout`：本文 §4/§5 裁剪为短历史注记、§7 改为已执行的验证入口与结果、新增 §9 验收断言证据映射，并完成 H2 残留核对。**#726/#727 合入前不勾选 [issue #725](https://github.com/gujialind/investring/issues/725) 的任何验收断言**——回填与关闭是合入后动作。
> 本文归属：目标形态、契约、显示层口径与决策记录为**设计正文**，长期持有；§4、§5 原为**计划**，已按[迁移与去重](../reference/documentation.md#doc-migration)裁剪为短历史注记（只留结论、指针与回退路径结果，步骤清单删除）。临时实施计划不进 `docs/reference/`。
> 正文不复制：业务规则（平台分账、现金流水读侧口径、快照取价、量化）正文在 [business-constraints.md](../reference/business-constraints.md)，本模块的读侧口径归 [现金账本](../reference/business-constraints.md#rule-cash) 持有；视觉公共规范在 [visual-spec.md](visual-spec.md)。本文只引用、不持有。
> 屏上形态基线：双端 mockup v3 共 13 帧（原件在 `.cache/cash-flow-mockup/`，gitignore 不入库）。**mockup 的申赎方向标注与后端账本相反，不作基线**（见 §2 的方向口径与 §6 的 T1）；文字契约以 [issue #725](https://github.com/gujialind/investring/issues/725) 的 16 条验收断言 + 本文 + rule-cash 为准。

## 1. 背景与范围

- 聚合四类现金来源：调仓与申赎交易的 CASH 腿、平台间现金转移的两条 CASH 腿、平台级现金事件（`share_change_event.cash_change`，白名单见 `backend/app/constants/share_change_events.py::CASH_EFFECT_EVENT_TYPES`）、手动重估（`manual_market_value`，`product_code="CASH"`）。分红与重估不生成 CASH trade 行，此前在任何现金视角都读不到——这是本模块相对「调仓页按现金筛」的真实增量。
- 组合级子页（desktop + `/m/` 双端），入口挂 `ManageLinksCard` 第 6 项，`NAV_ITEMS` 不变（不触发 mobileTab 位宽与 mobileEntryHost 决策）。
- 验收断言 **16 条**（余额 3 + 转移 3 + 当日 2 + 在途重估 4 + 构成 3 + 导航 1）以 #725 正文为准，本文不复制原文（逐条证据映射见 §9）。早期草案写作「18 条」是计数错误。
- 与 PR #721 的关系与处理见 §4。

## 2. 目标形态（设计定稿）

- 页头四卡：可用现金 / 在途资金 / 本期流入 / 本期流出。默认组合级；平台筛选激活切该平台；筛选态的组合级由「按平台构成」弹窗合计行承担（下钻与上看同一弹窗）。
- 筛选栏：区间（上界默认开放「至今」，否则预计到账日在未来的在途行会被默认视图藏掉）/ 状态 / 平台 / 流水类型（7 类，见 §3）。
- 列表 7 列：`日期 · 平台` 组列（按 `(平台, 现金日期)` rowSpan 合并，内含「当日另有 N 笔」入口）/ 流水类型（chip + 日期含义副行）/ 来源 · 去向 / 金额 / 平台日终余额（与组列同段合并）/ 状态 / 操作。排序 = 现金日期降序 → 平台 code 升序 → 来源类型（`trade_leg` → `transfer` → `dividend` → `revalue`）→ 原始记录 id，因此同组行必然连续，合并段不会被分页以外的因素切断。
- 三弹窗：当日该平台全部流水（仅解除**类型**筛选——表格仍保留当前状态筛选，「全部」指含被类型筛选隐藏的行，「共 N 笔」计数与对账块用未过滤全组；标注命中行、关窗回原筛选）；流水详情（kv + 日终余额对账块 + 未到账行的投影块；对账块按**组级**式渲染，见 §3）；按平台构成（行值 = 筛该平台时四卡值、合计行 = 组合口径、行末「看明细」钻穿 / 「更新」以该平台预选启动更新现金市值、合计行无「更新」）。
- 页头入口：现金转移 / 转移记录 / 更新现金市值（三个弹窗均已是 `frontend/src/components/shared/dialogs/` 的共享组件，复用不新建）。
- **方向口径（账本视角，不可反向）**：申购的 CASH buy 腿是**流入**（投资人注资入账，label「申购 · 注资入账」）、赎回的 CASH sell 腿是**流出**（组合向投资人支付，label「赎回 · 支付」）。mockup v3 按「基金申赎 = 扣款/到账」的直觉标了相反符号，与其自身余额列（= `calculate_available_cash` 单次调用）不可能同时成立——对账块会不闭合。**PR-2 最终不修正 mockup 帧**（原件不入库，改动即破坏唯一的布局对照基线）：mockup 仅作布局参考，方向以本条与 §6 T1 为准。
- 显示层口径四条（余额列）：平台级（列名「平台日终余额」，**无组合级余额列**）；时点值 = `calculate_available_cash(as_of=该行现金日期, platform=该行平台)` 单次调用；日终值同 `(平台, 日)` 合并一格；与类型/状态/区间筛选构造上免疫。
- 状态列文案档位（后端 `status.label` 派生，前端不另算）：未生效行按类型给「待扣款 / 现金待到账 / 待支付 / 在途 / 待到账」；**申购注资腿按 `trade_status` 分档**——pending 是「待确认」、confirmed 但确认日 C 未到是「已确认 · 待入账」（T+1 确认的常态，一律标「待确认」会与已确认的申赎记录矛盾）；**重估行**已烘焙进快照是「已生效」（neutral），未烘焙是「待重算」（warning）+ note「需重算快照后生效」。
- 显示层口径三条（补充）：「当日另有 N 笔」N = 后端给的 `group_total_count` − 在屏同组行数，N=0 不渲染；投影值（「预计到账后余额」）只出现在详情弹窗、由前端推算并明写「不是 `calculate_available_cash` 的结果」，**后端不出该字段**；平台间转移按平台-日拆两行、配对靠详情弹窗的关联业务块维持。
- 移动端：卡片流不做行合并，每卡重复显示日终余额 + 平台名；「当日该平台另有 N 笔」放卡片操作行左侧；三个页头入口并排一行（补移动端 positions 页无转移入口的缺口）；分页简化为「共 N 条 + 第 x/y 页」。移动端**同样提供详情弹窗**（kv 卡片形态），断言 6/10 不豁免端。

## 3. 契约（后端聚合查询，PR-1 已实现）

- 端点：`GET /api/portfolios/{portfolio_code}/cash-flows`（**复数**）。既有 `GET /api/portfolios/{portfolio_code}/cash-flow`（单数，`routers/portfolios.py`，申赎汇总）口径不同、并存不改名（改名是破坏性变更，超出 #725 范围）。
- 参数：`from_date`、`to_date`（可空 = 开放上界）、`status`、`platform_code`（**空串等同未传**，service 入口归一为 None，否则行集按空串过滤成空而 `calculate_available_cash` 仍走组合级 → 矛盾响应）、`flow_type`、`page`、`page_size`（1-100）。非法值 → `INVALID_PARAM`；`from_date > to_date` → `INVALID_DATE_RANGE`；组合不存在 → `PORTFOLIO_NOT_FOUND`（复用既有码，未新增错误码）。
- 行 schema：见 `backend/app/schemas/cash_flow.py::CashFlowRow`（字段清单是生成契约 `backend/openapi.json` 的事实来源，本文不复制）。设计要点：
  - `source_kind ∈ {trade_leg, transfer, dividend, revalue}`：申赎属 `trade_leg`（由 `flow_type` 区分），不另立 source_kind；平台间转移虽也是 CASH 腿，但两腿互指、需要独立的 pair 语义，故单列。
  - `flow_type ∈ {rebal_buy, rebal_sell, sub_buy, redeem, dividend, transfer, revalue}`（7 类 = 筛选下拉值域）。`forced_adjustment` 归 `dividend` 值域、`flow_type_label` 显示「现金调整」——不为一类事件另立筛选值（§6 T5）。
  - `cash_date` 是排序/分组/余额查询的唯一权威日期，锚点规则表见 [rule-cash](../reference/business-constraints.md#rule-cash)（卖出锚 `trade_date`、买入锚 `confirm_date`、事件锚有效现金日、重估锚 `value_date`）；`cash_date_label` / `cash_date_sub` 给日期含义副行（扣款日 T / 到账日 A · 预计 / 确认日 C / 到账日 cash_pay_date / 除息日 ex_date / 转移日 / 覆盖日）。
  - `platform_end_of_day_balance` + `prev_day_balance`：后者是详情弹窗对账块首行「{前日} 日终（或期初）」的数据来源——只有当日日终不足以让对账块闭合（§6 C3）。**对账式是组级**：前日日终 + 同日同平台**全部已生效流量** = 日终；重估日以覆盖值替换该式、不参与求和。「前日 + 本笔 = 日终」只在当日该平台恰好一笔且非重估日时成立，PR-2 的弹窗文案与 e2e 断言不得按单笔式写。后端按 `(平台, 现金日期)` **去重后**批量取价；前一交易日走 `try_get_prev_trading_day`，日历覆盖不足时该值为空而不是让整个只读列表失败。rowSpan 合并是显示层行为，后端不合并。
  - `group_total_count`：解除类型筛选、保留区间/平台/状态的同组总笔数（§6 C5）。
  - 金额一律 `Decimal` 4 位原值直出（JSON 字符串），后端不量化（§6 T3）。
- 分页与计数：按现金变动笔数切页与计数（一笔平台间转移 = 两笔，与各平台账本同构）。
- 概览与构成同响应携带：`overview {available, in_transit, inflow, outflow}` 与 `platform_breakdown {rows, total, recon}`（rows = 各平台口径、total = 组合口径、recon = 行和与合计之差 = 本期已生效转移腿净额）。流量卡跟随全部筛选并对**筛选后全集**聚合（不是当页）；存量卡只随平台筛选切 scope。单响应减少往返；若评审认为职责过重再拆 summary 端点（PR-1 未拆）。
- 在途：unsettled 行不进余额链的流量与流入/流出，只进 `in_transit`，按到账/扣款平台归属；重估行对 `in_transit` 的**金额**贡献恒为 0（绝对值不是增量）。未烘焙进快照的重估行 `settled=False`（判据见 [rule-cash](../reference/business-constraints.md#rule-cash)），故 `status=in_transit` 会选中它而存量卡金额不含它——筛选档与存量卡在重估行上刻意不同源。口径正文与「不等于快照 `IN_TRANSIT_*`」的边界见 [rule-cash](../reference/business-constraints.md#rule-cash)。
- 投影值不进后端（§6 T7）；ir-cli 命令组不进 PR-1，follow-up chore 单独加（手册与契约不随本 PR 一起膨胀）。

## 4. PR #721 的处理（历史注记）

结论：**主路径已执行，回退路径未触发**。#721（分支 `feature/719-cash-leg-pairing`，PR 仍 OPEN）在 rebase 解冲突的窗口内把 H2 整体撤出本 PR，只保留 H1——**H2 从未进入 `main`**，因此 PR-3 无撤除动作，只做残留核对。

- **H1 配对子行（保留，随 #721 交付）**：双向 `build_paired_leg_map` + 嵌套 `paired_leg` 响应字段 + 前端合成对手腿子行，回答「这笔买入花哪个平台的钱 / 卖出的钱去哪」，与本页互补。
- **H2 产品筛选含现金（退役）**：目的（让平台间转移可筛）被本模块完全承接——转移按平台-日拆两行、带余额与配对详情，口径更全；「调仓页产品筛选不含现金」是方案 C 的既定形态。原「禁止在调仓页产品筛选加现金」条款与 4 步操作清单随主路径执行退役（执行完毕、窗口已过，不是文档整理中静默删除保护禁令）。
- **残留核对（PR-3 执行）**：`origin/main` 与 `origin/feature/725-cash-flows-ui` 上 H2 三个符号**零残留**。两分支在全部 H2 相关文件上唯一 diff 是 visual-spec 的 #725 登记；现存命中项均为 #721 之前的既有基线、**不是 H2 语义也不是撤除对象**——`include_virtual` 只在 `/api/products` 域（#327 虚拟产品默认排除，产品管理页显式取数），`ProductFilterDialog` 的 `{ value: "CASH", label: "现金" }` 是 #324 起的**产品类型**下拉（调仓页取数不传 `include_virtual`，故现金产品筛不出、CASH 档选不中），`products=CASH|` 是 #155 通用 `code|market` 复合值解析的文档示例。命令与逐项判定在 PR-3 收尾 PR 正文留痕。
- #719 的验收回填与关闭留待 #721 合入后执行，本模块不代做。

## 5. 实施切分（历史注记）

三个 PR 均已交付、**均未合入 `main`**，stacked 关系：#726（base `main`）← #727（base `feature/725-cash-flows-api`）← PR-3（base `feature/725-cash-flows-ui`）。原步骤清单已删除，产物以下列 commit 指针为准。

- **PR-1 后端聚合查询 —— PR #726**（`feature/725-cash-flows-api`：`02dda7e` 主体、`e448ab5` 评审处置）：`cash_flow_service.py` + `schemas/cash_flow.py` + `routers/cash_flows.py` + openapi 再生成 + 单测与两份集成测试；读侧口径正文补入 [rule-cash](../reference/business-constraints.md#rule-cash)；本文与 `frontend/AGENTS.md` 入口句同批入库、`backend/AGENTS.md` 影响面表补行。ir-cli 命令组未进本 PR（follow-up chore）。
- **PR-2 前端双端页面 —— PR #727**（`feature/725-cash-flows-ui`：`39610cc` 主体、`05fa6e1` 评审处置、`76a8d98` 同步 PR-1、`043f4cc` coverage 分母棘轮随 #725 新增 lib 文件上调、`6797fee` 日期·平台列与格内日期折行修复、tip `0b6d098` 合并单元格垂直居中 + 余额列改名「当日余额」）：双端页面 + 7 列 rowSpan 列表 + 三弹窗 + `ManageLinksCard` 第 6 项；形态登记见 [visual-spec.md](visual-spec.md) §8（多行合并段）、§12（符号三档例外）、§20（两行变更记录）；方向按 §2 账本口径落地（消费后端 `flow_type_label`，前端不重算）；`e2e/cash-flows.spec.ts` 双端。mockup 帧的申赎相反标注未修正——mockup 仅作布局参考，方向以本文为准（原件不入库）。**复拍缺陷与修复（#727）**：桌面「日期 · 平台」表头与格内日期在 1280/1024 视口下各折两行（第一列实得 92px，日期按连字符断行），已补 `whitespace-nowrap`；mockup 列表帧无侧栏、表宽 1148px，桌面真实容器仅 910px，列表帧只可对照列结构与主次行，不可当宽度基线。
- **PR-3 收尾 —— `feature/725-cash-flows-closeout`**：§4/§5 裁剪、§7 改写为实测结果、新增 §9 断言证据映射、H2 残留核对（§4）、文档门禁。

原「#721 裁剪须先于 PR-2 合入」的顺序依赖随 #721 走主路径消解（§4）；现金流水页不消费 `paired_leg`，与 #721 无字段耦合，合入顺序不再受限。**合入后**须按 §9 逐条回填 #725 的 16 条断言，并处理 §6 列出的未裁决项。

## 6. 裁决项与决策记录

已由维护者在 PR-1 前裁决（T 系列）与实现期契约调整（C 系列）：

- **T1 申赎方向按后端账本口径**：申购 = 流入(+)、赎回 = 流出(−)，mockup 的相反标注作废（§2）。
- **T2 端点用复数 `/cash-flows`**，旧单数端点不动。
- **T3 金额 4 位原值直出、后端不量化**（读侧不量化，量化职责见 [量化产生点清单](../reference/business-constraints.md#量化产生点清单)）。
- **T4 `status` 只映射 `settled` / `in_transit` 两值**，不透出 raw `trade.status`（confirmed + 未来到账日在余额语义上仍是在途）；cancelled 腿排除。
- **T5 `forced_adjustment` 归 `flow_type=dividend`**、label 按 `event_type` 显示「现金分红 / 现金调整」，不另立筛选值域。
- **T7 不做 `include_pending` 投影**：投影只在详情弹窗、由前端推算（对应 §3 的 `prev_day_balance` 之外的推断值）。
- **C3 契约补 `prev_day_balance`**：只有当日日终无法支撑详情弹窗的对账块首行（原草案缺口）。
- **C5 契约补 `group_total_count`**：类型筛选态下前端无从得知同组总笔数。
- **C6/C7**：两级流入/流出排除规则与「按 `(平台, 现金日)` 去重批量取价」写进契约与 rule-cash 正文。
- 同日重估与增量排序：引擎口径 = 重估吸收当日增量。**PR-2 实现与原「显示默认不显式标注」的偏离（显式注记）**：详情/当日弹窗的对账块在同日存在已生效重估时渲染一行注记「同日现金重估（绝对值覆盖）吸收当日增量，日终以重估值为准」——对账块逐笔列出的是「当日已生效流量」，重估行不参与求和、日终直接取覆盖值，无注记时该式在重估日不闭合、会被读成后端算错；列表行本身仍不额外标注（状态列 + 金额档已表意）。
- 现金产品详情页：保留现状（单产品语境 vs 全来源语境不同），本期不退场。
- 页面命名：「现金流水」为暂名，PR-2 前由维护者拍板（PR-2 按「现金流水」落地，未另拍板即视为定名）。

PR-2 提出、**至今未裁决**的两项（本模块不代裁决，只登记事实）：

- **投影块触发面与 T7 字面的口径差**：T7 定「预计到账后余额 = 当前日终 + 本笔，只在详情弹窗」，其数值模型来自 mockup——mockup 的余额只累加已生效流水，故对任何未生效行都「不含本笔」。但 PR-1 契约的 `platform_end_of_day_balance = calculate_available_cash(as_of=cash_date)` 对 **confirmed 但到账日在未来**的行已把本笔计入，再投影会重复计。PR-2 的前端-only 处置：`projectedBalance` 收窄为**仅 pending 转入腿**（后端 `action.kind="confirm"`）返回非 null，并给这类行在「确认到账」按钮旁并列「详情」link 使其可达。若要 confirmed-未来行也给「预计到账」提示，需 PR-1 侧补字段或改 `platform_end_of_day_balance` 语义——属后端契约变更，未做。
- **移动端详情弹窗**：断言 6/10 未豁免端，PR-2 按建议**已提供**（kv 卡片形态，与桌面共用 `CashFlowsContent`）。若维护者改为豁免，回填 #725 时须在断言 6/10 注明「移动端由当日/构成弹窗部分承载」。

## 7. 已执行的验证入口与结果（PR-1 / PR-2，2026-10-18）

数字摘自 #726 / #727 正文与处置评论的实跑留痕；「本模块测试」行的 68 / 135 为 PR-3 在 closeout 分支（`9dda5fb` merge `043f4cc` 后）复跑，复跑范围见该行。本节其余不重跑、不换算。缺口见 §9 表末。

| 入口 | 命令要点 | 结果 |
| --- | --- | --- |
| 影响面圈定 | `.venv/bin/python scripts/verify.py plan --base origin/main` | PR-1 required jobs = changes / docker-build-smoke / image-smoke / backend-test / backend-test-mysql / cli-contract-check / frontend-check / frontend-e2e |
| 后端全量（SQLite） | `TEST_DB_URL=sqlite:////tmp/ir725/sqlite.db … pytest tests -q` | 2206 passed / 38 skipped |
| 后端全量（MySQL 方言） | 本机 `mysql:8.4` 容器映射 3307 的空库 `ir_test`，显式经 `TEST_DB_URL` 指定 | 2242 passed / 2 skipped。**未使用** `backend/.env.test`（指向远程实例，禁用于测试通道；隔离闸门 `backend/tests/db_isolation.py`）。仓库内**没有**预置的 3307 定义，`docker-compose.dev.yml` 是 `mysql:8.0` 映射 3306 / 库 `investring_dev` |
| 本模块测试 | PR-1 新增三测试文件单独跑（`pytest tests/unit/test_cash_flow_service.py tests/integration/test_cash_flows_balance.py tests/integration/test_cash_flows_api.py -q`）；`backend/AGENTS.md` 新登记行选择口径 `-k "cash_flow or cash_transfers or in_transit"` | **最终口径：62 个 test 函数、参数化展开后收集 68 条，单独跑 68 passed（SQLite）。**选择口径 PR-3 复跑（head 含 `043f4cc`）：SQLite 135 passed / 2116 deselected；MySQL 通道（本机 `mysql:8.4` 空库 `ir_test`）135 passed / 2116 deselected。旧留痕 61 / 128 是 `02dda7e` 快照（55 函数 + 7 参数化），`e448ab5` 评审处置 +7 后为现值；1211 deselected 对应一个未复现的更窄收集范围（现全量 `tests` 收集 2251），pass 数 135 与 #728 审查者复测一致 |
| 契约 | `backend/check_openapi.py`、`scripts/verify.py run contract`、`ir-cli/scripts/gen_response_fields.py --check` | openapi 一致、contract pass、`response_fields.py` 一致且无 diff（CLI 命令组未进 PR-1） |
| 前端门禁 | `scripts/verify-frontend.sh`；PR-2 后 `npm run lint` / `tsc --noEmit` / `npm run test` / `npm run build` | 0 error / 0 / 207 passed / build OK，桌面与移动两条路由均注册；`cashFlows.ts` 分支全覆盖 |
| 前端 E2E | `verify.py run e2e -- … e2e/cash-flows.spec.ts e2e/regression.spec.ts --workers 2 --retries 0`（隔离栈） | `05fa6e1` 处置后 `status: pass`、47 passed，双 project，运行前后源码指纹一致 |
| 双端目检 | `scripts/visual-verify.sh` desktop + mobile，对照 `.cache/cash-flow-mockup/v2-*` 列表帧 | 一致；过程中发现移动 2 列栅格下带符号大额溢出压邻卡，四卡数值降为 `text-xl` 后复拍确认。mockup 的申赎方向标注按 §6 T1 不作基线 |
| 文档门禁 | `python scripts/check_context_docs.py`、`python -m pytest scripts/tests/test_context_docs.py scripts/tests/test_ci_path_mapping.py -q` | PR-1 批：Context docs OK（11 documents / 8 source-reference files）、116 passed。PR-3 批见收尾 PR 正文 |

## 8. 风险与回滚（现状标注）

- **变更全为读侧新增**（新端点、新页面、新组件；无迁移、**无后端写入口径改动**——PR-2 同批改了共享的 `useCashTransfer`/`usePosition` 失效逻辑与 `CashTransferDialog` 的 id/htmlFor，但不触碰写入路径）：回滚顺序为 **PR-2 → PR-1**——PR-2 页面调用 PR-1 的 `/cash-flows` 端点，PR-1 不能在 PR-2 仍在时单独回滚，否则页面请求不存在的接口。PR-2 回滚会整体移除现金流水页、组件、hooks 与入口（`ManageLinksCard` 第 6 项随之消失），不是「仅入口消失」。两者回滚均不涉及数据恢复。H2 撤除已不再是风险项——#721 走主路径，H2 未进 `main`（§4）。
- **余额列性能**（仍有效）：PR-1 已按 `(平台, 日)` 去重批量取价，且只对当页行补齐展示信息与余额。截至 PR-2 交付**未触发**退路判据（无单页 >1s 的实测证据），维持引擎直调。若真实数据量下变慢，改为快照基线批量读（口径不变、只改读取模式），回写本文 §3。
- **重估生效依赖快照重算**（仍有效）：`calculate_available_cash` 只经快照基线读到覆盖，未重算时日终余额≠重估值。前端以状态档位「待重算」+ note 兜底（§2），不改引擎；运营上依赖「重估 → 重算」流程。
- **#721 联动**：#721 仍 OPEN，main 上暂无 `paired_leg`；现金流水页不消费该字段，两者可任意顺序合入。若 #721 后续再触碰 `TradesContent.tsx` / `ProductFilterDialog.tsx`，与本模块无文件冲突（本模块不改这两处）。

## 9. 16 条验收断言 → 证据（PR-3 建立）

断言原文与顺序以 [issue #725](https://github.com/gujialind/investring/issues/725) 正文的 16 条为准（余额 3 + 转移 3 + 当日 2 + 在途重估 4 + 构成 3 + 导航 1），本节不复制原文、只给落点与用例名。**状态列一律是「已实现，待合入后勾选」**：#726 / #727 尚未合入 `main`，勾选动作属合入后回填，本节不构成任何勾选。

| # | 断言要点 | 落点（PR + 文件） | 测试 / e2e 用例 | 状态 |
| --- | --- | --- | --- | --- |
| 1 | 行余额 = `calculate_available_cash(as_of=现金日期, platform)` 单次调用；组合级不入该列 | PR-1 `backend/tests/integration/test_cash_flows_balance.py`、`tests/unit/test_cash_flow_service.py`、`tests/integration/test_cash_flows_api.py` | `TestBalanceEqualsEngine::test_every_row_balance_is_a_single_engine_call`；`TestBalancePricing::test_balance_lookup_deduped_and_always_platform_scoped`；契约面 `TestResponseContract::test_row_schema_has_no_portfolio_level_or_projection_field` + `TestAccessAndEnvelope::test_row_payload_has_no_portfolio_balance_or_projection` | 已实现，待合入后勾选 |
| 2 | 同 `(平台, 现金日期)` 多笔合并一格（日终口径） | PR-1 同上；PR-2 `frontend/src/lib/cashFlows.test.ts`、`frontend/e2e/cash-flows.spec.ts` | `test_rows_in_same_group_share_both_balances`；`cashFlows.test.ts::「连续同 (平台,日期) 合并，被覆盖行标 -1」`；e2e「概览四卡 · 同日合并 …」桌面段（`group-cell`/`balance-cell` 各 1 格且 `rowspan=2`）与移动段（每卡重复余额） | 已实现，待合入后勾选 |
| 3 | 类型/状态筛选下余额值不变 | PR-1 `test_cash_flows_balance.py`、`test_cash_flow_service.py` | `TestBalanceEqualsEngine::test_filters_do_not_change_balance_values`；`TestOverviewScope::test_in_transit_and_available_ignore_type_and_status_filters`（存量卡面） | 已实现，待合入后勾选 |
| 4 | 一笔转移 = 两行、来源/去向互指、分页计数 +2 | PR-1 `test_cash_flows_api.py`、`test_cash_flow_service.py` | `TestTransferRows::test_two_rows_per_transfer_and_total_counts_both`、`test_source_main_points_at_the_other_platform`；unit `TestDerivationRules::test_transfer_splits_into_two_rows_pointing_at_each_other` | 已实现，待合入后勾选 |
| 5 | 组合级 in/out 不含转移，平台级含对应腿 | PR-1 `test_cash_flows_api.py`、`test_cash_flow_service.py` | `TestOverviewAndBreakdown::test_portfolio_level_excludes_transfers_platform_level_includes`；unit `test_portfolio_level_excludes_transfer_and_revalue`、`test_platform_level_includes_transfer_legs` | 已实现，待合入后勾选 |
| 6 | 详情弹窗显示对方腿（平台 · 日期 · 金额 · 状态） | PR-1 `test_cash_flows_api.py`；PR-2 `CashFlowsContent.tsx` + `e2e/cash-flows.spec.ts` | `TestTransferRows::test_cross_day_pair_exposes_settled_split`（`pair_ref` 四要素）；e2e 同一用例的 `#6` 段（详情弹窗「关联业务」可见）。移动端同供详情弹窗，见 §6 未裁决项 2 | 已实现，待合入后勾选 |
| 7 | 「当日另有 N 笔」N = 同平台-日总笔数 − 在屏行数；N=0 不渲染 | PR-1 unit + api；PR-2 `cashFlows.test.ts` + e2e | unit `TestGroupCount::test_group_count_ignores_type_filter`、`test_group_count_follows_status_filter`；api `TestFlowTypeFilter::test_group_total_count_releases_type_filter`；`cashFlows.test.ts::sameDayOthersCount`（含 clamp 与缺位兜底 4 条）；e2e `#7`（筛「调仓 · 买入扣款」后得「当日另有 1 笔」） | 已实现，待合入后勾选 |
| 8 | 当日弹窗：仅解除类型筛选、标注命中行、关窗回原筛选（「全部」口径与状态筛选态覆盖见 §2 与表末缺口） | PR-2 `e2e/cash-flows.spec.ts`（后端同组取数口径由 PR-1 `group_total_count` 支撑） | e2e `#8`：断言「当日该平台共 2 笔（含被当前筛选隐藏的行）」+ 被隐藏的申购行在弹窗内可见 + 关闭后 `same-day-link` 仍在（筛选未丢） | 已实现，待合入后勾选 |
| 9 | 未到账行不入余额与 in/out、只进在途（按到账平台）；余额格照常、金额注「不计入余额」 | PR-1 `test_cash_flows_balance.py`；PR-2 `cashFlows.test.ts` + e2e | `TestInTransitAndOverride::test_unsettled_row_keeps_balance_but_stays_out_of_flows`；`cashFlows.test.ts::amountNote「未生效 → 不计入余额」`；e2e「在途转移腿 …」的 `#9` 段 | 已实现，待合入后勾选 |
| 10 | 详情弹窗「预计到账后余额」= 当前日终 + 本笔，明写前端推算 | PR-2 `cashFlows.ts` + `cashFlows.test.ts` + e2e；PR-1 保证后端不出该字段 | `cashFlows.test.ts::projectedBalance`（5 条，含 confirmed-未来行不投影）；e2e `#10`（投影块 + 文案含 `calculate_available_cash`）；unit/api 的无投影字段契约断言。**触发面按 §6 未裁决项 1 收窄至 pending 转入腿** | 已实现，待合入后勾选 |
| 11 | 重估行出现（金额注为绝对值）；同日增量被吸收，日终 = 重估值 | PR-1 `test_cash_flows_balance.py`、api、unit；PR-2 `CashFlowsContent.tsx` | `TestInTransitAndOverride::test_revalue_row_is_absorbed_by_snapshot`；`TestGroupReconciliation::test_revalue_day_replaces_instead_of_summing`；`TestRevalueBaking::test_unbaked_override_is_unsettled_and_leaves_available_alone` + `test_generating_the_snapshot_flips_settled_to_true`；api `TestFlowTypeFilter::test_revalue_row_is_absolute_and_actionable`；对账块「同日现金重估 … 吸收当日增量」注记见 §6 | 已实现，待合入后勾选 |
| 12 | `share_change_event.cash_change` 出分红行、可按类型筛出 | PR-1 api + unit | api `TestFlowTypeFilter::test_dividend_domain_includes_forced_adjustment`（基金级父记录与 pending 事件不出行，避免双计）；unit `test_dividend_rows_use_cash_effective_date_and_raw_sign`、`test_forced_adjustment_shares_flow_type_but_keeps_own_label` | 已实现，待合入后勾选 |
| 13 | 构成弹窗每平台四值 = 筛该平台时四卡值；合计行 = 组合口径 | PR-1 api + unit；PR-2 e2e | api `TestOverviewAndBreakdown::test_breakdown_rows_equal_scoped_overview_and_total_equals_portfolio`；unit `test_breakdown_rows_ignore_platform_filter`；e2e `#13/#14` 段（`breakdown-row` 与合计行渲染） | 已实现，待合入后勾选 |
| 14 | 「看明细」钻穿并关窗；「更新」以该平台预选启动更新现金市值；合计行无「更新」 | PR-2 `e2e/cash-flows.spec.ts` | e2e 同一用例的 `#14` 段：`breakdown-drill` 关窗生效、`breakdown-update` 打开「更新现金市值」且平台触发器已预选「华宝证券」、合计行 `breakdown-update` 计数为 0 | 已实现，待合入后勾选 |
| 15 | 行和 ≠ 合计的差由表下注记命名（= 本期已生效转移腿） | PR-1 unit + api；PR-2 `CashFlowsContent.tsx` | unit `TestOverviewScope::test_breakdown_recon_equals_settled_transfer_legs`；api `TestOverviewAndBreakdown::test_recon_equals_settled_transfer_legs`；注记文案实现于构成弹窗表下（「差 = 本期已生效转移…」） | 已实现，待合入后勾选 |
| 16 | `ManageLinksCard` 第 6 项进入本页；`NAV_ITEMS` 不变 | PR-2 `ManageLinksCard.tsx` + `e2e/cash-flows.spec.ts` | e2e「组合详情「现金流水」入口进入本页」（双端 project）；`navItems.ts` 相对 `origin/main` **零 diff**（PR-3 核对，见 §4/PR 正文） | 已实现，待合入后勾选 |

### 表末缺口（勾选时须一并核对，勿当作已覆盖）

- **断言 15**：`recon` 的**数值**有后端单测与集成测试，注记**文案**只在实现里（`CashFlowsContent.tsx`），e2e 未对该句断言。
- **断言 8 / 14**：纯前端交互面，无后端对应用例；回归仅由 e2e 承担（弹窗帧不在 `visual-verify.sh` 能力内——`visual-shot.mjs` 只按路径截图、不开弹窗，故 13 帧中 6 张弹窗帧无像素基线，形态由 e2e 断言替代）。
- **断言 2 / 14 的移动端覆盖**：e2e 移动段对同日合并组只断言「申购 · 注资入账」一张卡的重复余额，「调仓 · 买入扣款」卡仅 `toBeVisible`（`cash-flows.spec.ts:217-224`）；「合计行无 `breakdown-update`」只在桌面段断言（`if (!isMobile)` 内，:270-276），移动端合计行无操作未受守。实现为逐卡渲染余额、合计卡不传 `onUpdate`，故当前成立；若移动端退化，这两条证据不会变红。补第二张卡余额与移动端合计行断言属 **PR-2 后续**（e2e 改动在 PR-2 分支，超出本 PR 只改文档的范围）。
- **断言 8 的状态筛选态**：实现是「当日弹窗表格保留当前状态筛选、仅计数与对账块用未过滤全组」（§2 口径；`CashFlowsContent.tsx:363-366` 的 `dayRows` 按 `statusFilter` 过滤），e2e `#8` 只构造了类型筛选，状态筛选态（如在途档下开弹窗）**未由 e2e 覆盖**。
- **断言 10**：结论依赖 §6 未裁决项 1（投影触发面收窄）。裁决若改为「confirmed-未来行也提示预计到账」，需后端补字段并同批改 `projectedBalance` 用例与 e2e。
- **断言 6 / 10 的移动端**：PR-2 已按 §6 未裁决项 2 提供移动端详情弹窗；若改为豁免，勾选时须注明「移动端由当日/构成弹窗部分承载」。
- **断言 11 的运营前提**：重估吸收依赖「重估 → 重算快照」流程，测试用例主动生成快照（`TestRevalueBaking`）；真实环境未重算时日终余额不等于重估值，由状态档「待重算」注记兜底（§8）。
