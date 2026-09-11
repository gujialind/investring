# backend/AGENTS.md — 后端模块指南

> 业务不变量/领域模型见根 `AGENTS.md` §2；错误码触发条件与字段级清单见 `docs/reference/business-constraints.md`（**改 services/routers 前必读**）；分层约定与架构见本文件 §1；**怎么跑、易踩坑**见 §2-§7。

## 1. 架构

### 1.1 分层目录与职责

| 目录                                                  | 职责                                                                                          |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `app/routers/`                                      | HTTP 薄适配层：解析参数、鉴权（`Depends`）、调 service、`db.commit()`、序列化；业务错误交全局 handler，不写 try/except 业务分支 |
| `app/services/`                                     | 全部业务规则/不变量/计算/状态机/ORM 读写；**只抛领域异常、不 import fastapi、不 commit（可 flush）**                      |
| `app/models/`                                       | SQLAlchemy 表模型                                                                              |
| `app/schemas/`                                      | Pydantic 请求/响应模型                                                                            |
| `app/constants/`                                    | 纯数据常量的单一事实来源：`asset_dimensions.py`（五维字典种子）、`audit_actions.py`（审计 action / resource\_type 枚举，列宽由 `tests/unit/test_audit_service.py` 守门）、`db_charset.py`（**全库** charset/collate，`Base.metadata` 钩子与迁移 0015 共用，由 `tests/unit/test_db_charset.py` 守门）、`log_charset.py`（#427 的历史快照：迁移 0014 的纳管清单 `CHARSET_TABLES`，只读勿增删）；埋点处禁止写字面量 |
| `app/utils/`                                        | 安全（密码/Token/登录锁）等工具                                                                         |
| `app/config.py` / `database.py` / `dependencies.py` | 配置、DB 会话、鉴权依赖（**声明式 `Base` 不在 `database.py` 里**——见 `app/models/base.py`，字符集钩子与之同址，§1.4）                                                                               |
| `app/logging_config.py` / `context.py` / `request_context.py` | 集中日志配置（stdout 单行 JSON）、请求级上下文（request_id / actor / client_ip）、请求上下文中间件（#404，约定与踩坑见 §1.5）    |

**分层约定（router 为 service 薄适配器）**：业务逻辑单一实现于 service，REST 共用，杜绝并行实现漂移。

* **事务边界属于 session 拥有者**：service 收到调用方注入的 session，不 `commit`/`rollback`（可 `flush`）；REST 在 router `db.commit()`（部分失败语义的端点如 recalculate 按 errors 决定 rollback/commit）。**合理例外**：自持 `SessionLocal` 的后台执行体（sync job 线程、scheduler 触发体）与 `task_runner` 的 checkpoint 提交（逐日快照回补、逐产品远程同步，需保留部分成功；**含任务执行记录的多段 commit**——`running` 先落库、终态再落库，见 §1.3「任务执行记录」）可自行 commit。

* **领域异常统一**：service 抛 `app/services/exceptions.py::BusinessError`（携 `code`/`message`/`http_status`/`details`）；`main.py` 全局 handler 映射为 `JSONResponse{"detail": {"error": code, "message": message}}`（保持前端契约；默认 422、重复创建类 400、NOT\_FOUND 404）。service 内**禁止** import/抛 `HTTPException`。

### 1.2 路由与 API 前缀

端点以 `app/main.py` 注册为准；CLI 机读契约见 `ir schema`。

> 前缀约定：所有资源挂 `/api/<资源名>`（如 `/api/snapshots/...`）；`cash_transfers` 作为 `portfolios` 子资源挂 `/api/portfolios/{code}/cash-transfer`；日志/任务/通知/数据源在 `/api/system/*` 二级命名空间。

### 1.3 核心服务

业务核心集中在四个服务模块（函数级细节读源码）：

* **`snapshot_service.py`**：快照生成/重算/校验；三表固定生成顺序、级联回退与自动重确认（根 §2.6）；持仓增量累加与在途计算（根 §2.5）。入口：`generate_daily_snapshots`（单日生成）、`_delete_existing_snapshots`（删除 + 级联回退）、`recalculate_snapshots`（单一事务重算）、`auto_confirm_after_snapshot`（逐日重确认）。
  - 单日生成的连续性校验（`SNAPSHOT_NOT_CONTINUOUS`）在**重算路径逐日重建时内部 bypass**；批量删除从最新日**倒序、逐日 commit**。
  - 级联回退（`_cascade_unconfirm_*`）的日期键：申赎按 `apply_date == D`、事件按 `entitlement_date == D`（`ex_date == D` 但 `entitlement_date < D` 的事件基数快照仍在，保持 confirmed、重建时被重新应用）；**交易不级联**——其取价依赖产品行情而非组合快照。
  - `auto_confirm_after_snapshot(D)` 确认口径：`apply_date <= D` 的 pending 申赎、`confirm_date == 下一交易日(D)` 的 pending 交易、`ex_date == 下一交易日(D)` 的 pending 事件（仅父/独立记录）；跨天转移两腿均 pending 时同组确认。
  - 快照预校验（生成前提检查）对存在 `ex_date <= target_date` 的 pending 事件返回 **`failed`**（根 §2.6 只陈述「不存在会影响该日的 pending 记录」这一语义结论）。
  - 取价实现为生成与预校验**共用同一函数**；`MISSING_NAV` 错误信息按 `[T=…]` / `[T-N=…]` 规则分组。
  - **可观测性**（#305，savepoint 机制修正 #419）：重算 / catch-up / generate-next / 调度路径的响应（调度为任务日志）携带逐日 `auto_confirmed` 与 `warnings`，逐日错误条目含 `code`/`details`；auto_confirm 循环单条 DB 级失败经 **session 级 savepoint**（`db.begin_nested()`）隔离，不毒化 session、循环继续——**连接级 savepoint 隔离不了 ORM flush 失败**：SQLAlchemy 会回滚 session 持有的整个 DBAPI 事务、把根因挂到父 `SessionTransaction._rollback_exception`（读法 `session._transaction._rollback_exception`），外层已 flush 的成果当场清零，此后任何会话操作都抛 `PendingRollbackError`（#419 实测）。savepoint 必须用**显式** `sp = db.begin_nested()` / `sp.commit()` / `sp.rollback()`，**不能用 `with`**：flush 失败时 SQLAlchemy 已自行回滚并关闭该 nested 事务，但 `with` 期间它仍是 session 的 `_trans_context_manager`，conftest 的 `after_transaction_end` 监听器补 savepoint 时会撞 `InvalidRequestError: Can't operate on closed transaction inside context manager`，把真实根因整个掩盖（#419 实测，7 个用例受影响）；显式形式下只在 `db.get_nested_transaction() is sp` 时才回滚（否则 SQLAlchemy 已回滚过，重复回滚抛 `InvalidRequestError`），回滚自身失败则响亮记 ERROR 并按下方 `SESSION_ABORTED` 终止。session 不可恢复时（连接级失效、`PendingRollbackError`、savepoint 回滚失败、失败后 `Session.is_active` 为假）记**一条** `SESSION_ABORTED` 根因后终止本段，且**进入守护前先探测**可用性——否则每条都只会重抛同因错误。各路径收敛方式：重算逐日循环见到 `SESSION_ABORTED` 即 break 并把根因写进 `results[].errors`（router 整体 rollback，响应仍 `200 + errors`）；catch-up 与调度路径在随后的 `db.commit()` 抛出后走各自 rollback + 记录（根因条目已在 `auto_confirmed` / `auto_confirm_failed` 里）；generate-next 无 errors 契约，冒泡为 500。

