# Issue #766 实施计划：tushare 分红检测器支持同日公告

> 状态：**待评估** — 本计划在动手前需对「事件形态」与「闸门放行边界」两个决策点达成一致（见 §4）。

## 1. 背景与目标

#743（再投资）与 #765（现金分红）已支持「权益登记日 = 除息日」的同日分红事件，但仅限**人工录入**。tushare 分红检测器 [`_detect_dividends`](backend/app/services/task_runner.py#L439-L584) 仍在第 498 行用 `if ex_date <= entitlement_date: continue` 跳过同日公告，导致场外最常见的同日形态无法自动建事件。

业务约束文档已明确把「同日 `dividend_pending` 检测器支持」列为 #743/#765 的 follow-up（[business-constraints.md:216](docs/reference/business-constraints.md#L216)）。

**目标**：检测器能识别 `ex_date == record_date` 的公告，建出可由用户选择现金/再投资方式的事件，并保证确认链路畅通、幂等、非同日行为不变。

## 2. 现状梳理

### 2.1 检测器当前行为（[task_runner.py:478-584](backend/app/services/task_runner.py#L478-L584)）

1. 只处理 `CN_OTC` + `tushare` 产品；
2. 取 `get_fund_div` 返回的 `ex_date` / `record_date` / `div_cash` / `div_proc`；
3. **第 498 行**：`if ex_date <= entitlement_date: continue` —— 同日与逆序（ex < record）一并跳过；
4. 第 532 行：`if ex_date <= pos.snapshot_date: continue` —— 除息日须严格晚于最新持仓快照日（防历史公告）；
5. 第 535-543 行：按 `(portfolio_code, product_code, ex_date, platform_code)` 查已有事件去重；
6. 直造 ORM `ShareChangeEvent(event_type="dividend_pending", status="pending", event_source="tushare")`，**不经服务层校验**，不写 `tushare_event_id`、`cash_pay_date`、`share_confirm_date`。

### 2.2 同日分红的服务层约束（[share_change_event_service.py](backend/app/services/share_change_event_service.py)）

