# 分红检测时序 Runbook：nav_sync × 除息日快照

> 定位：既有设计约束的运维可见性（#838，PR #835 合并后遗留、非阻塞）。本文件只记录操作前提、后果与补救，**不改变检测器行为**；代码位置以仓库相对路径标注。

## 1. 时序前提

tushare 分红检测在 **`run_nav_sync` 末尾**执行（`backend/app/services/task_runner.py:215`），检测对象是「最新持仓快照仍早于公告除息日」的组合：

- 取每个组合的最新 `portfolio_position.snapshot_date`（`backend/app/services/task_runner.py:521-536`）；
- 对某条公告，若 `ex_date <= 最新快照日`，直接 `continue` 跳过（`backend/app/services/task_runner.py:541-542`）。

因此成立的前提是：**目标除息日的快照尚未生成**（等价于 nav_sync 及其前置链路先于该日快照完成）。
正常调度是 nav_sync 07:00、快照 07:30（`docs/reference/logging.md:163-175`），时序天然满足；但以下情况会破坏该前提：

- nav_sync 连续失败/自动重试，快照由别的触发源（手动、cascade 复查、追平）先行生成；
- 人工先跑 `snapshot_generate` 再跑 nav_sync；
- 补录/追平历史时先重建了 `ex_date` 及之后的快照。

## 2. 错过窗口的后果

- **该公告不会建事件**：检测器对 `ex_date <= 最新快照日` 的组合静默跳过，且**不写 `TaskExecutionLog`、不写 `dividends_skipped`**（留痕只覆盖「本会建事件但 `div_cash` 缺失/非正」的分支，见 `backend/app/services/task_runner.py:554-566`、`docs/reference/business-constraints.md:203`）。
- **后续快照没有分红口径**：现金在途/再投资应收不会派生（`backend/app/services/reinvest_receivable.py`），`ex_date` 起的净值缺口不会被补齐；事件列表里也不会有这条记录（`docs/reference/business-constraints.md:203`、`:204`）。
- **检测器不会自愈**：重跑 nav_sync 仍会命中同一跳过条件；错过窗口后只能人工补录。

判定是否已错过：

1. 取该组合最新快照日；若 `>=` 公告的 `ex_date`，即已错过检测窗口；
2. 事件列表按 `(产品, 除息日, 平台)` 查不到对应记录；
3. 任务日志不会出现该公告的跳过项（这正是本 runbook 要说明的「无痕」）。

## 3. 人工补救（需授权，生产操作前先确认可回退）

补录事件要求 `ex_date` 晚于最新快照日（`docs/reference/business-constraints.md:211`），所以必须先回退快照：

1. **停写**：确认没有并发的 nav_sync / snapshot 任务在跑（否则删除后又被重建）。
2. **回退快照**：`DELETE /api/snapshots/{portfolio_code}/bulk/{from_date}`，`from_date = ex_date`（含当日），逐日删除并触发级联回退（`backend/app/routers/snapshots.py:444-507`）。
3. **补录事件**：按公告手工创建分红事件（现金/再投/待定），`ex_date`、`entitlement_date`、`platform_code`、`div_cash` 须与公告一致；同日形态按 `docs/reference/business-constraints.md:207-208` 的口径把关。
4. **重建快照**：从 `ex_date` 起逐日生成或重算；现金分红到账日、再投资份额确认日按事件口径落账。
5. **复核**：事件列表出现该记录；`ex_date` 快照的 `in_transit_total` / 份额变动符合预期；总额与公告金额一致。

> 回退快照会影响其后的派生状态（事件退回 pending、依赖快照的操作被冻结），务必先备份/确认影响范围，再执行第 2 步。

## 4. 留痕能力现状与后续

当前跳过**不落任何记录**；这是 #838 的已知边界，本文件不改行为。若需要事后可检索：

- 备选方案 A：在 `backend/app/services/task_runner.py:541-542` 的 `continue` 前，把跳过项并入 `skipped`/任务日志（形态对齐 `dividends_skipped`，按 `(产品, 除息日)` 去重）；
- 备选方案 B：仅落运行日志（INFO），不进任务终态，避免把正常历史公告变成告警噪音。

二者都需要单独评估（会影响任务日志口径与告警量），不混入本次文档改动。

## 5. 关联

- #766 / PR #835（同日 `dividend_pending` 与在途派生）
- #747、#746（nav_sync 与快照的调度时序）
- #771（`div_cash` 缺失/非正的检测口留痕口径）
- #837（pending 期产品维度 `daily_profit` 口径，独立缺陷项）