* **`position_service.py`**：可用现金/份额实时计算（根 §2.5/§2.3）；现金重估走 `manual_market_value` 覆盖层，绝不直写 `portfolio_position`。两条现金口径的函数级表达式：

  ```
  compute_cash_balance(T)：全量历史口径 = SUM(confirmed CASH trades WHERE confirm_date <= T)
                                       + SUM(confirmed events WHERE ex_date <= T, cash_change != 0)；无快照时降级用

  calculate_available_cash(T?) = 最新快照日 portfolio_position 的 CASH cash_amount（基线）
                               + SUM(confirmed CASH buys  WHERE confirm_date > 快照日 [AND confirm_date <= T])
                               − SUM(confirmed CASH sells WHERE confirm_date > 快照日 [AND trade_date <= T])
                               − SUM(pending CASH sells [WHERE trade_date <= T])
                               + SUM(confirmed event cash_change WHERE ex_date > 快照日 [AND ex_date <= T])
  ```

  T（`as_of_date`）为空时不设上限。可用现金基线只取 `product_code == "CASH" and shares is None` 的行，在途行不计入。

* **`trade_service.py`**：调仓创建/确认/取消（根 §2.5/§2.7）。配对腿同步的单一实现是 `sync_transfer_group`——只同步 `trade_date`/`status`/金额，**不传播 `confirm_date`**。
  - `cash_transfers.py` 以 `cross_day` 字段（`schemas/cash_transfer.py`）区分当天完成与跨天到账；跨天判断（`list_cash_transfers`）**以 buy 腿为准**——`buy.status != "confirmed"` 或 `buy.confirm_date > buy.trade_date`；`confirm_cash_transfer` 确认组内所有仍为 pending 的 CASH 腿。

* **`subscription_service.py`**：申赎创建/确认；首次申购净值 1.0000 并激活组合（根 §2.8/§2.2）。
  - 申赎 `confirm_date` 恒取 `get_next_trading_day(apply_date, days=1)`；unconfirm 时**重算期望确认日而非置 None**，保持 pending 记录 `confirm_date` 非空，避免快照校验 `confirm_date <= target` 因 SQL NULL 比较漏检。
  - **负现金防护重构**（#203）：#180 在申购 unconfirm 前的现金守卫已移除（该守卫曾阻断快照删除级联、异常被吞后产生孤儿记录），改由两处消费点防线拦截（根 §2.7）；存量负现金脏数据经快照 status 端点的 `negative_cash_platforms` 暴露，供运维处置。

其余模块中需记住的设计点：`snapshot_recalc_job.py`（#89 异步重算：复用 sync\_job 表 + 线程池，同类型单 active 锁，终态经 `GET /api/sync-jobs/{id}` 轮询）；`product_service.py::calculate_confirm_days` 为确认天数单一实现。其他服务职责读各文件 docstring。

* **`app/utils/quantize.py` 是精度的唯一入口**（#428）：三个 helper 与三个精度一一对应——`quantize_shares` / `quantize_amount`（2 位）、`quantize_nav`（4 位），**均显式 `rounding=ROUND_HALF_UP`**。调用点禁止写 `Decimal("0.01")` / `Decimal("0.0001")` 字面量再 `quantize()`：不传 `rounding=` 即吃 Decimal 上下文缺省的 **ROUND_HALF_EVEN**，两者只在「末位后一位恰为 5」的边界值上分叉——#428 前 7 处 4 位站点（快照构造点的 `total_value`/`unit_price`/`unit_price_change_pct`/`in_transit_total`、`cost_per_share`、场外确认对账的两侧）全是这种静默漂移，而 `quantize.py` 的 docstring 早已声称「均为 HALF_UP」。守门：`tests/unit/test_quantize.py::TestQuantizeNav`（含 HALF_UP/HALF_EVEN 判别式）、`tests/unit/test_quantize.py::TestAmountToSharesTwoStepQuantization`（#425：金额 → 份额必须先量化金额到分）、`tests/unit/test_snapshot_service.py::TestValueSnapshotFourDecimalRounding`（构造点边界值，取 `total_value/total_shares` 恰落第 5 位为 5 的商，改回缺省即红）、`tests/integration/test_trades.py::TestTradePreview` 的两条 4 位对账边界用例。`cost_per_share` 构造点同时**去掉了外层 `float()`**（与 #421 把快照构造点从 float 改回保标度 Decimal 同口径，列是 `Numeric(10,4)`）。

* **份额变动事件：计算单点、预览复用**（#424/#425）：变动量计算是纯函数 `share_change_event_service.compute_event_fields(event_type, entitlement_shares, *, …)`（返回 `EventFieldResult`，**不碰 ORM 对象**），确认与确认预览共用——「预览 == 确认」由此保证。
  - **写回刻意分离**：`apply_event_fields(event)` 是唯一写回点、**只由确认路径调用**。预览不得复用它：`record_audit` 的 `_diff_fields` 读事件当前字段，对象被预览改过后列表行会显示未落库的值；pending 对象若被 flush 还会把「预览」写进库。
  - **`forced_adjustment` 的两项语义不得「顺手修正」**：`shares_change` / `cash_change` 为空时**保持 None、不折成 0**（快照的事件应用循环以 `event.shares_change is None` 判定「纯现金调整」并跳过份额段，折成 0 会让对 CASH 产品的合法纯现金调整被现金行守卫误杀为 `POSITION_NOT_FOUND`）；`shares_after` 恒不写回（用户直填的「调整后余额」，unconfirm 同样不清空），预览照实回 `shares_after=None`。
  - **权益份额口径同点**：`resolve_entitlement_shares(db, event)` 供确认与预览共用（平台级按 `platform_code` 过滤、基金级取各平台 `shares > 0` 之和、`forced_adjustment` 另做 `(产品, market, 平台)` 精查 → `POSITION_NOT_FOUND`）。
  - `GET /api/share-change-events/{id}/preview` 只读、**pending-only**（`INVALID_STATUS`，与 confirm 同码同消息；**对 confirmed 不服务**——前端无该消费方，与 trades/subscriptions 的 preview 口径一致）；**刻意不复刻创建期的 `check_platform_coverage`**——那是「录入完整性」规则而非状态门。路由必须注册在 `GET /{id}` 之前。
  - **#425**：`reinvest_dividend` 是唯一「金额 → 份额」的事件类型，**先 `quantize_amount` 到分、再除以 `reinvest_nav`**（与 `cash_dividend` 同口径，亦与基金公司「先按分确定应得红利、再折算份额」一致）；跳过中间量化会把金额量化损失带进份额，跨四舍五入边界时与基金公司台账差 0.01 份。