- `_validate_event_dates`（L751）：`reinvest_dividend`（设 `share_confirm_date`）与 `cash_dividend` 允许 `ex_date == entitlement_date`；**`dividend_pending` 同日刻意不放宽**（[L789](backend/app/services/share_change_event_service.py#L789)：`same_day_allowed = share_confirm_date is not None or event_type == "cash_dividend"`）。
- `_validate_cash_pay_date`（L881）：同日现金分红必须携带严格晚于除息日的 `cash_pay_date`。
- `_validate_share_confirm_date`（L810）：同日再投资必须携带严格晚于除息日、且为交易日的 `share_confirm_date`。
- 确认时 `resolve_entitlement_shares` 以 `entitlement_date`（= 除息日）的持仓快照为基数。

### 2.3 快照闸门与派生在途（[snapshot_service.py](backend/app/services/snapshot_service.py)、[reinvest_receivable.py](backend/app/services/reinvest_receivable.py)）

- `_check_share_change_events`（L2431）：非现金类按 `share_effective_date <= target_date` 阻断快照；`dividend_pending` 的 `share_effective_date` 回落 `ex_date`。
- `dividend_pending` **不进**派生在途/应收查询（`list_reinvest_receivables` 过滤 `event_type=="reinvest_dividend"`，`list_cash_dividend_in_transit` 过滤 `event_type=="cash_dividend"` 且要求 `cash_pay_date` 非空）。

### 2.4 现有测试钉住的现状

[test_detect_dividends.py:199-202](backend/tests/unit/test_detect_dividends.py#L199-L202) `test_same_day_announcement_still_skipped` 显式断言同日公告被跳过。

## 3. 核心挑战：同日 `dividend_pending` 的结构性死锁

对同日公告（`ex_date = entitlement_date = D`），检测器若照现状建 `dividend_pending`：

```
pending 闸门：share_effective_date(=ex_date=D) <= D  →  阻断快照 D
确认需要：entitlement_date(=D) 的持仓快照作为基数  →  MISSING_POSITION_SNAPSHOT
```

**快照 D 被 pending 事件阻断，而确认又需要快照 D → 死锁。** 非同日形态（`ex_date > entitlement_date`）无此问题，因为权益登记日快照在除息日快照之前就已生成。

此外，即便放行快照 D，`dividend_pending` 不进派生在途，快照 D 的基金市值会因除息净值下跌而凭空掉一块（无应收/在途对冲），违反[快照规则](docs/reference/business-constraints.md#rule-snapshot)的市值不变量。

## 4. 决策点（需评估后定案）

### 决策点 1：检测器建什么类型的事件？

| 选项 | 说明 | 优点 | 缺点 |
|---|---|---|---|
| **A. 仍建 `dividend_pending`** | 用户确认时选现金/再投资；需同时解死锁与在途 | 与非同日检测器一致，不猜方式 | 改动面大（闸门 + 在途 + 守卫） |
| **B. 建 `cash_dividend`（默认现金）** | 用 tushare `pay_date` 填 `cash_pay_date`，复用同日现金分红全套机制 | 改动最小，自动确认链路现成 | 猜错方式（再投资客户）；除息日快照后改方式被 `SNAPSHOT_DEPENDENCY` 冻结 |
| **C. 建 `reinvest_dividend`（默认再投）** | 用下一交易日填 `share_confirm_date` | 同 B | 同 B，且再投资不是监管默认 |

**推荐 A**：与检测器现有 `dividend_pending` 语义一致（不猜方式），验收断言「reinvest 与 cash 各一例」也只有 A 能同时覆盖。B/C 把「猜错方式」的成本转嫁给用户，且与 #0024 迁移「拒绝猜测方式」的决策冲突。

### 决策点 2：同日 `dividend_pending` 如何破死锁并保市值？

采用 A 后需要两处配套：

1. **闸门放行除息日快照**：对 `entitlement_date == ex_date` 的 `dividend_pending`，闸门不在除息日 D 阻断，改为 D 之后的首个快照日阻断（给用户留出一个确认窗口，未确认则照常停快照）。
2. **派生在途对冲除息缺口**：同日 `dividend_pending` 按「基数份额 × div_cash」派生一笔现金在途（`IN_TRANSIT_DIVIDEND`），从除息日起计入市值——金额与确认后现金/再投完全一致，确认时事件类型切换不产生市值跳变。

> 若不做第 2 点，快照 D 的市值会凭空下跌，需用户确认后手动重算除息日及之后快照才能修复，体验差且违反不变量。

## 5. 实施步骤（按决策点 1=A、决策点 2=放行+在途 展开）

### 步骤 1：放宽检测器跳过条件

[task_runner.py:498](backend/app/services/task_runner.py#L498)

```python
# 旧：if ex_date <= entitlement_date: continue
# 新：仅跳过逆序（ex < record）的脏数据；同日放行
if ex_date < entitlement_date:
    continue
```

- 「误检历史公告」由第 532 行 `ex_date <= pos.snapshot_date` 兜住，无需引入 `ann_date`。
- 可同步把 `get_fund_div` 的返回键补上 `ann_date` / `pay_date`（仅留痕用，不参与判据），方便后续运维定位。

### 步骤 2：闸门放行同日 `dividend_pending` 的除息日快照

[snapshot_service.py `_check_share_change_events`](backend/app/services/snapshot_service.py#L2431)

非现金分支的阻断条件从：

```python
and_(~is_cash_dividend, ShareChangeEvent.share_effective_date <= target_date)
```

拆为：同日 `dividend_pending` 在 `target_date > ex_date` 时才阻断（即放行除息日当天快照），其余非现金事件维持原 `share_effective_date <= target_date`。

要点：
- 用 `ShareChangeEvent.event_type == PENDING_DIVIDEND_TYPE AND entitlement_date == ex_date` 识别同日待定；
- 阻断点取 `target_date > ex_date`（不要求精确交易日，因为快照只在交易日生成，下一快照日自然 > ex_date）；
- `auto_confirm_after_snapshot` 对 `dividend_pending` 仍走 `auto_confirm_skipped`（方式待选），不自动确认。

### 步骤 3：同日 `dividend_pending` 派生现金在途

[reinvest_receivable.py `list_cash_dividend_in_transit`](backend/app/services/reinvest_receivable.py#L332) 与 `_query_cash_rows`

- 查询条件增加 `OR event_type == dividend_pending AND entitlement_date == ex_date`；
- 对 `dividend_pending` 行，`cash_pay_date` 为空，在途窗口用 `ex_date <= D < ex_date + 1`（即仅除息日当天，因为确认方式后窗口由具体类型接管）；或更稳妥：用 `ex_date <= D`（直到确认为止），不设上界——确认后事件类型切换，自动退出本查询。
- 金额复用 `_cash_row_amount`（pending 分支按基数 × div_cash 现算），与确认后现金分红的 `cash_change` 逐分一致。
- `aggregate_receivable_flows` 无需改（只处理再投资应收的成对流水；现金在途走事件现金腿，不进本函数）。

### 步骤 4：确认链路校验

- `confirm_share_change_event` → `_prepare_confirmation_event`：同日 `dividend_pending` 选现金时需传 `cash_pay_date > ex_date`，选再投时需传 `share_confirm_date > ex_date` 且为交易日——现有 `_validate_cash_pay_date` / `_validate_share_confirm_date` 已覆盖，无需改。
- `_require_receivable_not_snapshotted`：对 `dividend_pending`（`share_confirm_date is None and event_type != cash_dividend`）本就 early return，除息日快照生成后仍允许选方式，无需改。
- 确认后事件类型切换，在途/应收查询自动从 pending 分支切到现金/再投分支，金额不变 → 无市值跳变。

### 步骤 5：幂等键复核

现有 `(portfolio_code, product_code, ex_date, platform_code)` 判据已覆盖同日：重复抓取命中 `existing` 即跳过。**无需新增 `tushare_event_id`**（该字段当前检测器未写，引入需迁移与回填，超出最小变更）。

## 6. 测试计划

### 6.1 修改现有测试
- [test_detect_dividends.py:199](backend/tests/unit/test_detect_dividends.py#L199) `test_same_day_announcement_still_skipped` → 改为 `test_same_day_announcement_creates_pending_event`：同日公告建出 `dividend_pending`，字段与非同日一致。

### 6.2 新增检测器单测（test_detect_dividends.py）
- 同日正 `div_cash` → 建 `dividend_pending`（`ex_date == entitlement_date`）。
- 同日 `div_cash` 缺失/0/负 → 不建事件，留痕（复用 `dividends_skipped` 通道）。
- 同日重复抓取 → 幂等（只一条）。
- 逆序（ex < record）→ 仍跳过（行为不变）。
- 同日 + 非同日混合 → 各自正确处理。

### 6.3 新增集成测（同日 pending 全链路）
- 检测器建同日 `dividend_pending` → 快照 D 可生成（闸门放行）→ 快照 D 市值含派生在途（不凭空下跌）。
- 确认为现金（带 `cash_pay_date > D`）→ 在途转 CASH，金额一致。
- 确认为再投（带 `share_confirm_date > D`）→ 应收转份额，金额一致。
- 未确认 → 快照 D+1 被闸门阻断（行为同非同日 pending）。

### 6.4 回归
- `test_detect_dividends.py` 非同日用例全绿。
- `test_share_events_cash_same_day.py`、`test_share_events_dividend_method.py`、`test_share_events_reinvest_receivable.py` 全绿。
- `test_snapshot_service.py` 闸门相关用例全绿。

## 7. 文档同步

- [business-constraints.md:216](docs/reference/business-constraints.md#L216) 「本批明确不含」段：删除「tushare 分红检测器仍跳过 `ex_date <= record_date`」与「同日 `dividend_pending` 亦不支持」的表述，改写为「#766 起检测器支持同日公告，建 `dividend_pending`，闸门放行除息日快照并派生在途」。
- [task_runner.py `_detect_dividends` docstring](backend/app/services/task_runner.py#L439)：补充同日放行与在途派生的说明。
- [snapshot_service.py `_check_share_change_events` docstring](backend/app/services/snapshot_service.py#L2431)：补充同日 `dividend_pending` 的放行规则。
- [reinvest_receivable.py](backend/app/services/reinvest_receivable.py)：补充同日 `dividend_pending` 临时走现金在途的说明。

## 8. 风险与未覆盖项

1. **派生在途的窗口上界**：若用 `ex_date <= D`（无上界），长期未确认的同日 pending 会永久在途、市值虚高——但闸门在 D+1 阻断快照，用户必须处理才能继续，风险可控。需在测试中钉住「D+1 阻断」。
2. **与 #747 每日净值同步的联动**：检测器在 `run_nav_sync` 末尾、快照生成之前运行，顺序已满足「净值到账后建事件」。本变更不改调度顺序。
3. **`tushare_event_id` 未引入**：若未来需要跨产品去重（如同一公告多代码），再单独评估。本 issue 的幂等由 `(组合, 产品, 除息日, 平台)` 满足。
4. **HK_MUTUAL 同日**：检测器只处理 `CN_OTC`，港互认不在范围（`tushare` 数据源也无港互认分红）。
