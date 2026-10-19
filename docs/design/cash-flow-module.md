# 现金流水模块（方案 C）设计

> 状态：**PR-1（后端聚合查询）已于 2026-10-18 落地**（issue #725）；PR-2（前端双端页面）与 PR-3（收尾）待实施，见 §5。
> 本文归属：目标形态、契约、显示层口径与决策记录为**设计正文**，长期持有；§4、§5 为**计划**，PR-2/PR-3 落地后裁剪为短历史注记（见 [documentation.md](../reference/documentation.md) 的[迁移与去重](../reference/documentation.md#doc-migration)节）。临时实施计划不进 `docs/reference/`。
> 正文不复制：业务规则（平台分账、现金流水读侧口径、快照取价、量化）正文在 [business-constraints.md](../reference/business-constraints.md)，本模块的读侧口径归 [现金账本](../reference/business-constraints.md#rule-cash) 持有；视觉公共规范在 [visual-spec.md](visual-spec.md)。本文只引用、不持有。
> 屏上形态基线：双端 mockup v3 共 13 帧（原件在 `.cache/cash-flow-mockup/`，gitignore 不入库）。**mockup 的申赎方向标注与后端账本相反，不作基线**（见 §2 的方向口径与 §6 的 T1）；文字契约以 [issue #725](https://github.com/gujialind/investring/issues/725) 的 16 条验收断言 + 本文 + rule-cash 为准。

## 1. 背景与范围

- 聚合四类现金来源：调仓与申赎交易的 CASH 腿、平台间现金转移的两条 CASH 腿、平台级现金事件（`share_change_event.cash_change`，白名单见 `backend/app/constants/share_change_events.py::CASH_EFFECT_EVENT_TYPES`）、手动重估（`manual_market_value`，`product_code="CASH"`）。分红与重估不生成 CASH trade 行，此前在任何现金视角都读不到——这是本模块相对「调仓页按现金筛」的真实增量。
- 组合级子页（desktop + `/m/` 双端），入口挂 `ManageLinksCard` 第 6 项，`NAV_ITEMS` 不变（不触发 mobileTab 位宽与 mobileEntryHost 决策）。
- 验收断言 **16 条**（余额 3 + 转移 3 + 当日 2 + 在途重估 4 + 构成 3 + 导航 1）以 #725 正文为准，本文不复制。早期草案写作「18 条」是计数错误。
- 与 PR #721 的关系与处理见 §4。

## 2. 目标形态（设计定稿）

- 页头四卡：可用现金 / 在途资金 / 本期流入 / 本期流出。默认组合级；平台筛选激活切该平台；筛选态的组合级由「按平台构成」弹窗合计行承担（下钻与上看同一弹窗）。
- 筛选栏：区间（上界默认开放「至今」，否则预计到账日在未来的在途行会被默认视图藏掉）/ 状态 / 平台 / 流水类型（7 类，见 §3）。
- 列表 7 列：`日期 · 平台` 组列（按 `(平台, 现金日期)` rowSpan 合并，内含「当日另有 N 笔」入口）/ 流水类型（chip + 日期含义副行）/ 来源 · 去向 / 金额 / 平台日终余额（与组列同段合并）/ 状态 / 操作。排序 = 现金日期降序 → 平台 code 升序 → 来源类型（`trade_leg` → `transfer` → `dividend` → `revalue`）→ 原始记录 id，因此同组行必然连续，合并段不会被分页以外的因素切断。
- 三弹窗：当日该平台全部流水（仅解除类型筛选、标注命中行、关窗回原筛选）；流水详情（kv + 日终余额对账块 + 未到账行的投影块；对账块按**组级**式渲染，见 §3）；按平台构成（行值 = 筛该平台时四卡值、合计行 = 组合口径、行末「看明细」钻穿 / 「更新」以该平台预选启动更新现金市值、合计行无「更新」）。
- 页头入口：现金转移 / 转移记录 / 更新现金市值（三个弹窗均已是 `frontend/src/components/shared/dialogs/` 的共享组件，复用不新建）。
- **方向口径（账本视角，不可反向）**：申购的 CASH buy 腿是**流入**（投资人注资入账，label「申购 · 注资入账」）、赎回的 CASH sell 腿是**流出**（组合向投资人支付，label「赎回 · 支付」）。mockup v3 按「基金申赎 = 扣款/到账」的直觉标了相反符号，与其自身余额列（= `calculate_available_cash` 单次调用）不可能同时成立——对账块会不闭合。PR-2 落地时按本条修正 mockup 帧。
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

## 4. PR #721 处理建议

现状（2026-10-18 核实）：#721 **OPEN**、CI 全绿、无审查结论、分支 `feature/719-cash-leg-pairing` 与 main **有冲突**（mergeable=CONFLICTING）。即：无论去留，#721 都要先过一次 rebase/解冲突。

#721 含两个可分离的半份：

- **H1 配对子行（只补信息不补行）**：后端双向 `build_paired_leg_map` 派生字段与嵌套 `paired_leg` 响应字段（`trade_service.py` / `schemas/trade.py` / `routers/trades.py` / openapi、ir-cli response_fields）、前端 `lib/tradePairs.ts` 及测试、`TradesContent.tsx` 与三个详情内容组件的子行渲染、visual-spec §8 派生结对子行条款、`e2e/trade-cash-pairing.spec.ts`。
- **H2 产品筛选含现金**：**不是**新增 `ProductFilterDialog` 的 CASH 选项（该选项 `{ value: "CASH", label: "现金" }` 已在 main）；实际新增的是快捷下拉的现金档 + `include_virtual=true` 取数行为 + 空 `market` 段放行，以及 `list_trades` 的 products=CASH 取数分支与相应 e2e 断言。visual-spec **没有**「§9 现金项条款」——§9 是筛选栏规范；裁剪时按 PR diff 的实际 hunk 执行，不按早期草案的字面描述。

方案 C 下的去留判断：H1 在交易语境里回答「这笔买入花哪个平台的钱 / 卖出的钱去哪」，与现金模块互补，**保留**；H2 的目的（让平台间转移可筛）被 #725 模块完全承接（转移在那里按拆分行出现、口径更全），且 C 的既定形态是「调仓页产品筛选不含现金」，**退役**。

**建议（主路径）：借解冲突的 rebase 窗口把 #721 裁剪为仅 H1，H2 不入 main。**

1. rebase `feature/719-cash-leg-pairing` 到最新 `origin/main`（冲突本就要解）。
2. 同批移除 H2：快捷下拉现金档与 `include_virtual` 取数分支、`list_trades` 的 products=CASH 分支与 schema 参数、e2e 现金筛选断言、ir-cli 手册/response_fields 对应条目（如有）。
3. 改标题只反映「筛选后补配对子行」；合入后回填 #719 正文：断言 1 勾选，断言 2/3 改为「由 #725 承接」并写原因（[AGENTS.md](../../AGENTS.md) §3.2），关闭 #719。
4. 收益：H2 代码不进 main，避免「先合再撤」的二次 churn；main 上不出现已决定要退役的双现金入口窗口期。

**回退路径**：若 H1/H2 在 `trade_service.py` / openapi 的 hunk 交织、拆分成本超过半日评审量，则 #721 整体 rebase 后合入，H2 在收尾 PR（PR-3）撤除；visual-spec §20 变更记录注明撤除理由，#719 同批回填。

**禁止**：长期挂起 #721 不管而在其冲突分支之上做 C 的前端 PR——`TradesContent.tsx` / `ProductFilterDialog.tsx` 同文件冲突面会被放大到两个 PR 互相阻塞。

## 5. 实施切分（计划）

**PR-1 后端聚合查询 —— 已完成（2026-10-18）**

- `backend/app/services/cash_flow_service.py` + `backend/app/schemas/cash_flow.py` + `backend/app/routers/cash_flows.py`（挂 `/api/portfolios`）+ openapi 再生成，契约检查通过。
- 单测 `backend/tests/unit/test_cash_flow_service.py`：四来源派生规则表、`group_total_count` 解除类型筛选口径、两级流入/流出排除与 recon、余额去重取价（含调用次数守门）、响应契约无组合级余额与投影字段、参数校验、分页。
- 集成 `backend/tests/integration/test_cash_flows_api.py`（HTTP 契约、转移拆行与 pair_ref、status 两值、构成弹窗与 recon）与 `backend/tests/integration/test_cash_flows_balance.py`（断言 1/2/3/9/11：余额逐行等于引擎单次调用、组内共享、筛选免疫、在途排除、重估经快照生成被吸收）。
- 业务口径正文已补入 [rule-cash](../reference/business-constraints.md#rule-cash)（现金流水读侧口径四条）。
- 本文与 `frontend/AGENTS.md` 入口句随 PR-1 入库；`backend/AGENTS.md` 影响面表补 `cash_flow_service.py` 行。
- ir-cli 命令组未进本 PR（follow-up chore）。

**PR-2 前端双端页面**

- `src/app/portfolio/[code]/cash-flows/page.tsx` + `/m/` 薄壳 + 共享内容组件；7 列列表 + rowSpan 合并 + 筛选栏 + 四卡 + 三弹窗 + `ManageLinksCard` 第 6 项。
- 按 §2 的方向口径修正 mockup 帧后再作目检基线。
- visual-spec 登记新页规格（弹窗复用仓库 dialog 形态，守 §13 组件复用红线；过 §19 新页面自检清单）+ §20 变更记录。
- e2e：桌面 + 移动影响面（列表合并段、四类筛选、三弹窗、钻穿与回筛）。
- 双端目检：以修正后的 13 帧为基线逐帧对照。

**PR-3 收尾**

- 主路径下仅做 H2 残留核对；回退路径下执行 H2 撤除（必须在 PR-2 合入后——转移先有新家再撤旧入口）。
- 回填 #725：待实现改动转完成项、16 条验收断言逐条勾选（未达项保持未勾选并给 follow-up 链接）。
- 文档终同步核对：visual-spec §20、`frontend/AGENTS.md` 入口、本文 §4/§5 裁剪为历史注记。

顺序依赖：PR-1 → PR-2 → PR-3；#721 裁剪独立于 PR-1，但**必须先于 PR-2 合入**（同文件冲突）。

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
- 同日重估与增量排序：引擎口径 = 重估吸收当日增量（mockup 已实现）；显示默认不显式标注。
- 现金产品详情页：保留现状（单产品语境 vs 全来源语境不同），本期不退场。
- 页面命名：「现金流水」为暂名，PR-2 前由维护者拍板。

## 7. 验证矩阵（[AGENTS.md](../../AGENTS.md) §2 / §3.5）

- 每个 PR：`.venv/bin/python scripts/verify.py plan --base origin/main` 圈影响面；PR-1 跑 `run contract` 与后端影响面测试；PR-2 跑前端质量门禁 + 影响面 e2e + 双端目检；文档改动跑 `python scripts/check_context_docs.py`。
- 后端测试按 `TEST_DB_URL` 参数化跑双通道（SQLite 与 MySQL）：仓库内**没有**预置的「docker MySQL 3307」定义（`docker-compose.dev.yml` 是 `mysql:8.0` 映射 3306、库 `investring_dev`）。PR-1 的 MySQL 通道用本机临时容器 `mysql:8.4` 映射 3307 的空库 `ir_test`，显式经 `TEST_DB_URL` 指定；`backend/.env.test` 指向远程实例，**禁止**用于测试通道（隔离闸门见 `backend/tests/db_isolation.py`）。
- 验收：#725 的 16 条断言逐条核对，交付说明改动、验证结果与未覆盖项。

## 8. 风险与回滚

- 变更全为 additive（新端点、新页面），PR-1 / PR-2 可独立回滚；H2 撤除仅在 PR-2 合入后执行。
- 余额列性能：PR-1 已按 `(平台, 日)` 去重批量取价，并对当页行才补齐展示信息与余额（不对全集取价）。若 PR-2 在真实数据量下仍慢，改为快照基线批量读（口径不变、只改读取模式），回写本文 §3。
- #721 裁剪若误伤 H1 派生字段契约（openapi / ir-cli response_fields），以 `e2e/trade-cash-pairing.spec.ts` 与 CLI 契约检查为守门证据。