* **`audit_service.py`（#405，跨切面）**：`audit_log` / `system_error_log` 的**唯一写入路径**，埋点一律调 `record_audit` / `record_system_error`，不直接 `db.add(AuditLog(...))`。

  - **必埋范围**：申赎、调仓、跨平台现金转移、份额变动事件、快照 generate/recalculate/delete（含级联回退）、现金手动重估，动作取 create/update/confirm/unconfirm/cancel/delete（快照另有 generate/recalculate/cascade\_unconfirm）。产品/平台/投资人/组合 CRUD 本期不埋（只覆盖高风险子集）。埋点在 **service**，故 REST 与 CLI 共用、不漏记；原先 router-inline 的申赎 cancel/delete、交易 delete、事件 delete 已提取为 service 函数。
  - **事务归属**：`record_audit` 不 commit，审计行随业务事务一起提交或回滚。写入包**连接级 savepoint**（`db.connection().begin_nested()`）且用 **Core INSERT** 而非 ORM `add()`+`flush()`：ORM flush 失败会把根因挂到 `SessionTransaction._rollback_exception`（读法 `session._transaction._rollback_exception`）并把事务置 DEACTIVE，此后任何会话操作都抛 `PendingRollbackError`，而**连接级** savepoint 的回滚碰不到 ORM 事务状态机（#419 实测），等于审计把业务事务拖垮；Core execute 失败只污染该条语句，故此处连接级足够。**审计不跟着 #419 换成 session 级**：需要 ORM 状态复位的只有「单条失败后还要继续循环」的 auto_confirm 路径；同形状的 `trade_service.sync_transfer_group` 与 `share_change_event_service._confirm_fund_level_event` 失败即 `rollback; raise`、不承诺 session 继续可用，同样保持连接级。旧文「非 session 级——与 conftest 的 savepoint 重启监听器冲突」需按 #419 实测限定：该冲突在 **`with db.begin_nested():`** 形式下真实发生（closed 事务仍是 `_trans_context_manager`，监听器补 savepoint 撞 `InvalidRequestError` 并掩盖根因，见 §1.3「可观测性」条），显式 begin/commit/rollback 形式下不复现；但审计此处保持连接级的理由与监听器无关，而是 Core execute 失败根本不留 `_rollback_exception`、session 继续可用。savepoint 之前必须先 `db.flush()` 把调用方的业务改动落进外层事务，否则 `ROLLBACK TO SAVEPOINT` 连带撤销它们（调用方却收到成功响应）；**该 flush 与建 savepoint 本身都在 `try` 内**（`sp = None` 哨兵，#422a）——flush 干的是调用方的活、撞约束即抛，此前它在守护之外逃出函数，`recalculate_snapshots` 的收尾埋点因此把 router 承诺的 `200 + results[].errors` 变成 `500 RECALCULATION_FAILED`（逐日 except 刻意不 rollback，半截 pending 对象正好留给收尾 flush 撞）。
  - **失败响亮**：审计写不进去 → 只回滚 savepoint + stdout ERROR + 落一条 `system_error_log`（`error_type=AuditWriteFailure`，走独立 `SessionLocal`），绝不外抛也绝不静默；`record_system_error` 自身 best-effort，写失败只记 stdout。守护覆盖 **flush → 建 savepoint → Core INSERT → 回滚** 四个环节（#422）：savepoint **回滚失败**不再被裸 `pass` 吞掉，另记一条带 `exc_info` 的 ERROR，且日志与 `system_error_log` 文案按回滚结果分支表述（回滚失败 ≈ 连接已死，此时「业务事务不受影响」是假话）。`request_path` 在唯一写入路径内按 `SYSTEM_ERROR_PATH_MAX`（= 列宽 200，守门测试绑死模型）截断——HTTP 路径长度无界（h11 请求行上限 16 KB），超宽在 MySQL 严格模式下 errno 1406 会让**整条**错误记录静默消失；完整 path 由 `main.py` handler 的 stdout ERROR 行保留，二者互补。**同族的第二个静默面是字符集**（#427）：当时库级 utf8mb3 与连接侧 utf8mb4 不对称，自由文本含 4 字节字符（emoji / CJK 扩展 B）时 errno 1366 同样被本处的 best-effort except 吸收、整条记录丢失——已由全库统一 utf8mb4 根治（#427 起、#433 完成，见 §1.4），**不再走「写入侧转义」路线**（那会引入「哪些列要转义」的清单式遗漏面）。
  - **actor 归属**：取 `context.get_actor()`，无请求上下文（调度器、线程池）落 `SYSTEM` 哨兵——`investor_code` 是 NOT NULL 列，缺哨兵则后台路径整条写不进去。`snapshot_recalc_job` 跨线程**只传 `request_id` 不传 actor**（`ThreadPoolExecutor.submit` 不复制 contextvars，且线程池与价格同步共用），故后台重算的审计恒为 SYSTEM、stdout 日志仍可关联触发请求。
  - **载荷口径**：diff-only（`_diff_fields` 只留变化字段，无变化则两侧皆 None；数值两侧统一转 Decimal 再比——DB Numeric 读出 Decimal 而 update schema 是 `Optional[float]`，`Decimal("1234.5600") != 1234.56` 恒真，直接比会把原样重提交的字段误判为变更、写出根本没发生的审计变更）、`ensure_ascii=False`（中文原样落库）、`default=str`（Decimal/date 字符串化）；create 无 `old_value`、delete 无 `new_value`。副作用**一律不另开记录**，但只有申赎确认（`cash_transfer_group`）与交易创建（`transfer_group`）真把它折进了 `new_value`——组合激活、交易确认的配对腿镜像本期不入载荷。快照 generate 的三个数值载荷（#421）同为**保标度字符串**：`_generate_portfolio_value_snapshot` 构造 ORM 对象时按列标度量化为 Decimal（`total_value`/`unit_price`/`unit_price_change_pct`/`in_transit_total` 4 位、`total_shares`/`frozen_shares` 2 位），埋点原样交给 `default=str`——**不再在构造点 `float()`**（那会丢标度、并让 0.1 类值带二进制误差进 JSON）。同函数的 API 返回 dict **刻意保留 `float()`**：那是前端 JSON 数字契约，与审计载荷口径正交。
  - **不重复记账**：级联回退若**委派**给既有实现（申赎走 `unconfirm_single_subscription`），由被委派方留 `unconfirm` 痕，不再叠 `cascade_unconfirm`；只有**就地改字段**的（事件级联）才自带 `cascade_unconfirm`。空删除（generate 每次都先调 `_delete_existing_snapshots`）不留 `delete` 痕，否则淹没审计日志。**空更新同理**：三个 update service（申赎 / 交易 / 份额事件）都只在 diff 非空时才调 `record_audit`——PUT 原样重提交是编辑表单常态，不设这道闸每次保存都会灌一条 `old_value`/`new_value` 皆 NULL 的空载荷行（零取证价值）。

