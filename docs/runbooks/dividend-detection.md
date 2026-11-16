# 分红检测时序 Runbook：nav_sync × 除息日快照

> 定位：既有设计约束的运维可见性（#838，PR #835 合并后遗留、非阻塞）。本文件只记录操作前提、后果与补救，**不改变检测器行为**；代码位置以仓库相对路径标注。

## 1. 时序前提

分红检测在 **`run_nav_sync` 末尾**执行（`backend/app/services/task_runner.py::run_nav_sync`，末尾调用 `_detect_dividends`），按产品 `data_source` 路由取数器（#881：tushare → `fund_div`；akshare → 东财「分红送配详情」`akshare_client.get_fund_div_otc`；仅 CN_OTC，HK/场内不覆盖），检测对象是「最新持仓快照仍早于公告除息日」的组合：

- 取每个组合的最新 `portfolio_position.snapshot_date`（`backend/app/services/task_runner.py::_detect_dividends`）；
- 对某条公告，若 `ex_date <= 最新快照日`，直接 `continue` 跳过（`backend/app/services/task_runner.py::_detect_dividends` 的跳过分支）。

因此成立的前提是：**目标除息日的快照尚未生成**（等价于 nav_sync 及其前置链路先于该日快照完成）。
正常调度是 nav_sync 07:00、快照 07:30（[任务执行记录](../reference/logging.md#logging-tasks)），时序天然满足；但以下情况会破坏该前提：

- nav_sync 连续失败/自动重试，快照由别的触发源（手动、cascade 复查、追平）先行生成；
- 人工先跑 `snapshot_generate` 再跑 nav_sync；
- 补录/追平历史时先重建了 `ex_date` 及之后的快照。

### 双源取数口径（#881）

- **akshare（东财「分红送配详情」）**：列为 年份/权益登记日/除息日/每10份分红/分红发放日；金额形如「每10份派现金X元」÷10 折每份（与 tushare `div_cash` 逐条一致，实测 000001 25/25）。归一行保留 `pay_date`（分红发放日），**#887 PR2 起检测器消费**（explicit 配置命中时作适用日期）；两源发放日有 1 天差（实测除息日 20070112：akshare 20070117 vs tushare 20070116），各取本值、不跨源修正（C1）。
- **预案/无效行防线**：该接口只列已实施分红（实测 000001 含 2007 年分红预案公告历史，详情仍只有 25 行实施记录，与 tushare `div_proc=实施` 一一对应）。**缺发放日**、金额解析失败或非现金形态（送股/拆分）的行由取数层归一 `div_cash=None`，走检测器既有 #771 闸门跳过并 `dividends_skipped` 留痕（**S3 原因文案区分**：缺发放日 → 「缺发放日（取数层归一金额 None）」，金额缺失/非正 → 「每份分红金额缺失或非正」；此路径**不产生 pending+degraded 人工兜底**，与 explicit 配置命中但判据不满足的 degraded 路径分立）；**缺权益登记日**的行由检测器日期门**静默跳过、无 `dividends_skipped` 留痕**（日期门先于留痕分支，与 tushare 同口径）；除息日不可解析的行取数层 WARNING 后丢弃（无法按 (产品, 除息日) 留痕）。
- **结构漂移响亮失败**：缺必需列 / 上游有行但除息日整段不可解析 → `AkshareAPIError`，该产品本轮检测跳过（warning + rollback，与 tushare API 错误同形），不静默空集。
- **tushare 路径**：`get_fund_div` 归一 5 字段（ex_date/record_date/div_cash/div_proc/pay_date，#887 PR2 补取 pay_date，缺值/NaN 归一 None），`div_proc != "实施"` 过滤不变；#771/#766 既有口径对两源同等适用。
- **分红方式配置层 + 检测接线（#887 PR1+PR2 已落地，SPEC v1.1）**：`dividend_method_config` 表存储 (组合,产品,市场,平台) 分红方式，按权益登记日生效区间解析（`dividend_config_service.resolve_dividend_method`）；`source=default`（迁移 0027 回填）仅确认弹窗预选、检测器视同无配置（A3），`source=explicit` 供检测器自动建带类型事件（PR2 接线，`source_filter='explicit'`）。配置可经 `ir dividend-config list/set/remove` 或组合详情 CN_OTC 持仓行入口管理；写侧恒 CN_OTC。**PR2 检测器消费 pay_date**：explicit 命中时现金建 `cash_dividend`（cash_pay_date=pay_date，同日须严格 >ex、非同日允许 ==ex，A5/R8）、再投建 `reinvest_dividend`（share_confirm_date=pay_date，须产品市场交易日且 >ex，叠加 A5b 滞后守卫 `pay_date >= try_get_next_trading_day(ex_date, nav_lag_days+1)`，nav_lag_days=0 退化 pay>ex）；pay_date 缺失/非法/不满足判据/日历耗尽/市场不允许 → 回退 `dividend_pending` + **degraded 留痕**（`dividends_degraded`，reason：pay_date_missing/pay_date_invalid/pay_date_not_after_ex/reinvest_non_trading_day/reinvest_nav_lag_window/market_not_allowed，经 `_derive_log_fields` 落任务终态 error_message，与 `dividends_skipped`「不建事件」语义分立）。**确认/自动确认/快照闸门与重放/应收派生仍不读配置**（检测器是 explicit 配置的唯一账务判定消费方）；无 explicit 时 pending 行为逐字不变（A3）。承诺口径：同日再投常态零人工，pay_date 落滞后窗口或净值迟披露时回退待定+留痕、人工确认兜底，**不得写成无条件零人工**。

## 2. 错过窗口的后果

- **该公告不会建事件**：检测器对 `ex_date <= 最新快照日` 的组合静默跳过，且**不写 `TaskExecutionLog`、不写 `dividends_skipped`**（留痕只覆盖「本会建事件但 `div_cash` 缺失/非正」的分支，见 `backend/app/services/task_runner.py::_detect_dividends` 的 `dividends_skipped` 分支、[份额变动事件](../reference/business-constraints.md#rule-event)）。
- **后续快照没有分红口径**：现金在途/再投资应收不会派生（`backend/app/services/reinvest_receivable.py`），`ex_date` 起的净值缺口不会被补齐；事件列表里也不会有这条记录（[份额变动事件](../reference/business-constraints.md#rule-event)）。
- **检测器不会自愈**：重跑 nav_sync 仍会命中同一跳过条件；错过窗口后只能人工补录。

判定是否已错过（先排除「本来就不该建事件」的组合）：

1. 先确认该组合在权益登记日持有该产品且对应平台非空：检测器只对最新快照中 `shares > 0`、`platform_code` 非空的持仓行建事件（`backend/app/services/task_runner.py::_detect_dividends` 的持仓筛选段），未持有/已清仓的组合本就不在建事件范围内，不适用本判定；
2. 取该组合最新快照日；若 `>=` 公告的 `ex_date`，即已错过检测窗口；
3. 事件列表按 `(产品, 除息日, 平台)` 查不到对应记录；
4. 任务日志不会出现该公告的跳过项（这正是本 runbook 要说明的「无痕」）。

## 3. 人工补救（需授权，生产操作前先确认可回退）

补录事件要求 `ex_date` 晚于最新快照日（[份额变动事件·日期约束](../reference/business-constraints.md#rule-event)），所以必须先回退快照：

1. **停写**：确认没有并发的 nav_sync / snapshot 任务在跑（否则删除后又被重建）。
2. **回退快照**：先 `DELETE /api/snapshots/{portfolio_code}/bulk/{from_date}?dry_run=true` 预览将删除的日期范围（零副作用），确认范围无误后再 `DELETE /api/snapshots/{portfolio_code}/bulk/{from_date}?confirm=true` 执行；`from_date = ex_date`（含当日），逐日删除并触发级联回退（`backend/app/routers/snapshots.py::delete_snapshots_bulk`）。
3. **补录事件**：按公告手工创建分红事件（现金/再投/待定），`ex_date`、`entitlement_date`、`platform_code`、`div_cash` 须与公告一致；同日形态按[份额变动事件·同日登记/除息](../reference/business-constraints.md#rule-event)的口径把关。
4. **重建快照**：从 `ex_date` 起逐日生成或重算；现金分红到账日、再投资份额确认日按事件口径落账。
5. **复核**：事件列表出现该记录；`ex_date` 快照的 `in_transit_total` / 份额变动符合预期；总额与公告金额一致。

> 回退快照会影响其后的派生状态（事件退回 pending、依赖快照的操作被冻结），务必先备份/确认影响范围，再执行第 2 步。

## 4. 留痕能力现状与后续

当前跳过**不落任何记录**；这是 #838 的已知边界，本文件不改行为。若需要事后可检索：

- 备选方案 A：在 `backend/app/services/task_runner.py::_detect_dividends` 的 `continue` 前，把跳过项并入 `skipped`/任务日志（形态对齐 `dividends_skipped`，按 `(产品, 除息日)` 去重）；
- 备选方案 B：仅落运行日志（INFO），不进任务终态，避免把正常历史公告变成告警噪音。

二者都需要单独评估（会影响任务日志口径与告警量），不混入本次文档改动。

## 5. 关联

- #766 / PR #835（同日 `dividend_pending` 与在途派生）
- #747、#746（nav_sync 与快照的调度时序）
- #771（`div_cash` 缺失/非正的检测口留痕口径）
- #837（pending 期产品维度 `daily_profit` 口径，独立缺陷项）