* **`task_runner.run_task` 是任务执行记录（`task_execution_log`）的唯一编排点**（#406）：「建 log（running，先 commit）→ 派发 `_TASK_DISPATCH` → 按结果落 status/duration/records/error → commit」。手动触发（`routers/tasks.py`）与调度触发（`scheduler_service`）共用它，两条路径不再各有 status 推导——那正是 #406 之前手动/自动可观测性不对称的成因。
  - **触发来源**取 `TRIGGER_MANUAL`（`"manual"`）/ `TRIGGER_SCHEDULED`（`"scheduled"`）常量，埋点禁写字面量；前端执行历史按它区分「手动 / 自动」。
  - **跳过不是执行**：锁被另一进程持有、当日非交易日两类跳过**不建执行记录**（否则每个非交易日为每条 job 各积一行无信息量噪音）；判定收敛在 `scheduler_service._should_run_today`，两条 job 共用。
  - **`records_*` 口径**（`_derive_log_fields` 按 task_code 归一，各任务返回 dict 结构不统一）：`nav_sync` = products_count / 差额 / len(failed_products)；`snapshot_generate` = portfolios_processed / 差额 / len(auto_confirm_failed)；`trading_calendar_sync` 与 `log_cleanup` 只填 total/success（synced_count、删除行数求和）。**`records_failed` 为 NULL 表示「未度量」，不是「零失败」**——语义不明处宁可留空，读侧不得把 NULL 当 0。#305 的 warnings / auto_confirm_failed 归并语义与 `error_message` 1000 字符截断原样保留在此处（原先写在 router 的按 code 分支里）。
  - **失败**：任务抛异常 → `status="failed"` + `error_message`（截断 1000，保留 `str(e)` 摘要语义）+ `error_stack`（traceback），异常**原样上抛**，HTTP 映射（500 / `BusinessError` 走全局 handler）留在 router；未知 task_code → `NotFoundError`（不建 log 行）。
  - **session 与事务**：复用调用方 session，不 close；异常路径**不 rollback**（`running` 行已 commit，不能连它一起回滚），任务体内未提交的业务写入由任务自身编排语义收口（`run_nav_sync` 逐产品 checkpoint、`_generate_snapshots_for_date` 逐日 rollback+commit）。
  - `run_nav_sync(db, log_id)` 的 `log_id` **必填**（每条 `NavSyncDetail` 都要挂到当次执行记录，否则逐产品明细无父行）；`run_snapshot_generate(db)` **没有** `log_id`——快照链路没有逐日明细表可关联，参数曾是死参数，已移除。

### 1.4 数据模型与关键约束

表结构与全部唯一约束以 `app/models/` 为准。需记住的设计决策：

* `trade.transfer_group` **NOT NULL**（每笔 trade 必属一个业务组），唯一约束 `(transfer_group, product_code, trade_type)`：基金腿与 CASH 腿按 `product_code` 区分、现金转移两腿按 `trade_type` 区分、申赎为单腿 `sub_{id}`，故 NOT NULL 下仍无碰撞。

* `portfolio_position` 有 CHECK 约束：`shares` 与 `cash_amount` 二者恰有其一（净值型 vs 非净值型）。

* `share_change_event` 双日期分级：`ex_date`（除息日，应用日）+ `entitlement_date`（权益登记日，基数日），要求 `ex_date > entitlement_date` 且均为交易日；`parent_event_id` 为基金级拆分子记录自引用。

* 外键删除行为均为 **RESTRICT**，通过业务流程（关闭/停用）管理生命周期，保留历史数据。

* **外键的约束名以模型 `ForeignKey(name=...)` 为规范名**（#434）：MySQL **不做**「同表同列对指向同一目标」的重复外键去重，两条语义相同的约束会同时生效。历史事故正是两条路径各建一条并存在生产库 `nav_sync_detail.job_id` 上——`create_all` 建未命名 FK、MySQL 自动命名成 `nav_sync_detail_ibfk_2`，迁移 0001 又用 `op.create_foreign_key('fk_nav_sync_detail_job_id', …)` 显式建了一条；0001 里那句包着 `try/except: pass`（为兼容 `create_all` 已建表的新库），把「同语义约束已存在」吞掉、没暴露成错误，于是生产（先建表、后跑迁移的历史顺序）两条都留下了。**危害不在冗余本身，而在后续迁移的静默前提失真**：按显式名 `DROP FOREIGN KEY` 只删掉一条，另一条继续强制外键语义，迁移作者会在「外键已解除」的错误前提下改列。故**新加外键一律显式 `name=`**，且与任何迁移里 `create_foreign_key` 用的名字一致；迁移 0001 已在所有环境执行过、**刻意不改**（改它会让「跑过旧 0001 的库」与「跑过新 0001 的库」落到不同状态），存量收敛交给迁移 0016。
  - **迁移 0016**（`down_revision = '0015'`）把 `nav_sync_detail.job_id` 收敛到「恰好一条、名为 `fk_nav_sync_detail_job_id`」，三条分支：已是目标态 → 空转（全新库路径 `create_all` 已按模型显式名建出唯一一条）；两条并存（生产现状）→ **只多删**冗余那条、好的那条全程不碰；只有自动名 `*_ibfk_N`（#433 之前建的旧库）→ 拆掉重建为显式名（避免各环境外键名长期分叉，也让 `0001.downgrade()` 在这些库上可用）。**downgrade 刻意 no-op**（与 0013 同型）：逆操作是把语义完全相同的冗余约束加回来，不恢复任何功能、只会把同一个陷阱重新埋进库，且 `ci.yml` 的 `alembic downgrade -1` 是真实路径、不能 raise。两条外键的 `ON DELETE`/`ON UPDATE` 规则不一致时**响亮失败**（`RuntimeError`）、绝不静默挑一条——误删一条外键 = 静默丢掉一层约束语义。全库另有只读哨兵：发现**别的**列对也重复时只打 WARNING、不自动处置（同列对重复本身不足以判定该保留哪条）。守门：`tests/unit/test_migration_0016.py`（`TARGET_FK` 与模型 `name=` 绑死、迁移冻结不 import 模型、纯字符串 DDL、SQLite no-op、MySQL 上三种起始态各自收敛且越界插入仍被拒）。

* **虚拟产品**（#93）：除 `CASH`（生产为部署期种子落库）外，迁移 0006 另种子 `IN_TRANSIT_BUY` / `IN_TRANSIT_SELL`，与 CASH 同构（`market=""`、`product_type="IN_TRANSIT"`、`confirm_days=0`）；以 `product_code` 区分方向。维度标签（#128）：CASH 产品 `asset_class_code=ASSET_CASH`、其余四维 NULL；IN\_TRANSIT 五维全 NULL。

* `is_qdii` 已降级为**纯展示标签**、`nav_lag_days` 逐产品自设（业务语义见根 §2.4）。**回填口径**：迁移 `0012` 仅把场外 QDII 的 `nav_lag_days` 置 1，**港互认基金需由界面/CLI 手工设为 1**——运行期不按产品类型自动映射。

* **资产分类五维度字典**（#128）：`asset_classification` 是正交维度值字典，五个维度 asset\_class（股票/债券/商品/现金，维持 4 类，REITs/另类按需再加）/region/style/size/segment（股票行业·债券期限·商品品种共用一维），产品以 5 个 FK 列挂维度值；字典种子单一事实来源为 `app/constants/asset_dimensions.py`（迁移与 `backend/tests/seed_base.py` 种子共用）。**维度值按需扩展（YAGNI），不为假想需求预留空值**；**asset\_class 的 `sort_order` 即前端饼图/分区色板序位，变更即改色**。分类信息仅读侧派生、快照表无分类列；前端二级分组默认股票→region、债券/商品→segment、现金平铺，可经组合级 `portfolio.display_config`（#144）按大类覆盖：JSON 列仅存显式覆盖项（NULL=默认），校验以 `asset_class_dimension_rule` 规则矩阵为准（无规则行的大类如现金不可配），大类一级分区不可变；PUT 显式传 null 或空对象 {} 清空（{} 归一为 NULL 入库）、不传不修改（哨兵区分，service 公开常量 UNSET）。

* **适用关系双层落库**（#135 矩阵落库）：运行期事实来源为 DB（常量为种子源），`validate_dimension_tags` 四层校验叠加、只收紧不放松——①存在性+dimension 匹配；②`is_active` 软失效（无物理删除；update 仅校验实际变化字段的新值，存量引用停用值不阻断其他编辑）；③维度级规则表 `asset_class_dimension_rule`（required/optional，**无行=forbidden，无规则行的大类=现金型全 forbidden**——新建大类配规则后运行期即可用，无需发版）；④值级关联表 `asset_dimension_applicability`（多对多，产品所选值必须关联其 asset\_class）。产品五维标签的「必填/禁止」语义由此两表驱动，不再硬编码。

* **四张日志表的 schema 事实来源是迁移 0013**（#405）：`audit_log` / `system_error_log` / `login_log` / `task_execution_log` 此前只由 `main.py` 的 `create_all` 建表，0001–0012 无对应 `create_table`——模型改列后生产库静默不跟随（schema drift）。0013 逐表 `has_table()` 守卫，**已存在则跳过并打 warning**（生产库这些表已由 create\_all 建出），故幂等；**downgrade 刻意 no-op**——采纳型迁移的逆操作不得删表：真实部署里这四张表与其数据都早于 0013 存在，而 `docs/runbooks/deploy-rollback.md` 的 `alembic downgrade` 是真实运维路径，删表 = 销毁审计/登录/任务历史；且 `task_execution_log` 被 `nav_sync_detail.task_log_id` 外键引用，MySQL 下 DROP 必失败（errno 3730，SQLite 不校验外键故本地测不出来）。`audit_log` 列宽是硬约束（`action` 20 / `resource_type` 50 / `investor_code` 20），改 `audit_actions.py` 的常量值先看宽度（`tests/unit/test_audit_service.py` 守门）。

* **全库字符集统一 utf8mb4**（#427 起、#433 完成，常量单一事实来源 `app/constants/db_charset.py`）：库级 charset 与排序规则是 `utf8mb4` / `utf8mb4_general_ci`（生产 RDS、`ci.yml` 三处建库语句、`docker-compose.dev.yml` 的 server 字符集三处对齐），连接侧 `app/config.py` 连接串同为 `charset=utf8mb4`。缺陷面是**两侧不一致**——utf8mb3 列容不下 **4 字节** UTF-8 字符（emoji、CJK 扩展 B；中文是 3 字节、utf8mb3 容得下），MySQL 严格模式下 errno 1366 `Incorrect string value` 让**整条**记录写不进去。失效形态分两档：`record_system_error` 是 best-effort，异常被吸收后**整条错误记录静默丢失**；`record_login_log`（`dependencies.py`）、`routers/tasks.py`、`nav_sync_detail`（`task_runner` / `market_data_service` 写外部数据源原文）的写入**不是** best-effort，会外抛 500、连带登录/任务执行/净值同步本身失败。

  - **两层落地缺一不可**。① **建表侧**：`app/models/base.py::Base` 挂在 `_CharsetBase` 上，其 `__init_subclass__` 给**每个**模型子类补 `mysql_charset` / `mysql_collate`（`setdefault` 语义，子类显式值优先），使 `create_all` 建出的表自带 `CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci`。**不是** `MetaData(mysql_charset=…)`——SQLAlchemy 2.0 的 `MetaData.__init__` 不接受方言参数（直接 `TypeError`）；**也不是** `MetaData` 的 `before_create` 事件——该事件只在 `create()`/`create_all()` 触发，`CreateTable(...).compile()` 不触发，DDL 与编译期检查会脱钩。**更不是**逐模型写 `__table_args__`：25 个模型漏一处就是一处静默的 utf8mb3 表。② **存量侧**：迁移 **0015** 逐表 `ALTER TABLE … CONVERT TO CHARACTER SET utf8mb4` + 库级 `ALTER DATABASE`（MySQL-only，SQLite 直接 return）。**0014** 是 #427 的中间态（只转五张日志/同步明细表），保留不改——它已在生产执行过，改它会让历史与仓库对不上。

  - **⚠️ 0015 的「先拆外键 → 转码 → 再建外键」不是可选优化**：MySQL 拒绝在表被外键引用时转码（errno 3780 `Referencing column … are incompatible`，实测生产 30 条外键涉及的 16 张表逐表直接转全部失败）。约束名**逐运行时从 `information_schema` 读**再原样重建，绝不硬编码——生产库外键名是 `fk_rule_class` / `fk_product_region_code` 这类描述性名字，测试库/全新库则由 `create_all` 生成 `*_ibfk_N`。**禁用 `DROP FOREIGN KEY IF EXISTS`**：MySQL 全系不支持（errno 1064），写了会让整批重建外键失败、表转码成功而外键全丢（实测踩过）。幂等：整库已是目标 charset 时直接空转（新库路径 `create_all` 已建 utf8mb4 → 0015 空转），非空转时总是重放「拆→转→建」以便中途失败后重跑补齐。

  - **downgrade 有条件跳过**（0014、0015 同判据）：反向转 utf8mb3 在表内已存在 4 字节字符时必然失败，会让 `docs/runbooks/deploy-rollback.md` 的 `alembic downgrade` 半途而废，故先按 `CONVERT(col USING utf8mb3) <> BINARY col` 逐列探测（utf8mb3 表示不了的字符转换后必然与原值不同；**刻意不用依赖 `CHAR_LENGTH` 字符数语义的 `LENGTH - 3*CHAR_LENGTH` 写法**——那套口径一旦被服务端按字节计就静默失效），有 4 字节字符则**跳过该表并打 WARNING**；探测本身抛错时按「无法证明可安全回退」处理、同样跳过——不假装成功、不吞数据，空库/无 4 字节数据的库仍完整可逆（CI 往返无需 `SKIP_DOWNGRADE`）。

  - **刻意排除「写入侧转义/替换」路线**：那要维护「哪些列要转义」的清单、遗漏面不可见，且改变取证文本原貌。守门：`tests/unit/test_db_charset.py`（逐模型断言建表 DDL 带 utf8mb4、钩子不覆盖显式值且不破坏 `__table_args__` 约束元组）与 `tests/unit/test_migration_0015.py`（外键 DDL 生成、入边表查找、MySQL 往返后外键定义与 4 字节数据不变、幂等空转）。

### 1.5 配置与运行

* 配置项以 `app/config.py` + `.env` 覆盖为准。

* **日志形态**（#404）：只写 stdout 单行 JSON，不落文件——轮转交给 docker `json-file` driver（生产 10m×3）。配置入口 `app/logging_config.py::setup_logging()`，在 `main.py` 模块级调用且**必须早于**建表与调度初始化那两处 import 期副作用；**幂等**（uvicorn / pytest / `scripts/run_e2e_backend.py` 是多入口，重复配置不得让一行变两行）。固定字段 `timestamp` / `level` / `logger` / `message`，经 `extra=` 传入的其余键原样进 JSON，异常折进 `exception` 字段而不拼进 `message`（否则换行会撕开单行 JSON）；**`extra=` 禁用 `message` 键**（LogRecord 保留字，会 KeyError）。**已知例外（刻意不修）**：`--workers 2`（生产 Dockerfile CMD）与 `--reload`（dev compose）下 uvicorn **父进程不 import 应用**，`setup_logging()` 对之不生效，每次启停输出 2~4 行明文（`INFO:     Uvicorn running on …`、`INFO:     Started parent process [18504]`）；工作进程起后一切皆 JSON。不用 `--log-config` 收编——那会让日志配置分裂成应用内 + 命令行双源。**程序化入口必须传 `log_config=None`**（#417）：`uvicorn.Config.__init__` 无条件重装 uvicorn 默认明文日志配置（handler 重装、`propagate=False`），会覆盖 import `app.main` 期已执行的 `setup_logging()`；`scripts/run_e2e_backend.py` 传 `log_config=None` 跳过后，uvicorn 自身日志（含 ASGI 异常堆栈）冒泡 root 变单行 JSON。命令行形态（生产 Dockerfile CMD）顺序相反——CLI 先配日志、应用后 import，`setup_logging()` 天然后写覆盖，无此问题。采集侧须容忍非 JSON 行（`jq -R 'fromjson? // empty'`）。

* **日志级别**（#404）：`LOG_LEVEL`（→ `settings.log_level`）优先，空则由 `debug` 推导（True→DEBUG / False→INFO）。`sqlalchemy.engine` / `sqlalchemy.engine.Engine` **不钉级别、但显式清 handlers**（#417）——SQL 详略已由 `database.py` 的 `echo=settings.debug` 控制，dictConfig 再钉级别会与 echo 争用；但 echo 生效时 SQLAlchemy 在 engine 创建时给 `sqlalchemy.engine.Engine` 自挂明文 StreamHandler（propagate 不变），每条 SQL 明文 + JSON 双写、多行 DDL 在明文侧撕行，故 dictConfig 清掉已挂 handler（依赖 `main.py` 顺序：engine 先建、`setup_logging()` 后跑）。`uvicorn.access` 关掉（访问日志由中间件单点产出，否则每请求两行），`uvicorn` / `uvicorn.error` 改为冒泡到 root，与应用日志同为 JSON。

* **请求上下文**（#404）：`RequestContextMiddleware`（`app/request_context.py`）沿用入站 `X-Request-ID` 或生成 uuid4 hex，注入同名响应头，每请求产一条访问日志（`method` / `path` / `status_code` / `duration_ms`；**只记 path 不记 query**，查询串可能带凭据），`/health` 跳过（探活会淹没真实流量）。入站值先过正则白名单才回写响应头/进日志（防响应头注入）。actor / client_ip 由 `dependencies.py::get_current_user` 写入。**必须是「中间件派发前创建的可变对象 + 依赖改字段」，不能在依赖里 `var.set(...)`**：FastAPI 把同步依赖与同步 endpoint 分别派发到 threadpool，anyio 每次派发都 `copy_context()` 出独立拷贝，依赖里的 `set` 只作用于依赖那份拷贝，endpoint / service 读到的仍是 None（实测）。

* **未预期异常**（#404）：`main.py` 的 `Exception` handler 记 ERROR（带堆栈）并返回 500。注意 Starlette 把 `Exception` handler 装在 `ServerErrorMiddleware`——中间件链的**最外层**：它执行时本中间件的 `finally` 已解绑上下文（故 request_id 从 `scope[SCOPE_REQUEST_ID_KEY]` 取回），响应经**原始** `send` 发出（故 `X-Request-ID` 头要 handler 自己补），且调完 handler **仍会重抛**（故测试须用 `TestClient(app, raise_server_exceptions=False)`）；中间件没见过 `response.start`，访问日志按 500 记。**path 口径（#422c）**：handler 的 stdout ERROR 行与传给 `record_system_error` 的 `request_path` 都是**完整** path，列宽截断只在写入侧（`SYSTEM_ERROR_PATH_MAX`）单点做——超长路径不至于让整条错误记录写不进去，也不丢取证信息。
  - **同一 handler 另落一条 `system_error_log`**（#405）：actor / client\_ip 同样只能从 scope 取回（`SCOPE_ACTOR_KEY` / `SCOPE_CLIENT_IP_KEY`，由 `dependencies.py::get_current_user` 认证成功后与 contextvar 一并暂存）；落库经 `run_in_threadpool` 挪出事件循环（handler 是 async，DB I/O 会阻塞其他请求）；`request_params` **刻意不记**——查询串可能带凭据，与访问日志只记 path 同口径。

* 服务版本：`FastAPI(version=…)` 取仓库根 `VERSION` 文件（`app.main._resolve_version`，`APP_VERSION` 环境变量优先）；版本变更必须同 commit 重导出 `openapi.json`（`check_openapi.py` 全量比对含 `info.version`），发布流程见 `docs/reference/versioning.md`。

* 调度：`scheduler_enabled`；两条独立每日 job——`daily_nav_sync`（净值同步+分红检测）与 `daily_snapshot_generate`（快照生成，#156），各持 MySQL `GET_LOCK` 互斥锁，cron 分别取 `scheduler_cron_daily` / `scheduler_cron_snapshot`；自动快照仅处理 `auto_snapshot_enabled=True` 的活跃组合（组合级开关默认 False，opt-in，只约束自动任务，手动生成/重算端点不受影响）。`init_tasks.py` 确保任务记录存在并同步文案，但不覆盖已有 cron\_expr。**自动运行同样落 `TaskExecutionLog`**（#406，`trigger_type="scheduled"`，与手动共用 `task_runner.run_task`，见 §1.3「任务执行记录」）；两处闸门收敛在 `_should_run_today`（GET\_LOCK 互斥 → 交易日判断），**锁被占与非交易日两类跳过不落执行记录**。

* 数据源：Tushare / AkShare，`data_sources` 路由读写 `.env`；安全：登录失败锁定、Token 过期/黑名单、改密后强制重登（参数明细见 `config.py`）。

***

## 2. 跑测试

```bash
cd backend && pytest tests -q
```

- **本地默认跑影响面子集**：全量耗时长，全量回归由 CI 兜底（合入前 `CI OK` 强制）。按改动文件圈定，如 `pytest tests/test_snapshot_service.py -q -x` 或 `pytest tests -q -k snapshot`；影响面拿不准就宁宽勿窄。上面整条命令留给怀疑大改动或合入前自检。
- **影响面圈定程序**（改动后按改动区域对照下表圈定子集，多区域取并集；表外区域按 `-k <领域词>` 就近圈定）：

  | 改动区域 | 最小子集 |
  | --- | --- |
  | `snapshot_service.py`（生成/重算/级联回退） | `pytest tests/unit/test_snapshot_service.py tests/integration -q -k snapshot` |
  | `position_service.py`（可用现金/份额） | `pytest tests/unit/test_position_service.py tests/integration -q -k "position or in_transit or cash"` |
  | `trade_service.py` / 调仓交易路由 | `pytest tests/integration/test_trades.py tests/integration/test_trade_cash_check.py -q` |
  | `subscription_service.py`（申赎） | `pytest tests/integration/test_subscriptions.py -q` |
  | 份额变动事件 | `pytest tests/integration -q -k "share_event or event_window or forced_adjustment"` |
  | 金额/份额量化 | `pytest tests/unit/test_quantize.py tests/integration -q -k precision` |
  | 分层红线（service 事务/异常约定） | `pytest tests/unit/test_service_no_commit.py -q` |
  | 任务执行记录（`task_runner.run_task` / `scheduler_service` / `routers/tasks.py`） | `pytest tests/unit/test_task_log_orchestration.py tests/unit/test_scheduler_service.py tests/unit/test_run_nav_sync.py tests/integration/test_task_execution_log.py tests/integration/test_tasks.py tests/integration/test_log_cleanup.py -q` |
  | 审计/系统错误日志（`audit_service.py`、任一埋点、日志表迁移） | `pytest tests/unit/test_audit_service.py tests/integration/test_audit_log.py tests/unit/test_migration_0013.py tests/unit/test_migration_0014.py -q` |
  | 字符集 / 建表（`db_charset.py`、`models/base.py`、迁移 0015、`ci.yml` 建库语句） | `pytest tests/unit/test_db_charset.py tests/unit/test_migration_0015.py tests/unit/test_migration_0014.py -q` |
  | 外键约束 / 外键名（`models/nav_sync_detail.py` 的 `name=`、迁移 0016） | `pytest tests/unit/test_migration_0016.py tests/unit/test_migration_0015.py -q` |

  跨核心服务的改动（snapshot/position/trade/subscription 任一）额外连带 `-k snapshot` 兜底——快照链是所有写路径的下游。
- **测试库优先级**（`tests/conftest.py::_load_test_db_url`）：env `TEST_DB_URL` > `backend/.env.test`（gitignored，按需配置本地/远程 MySQL）> 降级 `sqlite:///./test_investring.db`。CI 的 SQLite job 不设 `TEST_DB_URL`（也不存在 .env.test），MySQL job 显式设置。
- **会话开始 `drop_all + create_all`**（干净起跑）；会话结束**不清理**——跑完可直接登录本地前端浏览种子数据。
- fixture 层级：session（`test_engine`、`_seed_base_data`）→ autouse（认证全局状态隔离）→ function（`test_db`/`client`/`admin_headers`/`sample_portfolio` 等），业务数据一律用 function 级 fixture/factories 造，不动 session 种子。
- pytest 配置在 `pyproject.toml`（`--strict-markers`），新增 marker 须登记。
- **覆盖率（#254 设防期，#171 观察期已结束）**：本地跑测试默认**不收集**覆盖率（不传 `--cov` 即零开销）；查看口径用 `pytest tests/ -q --cov=app --cov-report=term-missing`（带分支列与缺失行号）。口径与阈值配置在 `pyproject.toml [tool.coverage.*]`：`branch=true`（分支含口径，line+branch 合并计总覆盖率）+ `fail_under=82`（2026-09-08 实测基线 82.15% 下取整，#405 审计埋点后上调）。CI backend-test job 带 `--cov` 运行，跌破阈值即门禁失败。
  - **棘轮规则**：`fail_under` 只升不降；任何 PR 全量实测总覆盖率超当前阈值 ≥1pp 时，顺手把阈值上调到实测值下取整（随该 PR 提交）；分支覆盖不单设独立阈值（branch=true 下 fail_under 已是分支含口径）。
  - 注意 fail_under 作用于 `--cov` 收集的那次运行：本地跑**子集**加 `--cov` 必然跌破阈值（子集覆盖不了全量代码），属预期，阈值只对全量运行有语义。

## 3. 种子数据（单一事实来源）

`tests/seed_base.py` 提供两个入口，改种子只改这一处：

* **`seed_base_data(db)`**——基础数据：维度字典、适用关系、4 平台、9 产品（3 虚拟 + 6 业务）、2025-2026 工作日日历、draft 组合 `E2E_PORT`（零交易零快照）、ADMIN/admin@2026、VIEWER/viewer123。**三处消费**：pytest（conftest `_seed_base_data`）、CI E2E（`scripts/seed_e2e.py`）、本地 E2E（`scripts/run_e2e_backend.py`）。
* **`seed_e2e_active(db)`**——E2E 专属活跃组合 `E2E_ACTIVE`（#354）。**仅两处 E2E 脚本在 `seed_base_data` 之后调用，不进 pytest session 种子**：其业务交易/申购/价格数据会泄漏进按「全局账本」精确断言的存量后端测试（`test_filter_by_status`/`test_sort_apply_date_desc`/`test_trades` 系列/价格 upsert 计数），且后端 pytest 本就不需要它。

**前端 E2E 依赖两个组合的形态契约**（spec 经 `frontend/e2e/helpers.ts` 按 code 直达，缺组合或形态退化会硬失败，不再优雅 skip），勿删勿改形态：

* `E2E_PORT`：draft、零交易/申赎/快照——platform-select-search、trade-buy-amount-linkage（用例 7 依赖零快照的 1.0000 首购窗口）等 spec 的目标。
* `E2E_ACTIVE`：动态日期编排——锚定 `date.today()` 回溯 4 个交易日 D1<D2<D3<D4：D1 申购（ADMIN/HBZQ，10 万）→ D2 确认（首窗净值 1.0000、组合转 active）+ 场内买入 510300.SH 并确认（成交价 4.0000，不依赖行情同步）+ 首快照（首快照日 == 最早 confirm_date，#180）→ D3 第二快照（价 4.2000，连续原则）→ D4 pending 场内买入（8,200 元，供编辑类用例；trade_date > 最新快照日且落在前端「近1年」默认过滤窗内，故必须动态日期）。业务数据走 service 层造（复用 CASH 配对腿/激活状态机/快照三表全部不变量），仅 Portfolio/PriceRecord 直接 ORM；整段单事务末尾一次 commit，幂等守卫 =「组合已存在则跳过」。**create_trade 后必须 `db.flush()` 再 confirm**——否则基金腿 id=None，`sync_transfer_group` 定位不到配对 CASH 腿，CASH 腿滞留 pending 使快照预校验失败。**禁止对 `E2E_ACTIVE` 跑 recalculate/catch-up/generate-next**：auto_confirm 会确认 D4 pending 交易，破坏编辑契约。

日历**未随本次扩展**（仍止 2026-12-31）：`test_trading_day`/`test_snapshot_service` 用 2027、2030 作「未同步」哨兵日期，扩展日历即打破它们，属另一耦合议题。`seed_e2e_active` 的动态日期依赖日历覆盖 today 及前 4 个交易日，越界抛 `RuntimeError` 响亮失败（非静默）——临近 2026 年底需一并处理日历终点与哨兵测试。

契约由 `tests/integration/test_seed_contract.py` 锁死（纯查询断言，E2E_ACTIVE 用 function 级 fixture 现造），改种子形态先改契约测试。

## 4. 本地启动

```bash
cd backend && uvicorn app.main:app --reload   # 配置见 .env.example
```

启动时序：import 期 `create_all` → lifespan 内 `alembic upgrade head`。**alembic 不能单独从零建库**（迁移依赖 create_all 先建表，如 0007 直接 UPDATE product）。

## 5. 迁移（alembic）

- 新迁移必须提供可逆 downgrade；CI（backend-test-mysql）对最新一条迁移做 downgrade/upgrade 往返验证；确不可逆时 PR 设 `SKIP_DOWNGRADE` 豁免、合入后移除。
- **采纳型迁移**（把早已存在的事实纳入管理，`upgrade()` 对已存在的库近乎 no-op）的 **downgrade 刻意 no-op、且不得 `raise`**：**0013**（四张日志表纳入 alembic）与 **0016**（`nav_sync_detail.job_id` 外键去重收敛）。no-op 的逆操作本身无物可还原，`raise NotImplementedError` 还会把 `ci.yml` 的 `alembic downgrade -1` 弄红。理由逐条写在各自 `downgrade()` 的注释里。
- 现有不可逆迁移：**0006、0008**（含 DROP 列），回滚只能靠备份。
- **部分条件下不可逆**：**0014**（日志/同步明细表 utf8mb3→utf8mb4）与 **0015**（全库 utf8mb4）的 downgrade 在目标表已含 4 字节字符时跳过该表并打 WARNING（反向转码必然失败，跳过优于半途而废），故对空库/无 4 字节数据的库仍是完整可逆迁移——CI 往返可过、不需要豁免。0015 的关系是 0014 → 0015（`down_revision = '0014'`）：0014 只转五张日志/同步明细表，0015 把库级默认与其余表一次转净，**0014 已在生产执行过、刻意不改**。
- 种子类 DML 写迁移时双方言（SQLite/MySQL）都要过——迁移文件头注释写清幂等设计。

## 6. 依赖

- `requirements.txt` 是镜像与 CI 的**唯一安装来源**（`Dockerfile:30-32` + `ci.yml` 四个 job 均裸 `pip install -r requirements.txt`）；`pyproject.toml` 的 `dependencies` 全是 `>=` 下界，**不参与构建**，改它不改变任何安装结果。加依赖须更新 requirements.txt。
- **传递依赖不显式钉版就等于没钉**：未出现在 requirements.txt 的包，版本由构建时解析决定、仓库零记录。已钉：`click`（uvicorn 传递）、`starlette`（fastapi 传递，#314）。干净环境实测解析 79 个包、requirements.txt 仅声明 30 个，**其余 50 个传递依赖仍浮动**（清单见 issue #314；含 `anyio`、`typing_extensions`、`pydantic_core`、`cryptography`、`greenlet`、`h11`/`httptools`/`websockets` 等可能跨大版本者）。
- **本地 ≠ CI**：pip 不升级已满足下界的已装包 → 同一份 requirements.txt 在本地可能是旧版、干净环境解析成新版；排查版本相关现象先 `pip show <pkg>` 对齐。
- `pip-audit`（`security-scan.yml`）按**声明**解析，抓不到未声明的传递依赖，别当锁文件用。

## 7. E2E 相关脚本

- `scripts/run_e2e_backend.py`：本地 E2E 后端（SQLite 临时库，每次启动重建 + 自动种子，空 lifespan 跳迁移）。默认 `/tmp/ir_e2e.db` + `:8000`，可用 **`E2E_DB_PATH` / `E2E_PORT`** 覆盖（并行会话/隔离栈复用）。
- `scripts/seed_e2e.py`：CI E2E 种子入口，**未设 `DATABASE_URL` 直接拒绝**（防误连）。
