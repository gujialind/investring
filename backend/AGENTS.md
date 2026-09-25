# backend/AGENTS.md — 后端模块指南

> 先按[根指南](../AGENTS.md#2-按任务加载)定位任务；改 services/routers 前读[业务规则](../docs/reference/business-constraints.md)的对应领域与错误码。日志改动读[日志规范](../docs/reference/logging.md)，文档改动读[AI 文档规范](../docs/reference/documentation.md)。本文件持有后端分层、事务、迁移兼容与操作边界。

## 1. 架构

### 1.1 分层目录与职责

| 目录                                                  | 职责                                                                                          |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `app/routers/`                                      | HTTP 薄适配层：解析参数、鉴权（`Depends`）、调 service、`db.commit()`、序列化；业务错误交全局 handler，不写 try/except 业务分支 |
| `app/services/`                                     | 全部业务规则/不变量/计算/状态机/ORM 读写；**只抛领域异常、不 import fastapi、不 commit（可 flush）**                      |
| `app/models/`                                       | SQLAlchemy 表模型                                                                              |
| `app/schemas/`                                      | Pydantic 请求/响应模型                                                                            |
| `app/constants/`                                    | 纯数据常量的单一事实来源：`asset_dimensions.py`（五维字典种子）、`audit_actions.py`（审计 action / resource\_type 枚举，列宽由 `tests/unit/test_audit_service.py` 守门）、`db_charset.py`（**全库** charset/collate，`Base.metadata` 钩子与迁移 0015 共用，由 `tests/unit/test_db_charset.py` 守门）、`log_charset.py`（#427 的历史快照：迁移 0014 的纳管清单 `CHARSET_TABLES`，只读勿增删）；埋点处禁止写字面量 |
| `app/utils/`                                        | 安全（密码/Token/登录锁）、数值量化（`quantize.py`）、MySQL 1213 有界重试（`db_retry.py`，#618）等工具                    |
| `app/config.py` / `database.py` / `dependencies.py` | 配置、DB 会话、鉴权依赖（**声明式 `Base` 不在 `database.py` 里**——见 `app/models/base.py`，字符集钩子与之同址，§1.4）                                                                               |
| `app/logging_config.py` / `context.py` / `request_context.py` | 集中日志配置（stdout 单行 JSON）、请求级上下文（request_id / actor / client_ip）、请求上下文中间件（#404，约定与踩坑见 §1.5）    |

**分层约定（router 为 service 薄适配器）**：业务逻辑单一实现于 service，REST 共用，杜绝并行实现漂移。

* **事务边界属于 session 拥有者**：service 收到调用方注入的 session，不 `commit`/`rollback`（可 `flush`）；REST 在 router `db.commit()`（部分失败语义的端点如 recalculate 按 errors 决定 rollback/commit）。**合理例外**：自持 `SessionLocal` 的后台执行体（sync job 线程、scheduler 触发体）与 `task_runner` 的 checkpoint 提交（逐日快照回补、逐产品远程同步，需保留部分成功；**含任务执行记录的多段 commit**——`running` 先落库、终态再落库，见[任务执行规范](../docs/reference/logging.md#logging-tasks)）可自行 commit。

* **领域异常统一**：service 抛 `app/services/exceptions.py::BusinessError`（携 `code`/`message`/`http_status`/`details`）；`main.py` 全局 handler 映射为 `JSONResponse{"detail": {"error": code, "message": message}}`（保持前端契约；默认 422、重复创建类 400、NOT\_FOUND 404）。service 内**禁止** import/抛 `HTTPException`。

守门见 [test_service_no_commit.py](tests/unit/test_service_no_commit.py)：递归 AST 检查服务的 HTTPException 依赖（含别名、模块限定和嵌套导入）；既有普通 service 动态用例同时禁止注入会话 commit/rollback，保留 savepoint。**任务投递入口**（`submit_price_sync_job`、`submit_snapshot_recalc_job`）方向相反——它**必须** commit（后台线程另开会话按 job_id 取任务，只 flush 会丢任务），正因如此一律自持 `SessionLocal`、**不接受注入会话**（注入形态下的 commit 会连带提交调用方未提交的写入且 rollback 撤不回，#592 方案 A）；`test_service_no_commit.py::TestTaskSubmissionSessionOwnership` 同时钉住签名（不得重新出现 db/session 形参）、跨会话可见性与投递失败不留 pending 孤儿三条。

### 1.2 路由与 API 前缀

端点以 `app/main.py` 注册为准；CLI 机读契约见 `ir schema`。

> 前缀约定：所有资源挂 `/api/<资源名>`（如 `/api/snapshots/...`）；`cash_transfers` 作为 `portfolios` 子资源挂 `/api/portfolios/{code}/cash-transfer`；日志/任务/通知/数据源在 `/api/system/*` 二级命名空间。

**响应模型守门**：返回 JSON 的端点必须声明 `response_model`——ORM 行不加收窄会全列直吐（#487）。未声明者逐条登记在 `tests/integration/test_response_model_guard.py` 的白名单（附理由、只减不增），并与 `backend/openapi.json` 两层互证（扫描操作全集；「`200` 带 `application/json` 媒体类型但 schema 为空壳」集合）；「可空列 ↔ 响应字段不接受 None」的静态核对与例外台账由 `tests/unit/test_response_model_nullability.py` 守门（`service_guard` 条目按字段登记守门模块，测试实调守门 + 更新路径调用点 AST 复核；未修缺口以 `known_gap` 登记并挂 follow-up issue 号）。**显式 null 收口（#579 起全仓口径统一）**：全部更新路径已接入 `null_guard.reject_explicit_nulls`——投资人 / 产品 / 资产分类 / 申赎 / 调仓 / 份额事件六个服务（#573/#579）与 `platforms` / `data_sources` / `portfolios` 三个路由（#579），显式传 null 一律 422 `INVALID_PARAM`，不要为新字段放行 null；清除型字段在调用点用 `allow` 显式声明，口径 = 列可空且响应 Optional（如 `platforms.platform_type`、`portfolio.description`、`trade.notes`），或属既定清空语义 / 专用校验器字段（`portfolio.display_config` 的 null / `{}` 是清空，见 §1.4；`trade.cash_confirm_date` 走 #493 专用拒绝）。收口位置保持在专用状态码之后（调仓的状态守卫、份额事件的 confirmed 阻断优先），不再存在「null = 未提供」端点。新增更新端点/字段必须显式接入本收口，不要据本节推测已全覆盖——见[易错陷阱](../docs/reference/business-constraints.md#易错陷阱)。

### 1.3 核心服务

业务核心集中在四个服务模块（函数级细节读源码）：

<a id="backend-auto-confirm"></a>

* **`snapshot_service.py`**：业务正文见[快照](../docs/reference/business-constraints.md#rule-snapshot)与[现金账本](../docs/reference/business-constraints.md#rule-cash)。入口为 `generate_daily_snapshots`、`_delete_existing_snapshots`、`recalculate_snapshots`、`auto_confirm_before_snapshot`、`auto_confirm_after_snapshot`。
  - 单日生成的连续性校验（`SNAPSHOT_NOT_CONTINUOUS`）在**重算路径逐日重建时内部 bypass**；批量删除从最新日**倒序、逐日 commit**。
  - **并发生成会撞 MySQL 1213，`POST /snapshots/generate` 由有界重试吸收**（#618）：生成走「先删后插」，REPEATABLE READ 下那条**命中 0 行**的 DELETE 会在唯一索引上留下 gap lock，而 gap 锁彼此兼容——两个并发事务同时持有同一段间隙后，各自的 INSERT 要 insert intention lock，与对方的 gap 锁互斥 → 循环等待 → InnoDB 报 1213 并整体回滚一方。**争的是同一段索引间隙而非同一行，故按组合加 `GET_LOCK` 治不了它**（实测两侧各持一把不同名的组合锁，5/5 仍死锁）；READ-COMMITTED 下 gap 锁基本关闭，同样写序列 5/5 不死锁，所以生产 RDS（实例级 `READ-COMMITTED`）不显形、CI 的 `mysql:8.4`（默认 RR）必现。处置取 `app/utils/db_retry.py::retry_on_deadlock`——**只认 errno 1213**（1205 等锁超时消息里同样写着 `try restarting transaction`，但重试只会加倍负载，刻意排除），整体回滚后重放**含 commit 的整个工作单元**（死锁可能在 service 的 `flush()` 处、也可能在 `commit()` 处才暴露），默认 3 次尝试、递增小退避，每次重放留一条 WARNING。重试层不改响应契约：预算耗尽仍走该端点既有的 `except Exception` → `report_unexpected` → 500 `SNAPSHOT_GENERATION_FAILED`。**其余快照写端点（recalculate / catch-up / generate-next / 调度）刻意未接**——它们各有部分成功语义（`200 + errors`、逐日 commit），套用重试要先逐条厘清事务边界，而现场证据只覆盖 generate；helper 已就位，出现证据再接。死锁详情与全部对照实验见 issue #618。
  - 级联回退（`_cascade_unconfirm_*`）的日期键：申赎按 `apply_date == D`、事件按 `entitlement_date == D`（`ex_date == D` 但 `entitlement_date < D` 的事件基数快照仍在，保持 confirmed、重建时被重新应用）；**交易不级联**——其取价依赖产品行情而非组合快照。
  - `auto_confirm_before_snapshot`（#495）在 `generate_daily_snapshots` 依赖校验前运行，日期键：`latest_snapshot_date < confirm_date <= target_date`（仅申赎；须已有快照基线）。消化当天补录单（申请日 == 最新快照日、确认日 == 目标日），否则被 pending 校验阻断且生成后 auto_confirm 救不到（死锁）。`auto_confirm_after_snapshot(D)` 日期键：申赎 `apply_date <= D`；事件 `ex_date == 下一交易日(D)`（仅父/独立记录）；跨天转移走 `cross_day_pending`，排除 rebal_ 且置 confirmed 前拒绝非 CASH 腿。调仓不自动确认的理由与阻断规则见[调仓正文](../docs/reference/business-constraints.md#rule-trade)。
  - pending 事件预校验使用 `ex_date <= target_date`，失败返回 failed。
  - 取价实现为生成与预校验**共用同一函数**；`MISSING_NAV` 错误信息按 `[T=…]` / `[T-N=…]` 规则分组。
  - **可观测性**（#305，savepoint 机制修正 #419）：重算 / catch-up / generate-next / 调度路径的响应（调度为任务日志）携带逐日 `auto_confirmed` 与 `warnings`，逐日错误条目含 `code`/`details`；auto_confirm 循环单条 DB 级失败经 **session 级 savepoint**（`db.begin_nested()`）隔离，不毒化 session、循环继续——**连接级 savepoint 隔离不了 ORM flush 失败**：SQLAlchemy 会回滚 session 持有的整个 DBAPI 事务、把根因挂到父 `SessionTransaction._rollback_exception`（读法 `session._transaction._rollback_exception`），外层已 flush 的成果当场清零，此后任何会话操作都抛 `PendingRollbackError`（#419 实测）。savepoint 必须用**显式** `sp = db.begin_nested()` / `sp.commit()` / `sp.rollback()`，**不能用 `with`**：flush 失败时 SQLAlchemy 已自行回滚并关闭该 nested 事务，但 `with` 期间它仍是 session 的 `_trans_context_manager`，conftest 的 `after_transaction_end` 监听器补 savepoint 时会撞 `InvalidRequestError: Can't operate on closed transaction inside context manager`，把真实根因整个掩盖（#419 实测，7 个用例受影响）；显式形式下只在 `db.get_nested_transaction() is sp` 时才回滚（否则 SQLAlchemy 已回滚过，重复回滚抛 `InvalidRequestError`），回滚自身失败则响亮记 ERROR 并按下方 `SESSION_ABORTED` 终止。session 不可恢复时（连接级失效、`PendingRollbackError`、savepoint 回滚失败、失败后 `Session.is_active` 为假）记**一条** `SESSION_ABORTED` 根因后终止本段，且**进入守护前先探测**可用性——否则每条都只会重抛同因错误。各路径收敛方式：重算逐日循环见到 `SESSION_ABORTED` 即 break 并把根因写进 `results[].errors`（router 整体 rollback，响应仍 `200 + errors`）；catch-up 与调度路径在随后的 `db.commit()` 抛出后走各自 rollback + 记录（根因条目已在 `auto_confirmed` / `auto_confirm_failed` 里）；generate-next 无 errors 契约，冒泡为 500。

* **[position_service.py](app/services/position_service.py)**：业务正文见[现金账本](../docs/reference/business-constraints.md#rule-cash)与[可用量](../docs/reference/business-constraints.md#可用量口径)。`calculate_available_cash(as_of_date=...)` 为空时不设上限，基线只取 CASH、排除在途；`compute_cash_balance` 的全量 confirmed 口径只用于手动重估审计 computed_value（get_cash_value 无生产调用方），不可拿来作可用现金降级基线。无快照不能恢复“全量基线 + 1970 哨兵增量”的双计账（#515），守门见 [test_position_service.py](tests/unit/test_position_service.py) 的 `TestNoSnapshotCashCountedOnce`。

* **`trade_service.py`**：完整操作矩阵见[调仓](../docs/reference/business-constraints.md#rule-trade)与[生命周期](../docs/reference/business-constraints.md#rule-lifecycle)，不以两腿同状态假设替代规则。
  - `sync_transfer_group` 是同步单点；编辑日期 `propagate_dates=True` 时买入扣款跟随 T，不传播 confirm_date、不制造 pending 调仓 CASH 腿。组号须在 flush 前分配；确认的 `validate_confirm_cash_leg` 在 setattr 前只读校验，实际写腿由 `_apply_confirm_cash_leg` 执行。
  - `compute_confirm_plan` 组合 `calculate_confirm_preview` 与 `resolve_cash_leg_plan`，preview/confirm 共用；`validate_group_snapshot_free(extra_dates=...)` 供改删回退复用，现金加回由 `validate_buy_cash_with_addback` 单点处理。
  - `build_paired_cash_leg_map` 按组合/组号批量取反向 CASH 腿，不得受列表 status/platform/分页筛选截断，读侧字段由它派生。
  - `list_cash_transfers` 的跨天判据以 buy 腿为准（未 confirmed 或 confirm_date > trade_date）；`confirm_cash_transfer` 确认组内仍 pending 的 CASH 腿，验资时点取 transfer_date。

* **`subscription_service.py`**：定价见[申赎](../docs/reference/business-constraints.md#rule-subscription)，激活见[组合管理](../docs/reference/business-constraints.md#rule-portfolio)。unconfirm 重算期望 confirm_date 而非置 None，防 SQL NULL 比较漏过 pending 检查。
  - 不恢复申购 unconfirm 前的现金守卫（#203：曾阻断快照级联产生孤儿），消费点防线见[生命周期](../docs/reference/business-constraints.md#rule-lifecycle)；存量负现金经 status 的 negative_cash_platforms 暴露。

其余模块中需记住的设计点：`snapshot_recalc_job.py`（#89 异步重算：复用 sync\_job 表 + 线程池，同类型单 active 锁，终态经 `GET /api/sync-jobs/{id}` 轮询）；`product_service.py::calculate_confirm_days` 为确认天数单一实现；`cumulative_profit_service.py`（#598 三粒度累计收益，只读、无接口/页面接入，公式见[累计收益](../docs/reference/business-constraints.md#rule-cumulative-profit)，与持仓列表旧 `profit_loss` 并存不替换）。其他服务职责读各文件 docstring。

* **精度入口 [quantize.py](app/utils/quantize.py)**：规则与产生点统一见[数值口径](../docs/reference/business-constraints.md#rule-precision)。守门为 `test_quantize.py::TestQuantizeNav` / `TestAmountToSharesTwoStepQuantization`、`test_snapshot_service.py::TestValueSnapshotFourDecimalRounding` 与 `test_trades_validation_preview.py::TestTradePreview` 的四位边界用例；它们区分 HALF_UP 与缺省 HALF_EVEN，不能换成非边界数字。`TestFinancialQuantizationGuard` 在六个核心财务模块默认禁止直接 `.quantize()` 与 `round()`——含属性形式（`builtins.round()`、其别名、`np.round()`、`Series.round()`，不白名单接收者）、模块级调用与嵌套/异步函数（#590 收紧：新增未登记函数直接舍入即失败，不再依赖「登记产生点」圈定禁止范围）；读侧统计舍入（float 序列化展示）仅按「文件 + 限定函数」登记豁免（例外清单以 `test_quantize.py::_FINANCIAL_ROUND_EXCEPTIONS` 台账为准，代码用字面相等断言钉死），豁免限**函数体**及其嵌套函数——装饰器与默认值在定义处的外层作用求值、不在豁免内，豁免也不扩大为整文件、不豁免 `.quantize()`；例外函数更名/迁移或其子树内不再有直接豁免调用时按过期例外失败，父与嵌套子同时登记按无效台账失败（内层恒被外层遮蔽），空扫描不得通过。**不覆盖的形态**（AST `Call` 匹配的固有逃逸面，由 `test_known_escape_forms_are_not_covered` 钉成可见契约、扩展守卫时该用例翻红迫使有意识更新）：`functools.partial(round, …)`、`map(round, xs)`、f-string `f"{v:.2f}"`。ORM 成本价保持 Decimal，理由见[审计载荷](../docs/reference/logging.md#logging-audit)。

* **份额变动事件：计算单点、预览复用**（#424/#425）：变动量计算是纯函数 `share_change_event_service.compute_event_fields(event_type, entitlement_shares, *, …)`（返回 `EventFieldResult`，**不碰 ORM 对象**），确认与确认预览共用——「预览 == 确认」由此保证。
  - **写回刻意分离**：`apply_event_fields(event)` 是唯一写回点、**只由确认路径调用**。预览不得复用它：`record_audit` 的 `_diff_fields` 读事件当前字段，对象被预览改过后列表行会显示未落库的值；pending 对象若被 flush 还会把「预览」写进库。
  - **`forced_adjustment` 的两项语义不得「顺手修正」**：`shares_change` / `cash_change` 为空时**保持 None、不折成 0**（快照的事件应用循环以 `event.shares_change is None` 判定「纯现金调整」并跳过份额段，折成 0 会让对 CASH 产品的合法纯现金调整被现金行守卫误杀为 `POSITION_NOT_FOUND`）；`shares_after` 恒不写回（用户直填的「调整后余额」，unconfirm 同样不清空），预览照实回 `shares_after=None`。
  - **前置校验同点**（#460 评审）：状态门 + #279 双校验抽为 `_validate_confirm_preconditions(db, event)` 公共 preamble，确认与预览各调一次——前置拒绝同序、同码、同消息靠结构保证而非约定；权益登记日快照存在性探针（`MISSING_POSITION_SNAPSHOT`）内聚在 `resolve_entitlement_shares` / `_confirm_fund_level_event` 的未命中分支（`_require_entitlement_snapshot` 惰性探针，命中零额外查询），基金级「无持仓」拒绝文案两侧共用常量 `MSG_FUND_LEVEL_NO_HOLDINGS`。
  - **权益份额口径同点**：`resolve_entitlement_shares(db, event)` 供确认与预览共用（平台级按 `(market, platform_code)` 过滤、基金级取 `event.market` 下各平台 `shares > 0` 之和、`forced_adjustment` 另做 `(产品, market, 平台)` 精查 → `POSITION_NOT_FOUND`）；`_confirm_fund_level_event` 拆子记录与 `check_platform_coverage` 平台覆盖校验同以 `event.market` 为边界（#461：LOF 一码多市场不得跨市场聚合/摊派），LOF 两市场须分别录入事件。
  - `GET /api/share-change-events/{id}/preview` 只读、**pending-only**（`INVALID_STATUS`，与 confirm 同码同消息；**对 confirmed 不服务**——前端无该消费方，与 trades/subscriptions 的 preview 口径一致）；**刻意不复刻创建期的 `check_platform_coverage`**——那是「录入完整性」规则而非状态门。（路由注册顺序不影响匹配：Starlette 的 `/{id}` 只匹配单段路径，`/{id}/preview` 两段路径不会被吞。）
  - 金额转份额的两步量化及 pending NULL 语义见[事件正文](../docs/reference/business-constraints.md#rule-event)，不要在预览另算一套。

* **审计与系统错误**只经 `audit_service.record_audit` / `record_system_error`，不 commit；Core INSERT + 连接级 savepoint 的理由、失败隔离与载荷规则统一见[审计规范](../docs/reference/logging.md#logging-audit)。不要套用 auto_confirm 的 ORM savepoint。
  - **`system_error_log` 的覆盖面含 router 兜底**（#553）：`HTTPException` 由中间件链内侧就地渲染、冒不到全局 `Exception` handler，故 `app/routers/**` 里「`except Exception` → 抛 5xx」的分支**必须自己留痕**——统一走 `app/error_reporting.py::report_unexpected(e, operation=...)`（ERROR 带原异常堆栈 + 落 `system_error_log`，`error_type` 取原始异常类名；`raise` / `db.rollback()` 仍归调用点，错误码与响应契约不变）。**只给 catch-all 用**：`BusinessError` / `ValueError` 走全局 handler 的 WARNING 口径。守门：`tests/unit/test_catchall_logging_guard.py`（AST 扫 `app/routers/**`，缺出口即判红并点名 `文件:行`）+ `tests/integration/test_router_catchall_observability.py`（行为）。
* **任务记录**只经 `task_runner.run_task`；这是分阶段提交的事务例外，session 归调用方、不 close。编排、跳过、NULL 语义与异常收口见[任务执行规范](../docs/reference/logging.md#logging-tasks)。

<a id="backend-models"></a>
### 1.4 数据模型与关键约束

表结构与全部唯一约束以 `app/models/` 为准。需记住的设计决策：

* `trade.transfer_group` **NOT NULL**（每笔 trade 必属一个业务组），唯一约束 `(transfer_group, product_code, trade_type)`：基金腿与 CASH 腿按 `product_code` 区分、现金转移两腿按 `trade_type` 区分、申赎为单腿 `sub_{id}`，故 NOT NULL 下仍无碰撞。

* `portfolio_position` 有 CHECK 约束：`shares` 与 `cash_amount` 二者恰有其一（净值型 vs 非净值型）。

* `share_change_event` 日期分工：`ex_date`（除息日，份额应用日）+ `entitlement_date`（权益登记日，基数日），要求 `ex_date > entitlement_date` 且均为交易日；可空 `cash_pay_date` 仅供现金分红到账（#522，允许非交易日），省略/清空与生效规则见[事件正文](../docs/reference/business-constraints.md#rule-event)。`parent_event_id` 为基金级拆分子记录自引用。

* 外键删除行为均为 **RESTRICT**，通过业务流程（关闭/停用）管理生命周期，保留历史数据。

* **外键的约束名以模型 `ForeignKey(name=...)` 为规范名**（#434）：MySQL **不做**「同表同列对指向同一目标」的重复外键去重，两条语义相同的约束会同时生效。历史事故正是两条路径各建一条并存在生产库 `nav_sync_detail.job_id` 上——`create_all` 建未命名 FK、MySQL 自动命名成 `nav_sync_detail_ibfk_2`，迁移 0001 又用 `op.create_foreign_key('fk_nav_sync_detail_job_id', …)` 显式建了一条；0001 里那句包着 `try/except: pass`（为兼容 `create_all` 已建表的新库），把「同语义约束已存在」吞掉、没暴露成错误，于是生产（先建表、后跑迁移的历史顺序）两条都留下了。**危害不在冗余本身，而在后续迁移的静默前提失真**：按显式名 `DROP FOREIGN KEY` 只删掉一条，另一条继续强制外键语义，迁移作者会在「外键已解除」的错误前提下改列。故**新加外键一律显式 `name=`**，且与任何迁移里 `create_foreign_key` 用的名字一致；迁移 0001 已在所有环境执行过、**刻意不改**（改它会让「跑过旧 0001 的库」与「跑过新 0001 的库」落到不同状态），存量收敛交给迁移 0016。
  - **迁移 0016**（`down_revision = '0015'`）把 `nav_sync_detail.job_id` 收敛到「恰好一条、名为 `fk_nav_sync_detail_job_id`」，三条分支：已是目标态 → 空转（全新库路径 `create_all` 已按模型显式名建出唯一一条）；两条并存（生产现状）→ **只多删**冗余那条、好的那条全程不碰；只有自动名 `*_ibfk_N`（#433 之前建的旧库）→ 拆掉重建为显式名（避免各环境外键名长期分叉，也让 `0001.downgrade()` 在这些库上可用）。**downgrade 刻意 no-op**（与 0013 同型）：逆操作是把语义完全相同的冗余约束加回来，不恢复任何功能、只会把同一个陷阱重新埋进库，且 `ci.yml` 的 `alembic downgrade -1` 是真实路径、不能 raise。两条外键的 `ON DELETE`/`ON UPDATE` 规则不一致时**响亮失败**（`RuntimeError`）、绝不静默挑一条——误删一条外键 = 静默丢掉一层约束语义。全库另有只读哨兵：发现**别的**列对也重复时只打 WARNING、不自动处置（同列对重复本身不足以判定该保留哪条）。守门：`tests/unit/test_migration_0016.py`（`TARGET_FK` 与模型 `name=` 绑死、迁移冻结不 import 模型、纯字符串 DDL、SQLite no-op、MySQL 上三种起始态各自收敛且越界插入仍被拒）。

* **虚拟产品**（#93/#522）：除 `CASH`（生产为部署期种子落库）外，迁移种子含 `IN_TRANSIT_BUY` / `IN_TRANSIT_SELL`（0006）及 `IN_TRANSIT_DIVIDEND`（#522），与 CASH 同构（`market=""`、`product_type="IN_TRANSIT"`、`confirm_days=0`）；以 `product_code` 区分买入/卖出/分红在途。维度标签（#128）：CASH 产品 `asset_class_code=ASSET_CASH`、其余四维 NULL；IN\_TRANSIT 五维全 NULL。计算与现金可用性见[现金账本](../docs/reference/business-constraints.md#rule-cash)。

* `is_qdii` 已降级为**纯展示标签**、`nav_lag_days` 逐产品自设（业务语义见[产品规则](../docs/reference/business-constraints.md#rule-product)）。**回填口径**：迁移 `0012` 仅把场外 QDII 的 `nav_lag_days` 置 1，**港互认基金需由界面/CLI 手工设为 1**——运行期不按产品类型自动映射。

* **资产分类五维度字典**（#128）：`asset_classification` 是正交维度值字典，五个维度 asset\_class（股票/债券/商品/现金，维持 4 类，REITs/另类按需再加）/region/style/size/segment（股票行业·债券期限·商品品种共用一维），产品以 5 个 FK 列挂维度值；字典种子单一事实来源为 `app/constants/asset_dimensions.py`（迁移与 `backend/tests/seed_base.py` 种子共用）。**维度值按需扩展（YAGNI），不为假想需求预留空值**；**asset\_class 的 `sort_order` 即前端饼图/分区色板序位，变更即改色**。分类信息仅读侧派生、快照表无分类列；前端二级分组默认股票→region、债券/商品→segment、现金平铺，可经组合级 `portfolio.display_config`（#144）按大类覆盖：JSON 列仅存显式覆盖项（NULL=默认），校验以 `asset_class_dimension_rule` 规则矩阵为准（无规则行的大类如现金不可配），大类一级分区不可变；PUT 显式传 null 或空对象 {} 清空（{} 归一为 NULL 入库）、不传不修改（哨兵区分，service 公开常量 UNSET）。

* **适用关系双层落库**（#135 矩阵落库）：运行期事实来源为 DB（常量为种子源），`validate_dimension_tags` 四层校验叠加、只收紧不放松——①存在性+dimension 匹配；②`is_active` 软失效（无物理删除；update 仅校验实际变化字段的新值，存量引用停用值不阻断其他编辑）；③维度级规则表 `asset_class_dimension_rule`（required/optional，**无行=forbidden，无规则行的大类=现金型全 forbidden**——新建大类配规则后运行期即可用，无需发版）；④值级关联表 `asset_dimension_applicability`（多对多，产品所选值必须关联其 asset\_class）。产品五维标签的「必填/禁止」语义由此两表驱动，不再硬编码。

* **四张日志表的 schema 事实来源是迁移 0013**（#405）：`audit_log` / `system_error_log` / `login_log` / `task_execution_log` 此前只由 `main.py` 的 `create_all` 建表，0001–0012 无对应 `create_table`——模型改列后生产库静默不跟随（schema drift）。0013 逐表 `has_table()` 守卫，**已存在则跳过并打 warning**（生产库这些表已由 create\_all 建出），故幂等；**downgrade 刻意 no-op**——采纳型迁移的逆操作不得删表：真实部署里这四张表与其数据都早于 0013 存在，而 `docs/runbooks/deploy-rollback.md` 的 `alembic downgrade` 是真实运维路径，删表 = 销毁审计/登录/任务历史；且 `task_execution_log` 被 `nav_sync_detail.task_log_id` 外键引用，MySQL 下 DROP 必失败（errno 3730，SQLite 不校验外键故本地测不出来）。`audit_log` 列宽是硬约束（`action` 20 / `resource_type` 50 / `investor_code` 20），改 `audit_actions.py` 的常量值先看宽度（`tests/unit/test_audit_service.py` 守门）。

* **全库字符集统一 utf8mb4**（#427 起、#433 完成，常量单一事实来源 `app/constants/db_charset.py`）：库级 charset 与排序规则是 `utf8mb4` / `utf8mb4_general_ci`（生产 RDS、CI 建库语句 `ci.yml` 两处 + `e2e-stack.yml` 一处、`docker-compose.dev.yml` 的 server 字符集三处对齐），连接侧 `app/config.py` 连接串同为 `charset=utf8mb4`。缺陷面是**两侧不一致**——utf8mb3 列容不下 **4 字节** UTF-8 字符（emoji、CJK 扩展 B；中文是 3 字节、utf8mb3 容得下），MySQL 严格模式下 errno 1366 `Incorrect string value` 让**整条**记录写不进去。失效形态分两档：`record_system_error` 是 best-effort，异常被吸收后**整条错误记录静默丢失**；`record_login_log`（`dependencies.py`）、`routers/tasks.py`、`nav_sync_detail`（`task_runner` / `market_data_service` 写外部数据源原文）的写入**不是** best-effort，会外抛 500、连带登录/任务执行/净值同步本身失败。

  - **两层落地缺一不可**。① **建表侧**：`app/models/base.py::Base` 挂在 `_CharsetBase` 上，其 `__init_subclass__` 给**每个**模型子类补 `mysql_charset` / `mysql_collate`（`setdefault` 语义，子类显式值优先），使 `create_all` 建出的表自带 `CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci`。**不是** `MetaData(mysql_charset=…)`——SQLAlchemy 2.0 的 `MetaData.__init__` 不接受方言参数（直接 `TypeError`）；**也不是** `MetaData` 的 `before_create` 事件——该事件只在 `create()`/`create_all()` 触发，`CreateTable(...).compile()` 不触发，DDL 与编译期检查会脱钩。**更不是**逐模型写 `__table_args__`：25 个模型漏一处就是一处静默的 utf8mb3 表。② **存量侧**：迁移 **0015** 逐表 `ALTER TABLE … CONVERT TO CHARACTER SET utf8mb4` + 库级 `ALTER DATABASE`（MySQL-only，SQLite 直接 return）。**0014** 是 #427 的中间态（只转五张日志/同步明细表），保留不改——它已在生产执行过，改它会让历史与仓库对不上。

  - **⚠️ 0015 的「先拆外键 → 转码 → 再建外键」不是可选优化**：MySQL 拒绝在表被外键引用时转码（errno 3780 `Referencing column … are incompatible`，实测生产 30 条外键涉及的 16 张表逐表直接转全部失败）。约束名**逐运行时从 `information_schema` 读**再原样重建，绝不硬编码——生产库外键名是 `fk_rule_class` / `fk_product_region_code` 这类描述性名字，测试库/全新库则由 `create_all` 生成 `*_ibfk_N`。**禁用 `DROP FOREIGN KEY IF EXISTS`**：MySQL 全系不支持（errno 1064），写了会让整批重建外键失败、表转码成功而外键全丢（实测踩过）。幂等：整库已是目标 charset 时直接空转（新库路径 `create_all` 已建 utf8mb4 → 0015 空转），非空转时总是重放「拆→转→建」以便中途失败后重跑补齐。

  - **downgrade 有条件跳过**（0014、0015 同判据）：反向转 utf8mb3 在表内已存在 4 字节字符时必然失败，会让 `docs/runbooks/deploy-rollback.md` 的 `alembic downgrade` 半途而废，故先按 `CONVERT(col USING utf8mb3) <> BINARY col` 逐列探测（utf8mb3 表示不了的字符转换后必然与原值不同；**刻意不用依赖 `CHAR_LENGTH` 字符数语义的 `LENGTH - 3*CHAR_LENGTH` 写法**——那套口径一旦被服务端按字节计就静默失效），有 4 字节字符则**跳过该表并打 WARNING**；探测本身抛错时按「无法证明可安全回退」处理、同样跳过——不假装成功、不吞数据，空库/无 4 字节数据的库仍完整可逆（CI 往返无需 `SKIP_DOWNGRADE`）。

  - **刻意排除「写入侧转义/替换」路线**：那要维护「哪些列要转义」的清单、遗漏面不可见，且改变取证文本原貌。守门：`tests/unit/test_db_charset.py`（逐模型断言建表 DDL 带 utf8mb4、钩子不覆盖显式值且不破坏 `__table_args__` 约束元组）与 `tests/unit/test_migration_0015.py`（外键 DDL 生成、入边表查找、MySQL 往返后外键定义与 4 字节数据不变、幂等空转）。

### 1.5 配置与运行

* 配置项以 `app/config.py` + `.env` 覆盖为准。

* **运行日志**由 `app/logging_config.py::setup_logging` 配置；JSON、级别、初始化先后、请求上下文与程序化 `log_config=None` 约束统一见[运行日志规范](../docs/reference/logging.md#logging-runtime)。修改入口时须保留 uvicorn 父进程明文这一已知例外。

* 服务版本：`FastAPI(version=…)` 取仓库根 `VERSION` 文件（`app.main._resolve_version`，`APP_VERSION` 环境变量优先）；版本变更必须同 commit 重导出 `openapi.json`（`check_openapi.py` 全量比对含 `info.version`），发布流程见 `docs/reference/versioning.md`。

* 调度：`scheduler_enabled`；两条独立每日 job——`daily_nav_sync`（净值同步+分红检测）与 `daily_snapshot_generate`（快照生成，#156），各持 MySQL `GET_LOCK` 互斥锁，cron 分别取 `scheduler_cron_daily` / `scheduler_cron_snapshot`；自动快照仅处理 `auto_snapshot_enabled=True` 的活跃组合（组合级开关默认 False，opt-in，只约束自动任务，手动生成/重算端点不受影响）。`init_tasks.py` 确保任务记录存在并同步文案，但不覆盖已有 cron\_expr。**自动运行同样落 `TaskExecutionLog`**（#406，`trigger_type="scheduled"`，与手动共用 `task_runner.run_task`，见[任务执行规范](../docs/reference/logging.md#logging-tasks)）；两处闸门收敛在 `_should_run_today`（GET\_LOCK 互斥 → 交易日判断），**锁被占与非交易日两类跳过不落执行记录**。

* 数据源：Tushare / AkShare，`data_sources` 路由读写 `.env`；安全：登录失败锁定、Token 过期/黑名单、改密后强制重登（参数明细见 `config.py`）。

***

## 2. 跑测试

```bash
cd backend && pytest tests -q
```

- **本地默认跑影响面子集**：全量耗时长，全量回归由 CI 兜底——PR 侧 CI 按路径裁剪 job，但**触碰的栈仍跑全量**（改后端即 SQLite + MySQL 两套全量，映射见 `.github/workflows/ci.yml` 的 `changes` job）；跨栈组合与纯时间流逝型失效由 push 侧保守全量与下一个触碰该栈的 PR 兜底。按改动文件圈定，如 `pytest tests/test_snapshot_service.py -q -x` 或 `pytest tests -q -k snapshot`；影响面拿不准就宁宽勿窄。上面整条命令留给怀疑大改动或合入前自检。
- **影响面圈定程序**（改动后按改动区域对照下表圈定子集，多区域取并集；表外区域按 `-k <领域词>` 就近圈定）：

  | 改动区域 | 最小子集 |
  | --- | --- |
  | `snapshot_service.py`（生成/重算/级联回退） | `pytest tests/unit/test_snapshot_service.py tests/integration -q -k snapshot` |
  | `position_service.py`（可用现金/份额） | `pytest tests/unit/test_position_service.py tests/integration -q -k "position or in_transit or cash"` |
  | `cumulative_profit_service.py`（累计收益读侧，#598） | `pytest tests/integration/test_cumulative_profits.py tests/unit/test_position_service.py -q` |
  | `trade_service.py` / 调仓交易路由 | `pytest tests/integration/test_trades*.py tests/integration/test_trade_cash_check.py -q` |
  | `subscription_service.py`（申赎） | `pytest tests/integration/test_subscriptions*.py -q` |
  | `cash_transfer_service.py`（跨天现金转移，两腿 Trade） | `pytest tests/integration/test_trades*.py tests/integration/test_cash_transfers*.py tests/integration/test_trade_cash_check.py -q` |
  | 份额变动事件 | `pytest tests/integration -q -k "share_event or event_window or forced_adjustment"` |
  | 金额/份额量化 | `pytest tests/unit/test_quantize.py tests/integration/test_amount_precision.py tests/integration/test_shares_precision.py tests/unit/test_snapshot_service.py tests/integration/test_trades_validation_preview.py -q` |
  | 分层红线（service 事务/异常约定） | `pytest tests/unit/test_service_no_commit.py -q` |
  | 交易日 / 严格取价（`trading_utils.py` 的 get_*/try_get_* 与快照取价 `_prev_trading_day`） | `pytest tests/unit/test_trading_utils.py tests/integration/test_trading_day.py tests/integration/test_snapshot_nav_strict.py tests/integration/test_snapshot_calendar_exhaustion.py -q` |
  | 原子性 / 双层账本测试及共享状态 helper | `pytest tests/integration/test_snapshots.py tests/integration/test_snapshot_observability.py tests/integration/test_trades_in_transit_lifecycle.py tests/integration/test_subscriptions_create.py -q` |
  | 死锁有界重试（`utils/db_retry.py`、`routers/snapshots.py` 的 generate 端点） | `pytest tests/unit/test_db_retry.py tests/integration/test_snapshots_deadlock_retry.py tests/integration/test_router_catchall_observability.py tests/unit/test_catchall_logging_guard.py -q` |
  | 任务执行记录（`task_runner.run_task` / `scheduler_service` / `routers/tasks.py`） | `pytest tests/unit/test_task_log_orchestration.py tests/unit/test_scheduler_service.py tests/unit/test_run_nav_sync.py tests/integration/test_task_execution_log.py tests/integration/test_tasks.py tests/integration/test_log_cleanup.py -q` |
  | 审计/系统错误日志（`audit_service.py`、任一埋点、日志表迁移） | `pytest tests/unit/test_audit_service.py tests/integration/test_audit_log.py tests/unit/test_migration_0013.py tests/unit/test_migration_0014.py -q` |
  | 字符集 / 建表（`db_charset.py`、`models/base.py`、迁移 0015、`ci.yml` 建库语句） | `pytest tests/unit/test_db_charset.py tests/unit/test_migration_0015.py tests/unit/test_migration_0014.py -q` |
  | 外键约束 / 外键名（`models/nav_sync_detail.py` 的 `name=`、迁移 0016） | `pytest tests/unit/test_migration_0016.py tests/unit/test_migration_0015.py -q` |
  | 测试库隔离（`tests/db_isolation.py`、`tests/conftest.py` 的库选定与归属判据） | `pytest tests/unit/test_db_isolation.py -q`（结构性守卫，改 conftest 导入顺序必跑；全量会话验证由 CI 兜底） |

  表中的通配项（`test_trades*.py` / `test_subscriptions*.py` / `test_cash_transfers*.py`）只匹配该前缀开头的文件：新增这些领域的集成测试必须以**复数前缀**命名（`test_trades_*` / `test_subscriptions_*` / `test_cash_transfers_*`），否则静默落在影响面外；不便改名的单数文件（如 `test_trade_cash_check.py`）在表中显式列出，后续同类文件照此登记。

  跨核心服务的改动（snapshot/position/trade/subscription 任一）额外连带 `-k snapshot` 兜底——快照链是所有写路径的下游。
- **测试库一律由 pytest 选定**（#539 第二单元，判据在 `tests/db_isolation.py`、由 `tests/conftest.py` 在 **`import app.main` 之前**调用）：env `TEST_DB_URL` > `backend/.env.test`（gitignored，按需配置本地/远程 MySQL）> **缺省 = 本次会话自建的临时目录 SQLite**。外部环境 `DATABASE_URL` 一律忽略（只 WARN），不再像早期那样 `setdefault` 继承；`SCHEDULER_ENABLED` 测试期恒关（调度 job 在 lifespan 内会真写库）。次序仍是硬要求：`app.database` 导入时绑定设置与引擎；`app.main` 导入不再连接或建表，pytest 继续自行显式建库。CI 的 SQLite job 两条显式通道都不存在（走缺省临时目录），MySQL job 显式设 `TEST_DB_URL`，且连接账号是**与库同名的最小权限账号**（`ci.yml` 的 `ir_test`/`ir_migration` 各一、`e2e-stack.yml` 的 `ir_e2e` 一个（#548），root 只留在建库建号那一步；权限清单以该守门的 `REQUIRED_PRIVILEGES` 为单一事实来源，两个 job 共用一份、不分叉）——归属闸门判的是「这个库归不归 pytest」，不给小权限，指错 URL 照样能毁整库；**E2E 侧连这道闸门都没有**（它走 `app/database.py` 直吃 `DATABASE_URL`），DBACL 是那条路径上唯一的结构防线。该约束钉在 `scripts/tests/test_ci_mysql_account.py`（`TARGETS` 覆盖两个 workflow，含逐目标的反例用例与「连接串条数」断言）。
- **破坏性初始化只允许打在 pytest 创建并持有的实例上**，判据**不看库名**（名字含不含 test 都不构成许可）：目标为空、或本次会话自建、或带 pytest 写入的归属标记表 `__ir_pytest_ownership__` 且除标记表与模型表外无来源不明之表——三者皆不满足即整体拒绝，探测连不上同样拒绝（失败不等于空库）。会话开始仍 `drop_all + create_all` 保证干净起跑；会话结束**只回收本次自建的临时目录**，显式声明的库保留数据（那种情况下跑完仍可直接登录本地前端浏览种子数据）。守门反例集：`tests/unit/test_db_isolation.py`。
- fixture 层级：session（`test_engine`、`_seed_base_data`）→ autouse（认证全局状态隔离）→ function（`test_db`/`client`/`admin_headers`/`sample_portfolio` 等），业务数据一律用 function 级 fixture/factories 造，不动 session 种子。
- **共享测试 helper（#478/#479/#481 评审，issue #494）**：仅单目录内复用 → `tests/<layer>/<domain>_helpers.py`；跨层/全局 → `tests/` 根（与 `factories.py`、`seed_base.py` 同层）。命名固定 `<domain>_helpers.py`、**无 `test_` 前缀**（pytest 不收集，也不会被 `test_dialect_marker_guard.py` 的 AST 扫描误判为用例）。形态是**模块级工厂函数 + 显式 import**，不是 fixture（业务数据仍按上条走 function 级 fixture/factories）；**跨 ≥2 个测试文件共用才提取**，调用点全在单文件则留原位。头部注释分「溯源 / 现状」两句写，不把 helper 绑死单一 issue；改 helper 时跑消费它的同一 `-k` 子集。
- pytest 配置在 `pyproject.toml`（`--strict-markers`），新增 marker 须登记。
- **分层 marker（#469）**：`unit` / `integration` / `e2e` 由 `tests/conftest.py::pytest_collection_modifyitems` 按目录自动打标（新增文件天然带标），可用 `pytest tests -m integration -q` 选子集；`dialect` 表达「依赖 MySQL 方言行为（SQLite 下自动 skip）」，由用例显式 `@pytest.mark.dialect` 声明（迁移 0014/0015/0016 的 MySQL 类、审计日志的列宽/4 字节用例），**守门**：`tests/unit/test_dialect_marker_guard.py` 断言「凡调用 `_mysql_only()` 或判断 `dialect.name` 含 mysql 的用例必带该标记」（漏标时 `-m` 收窄会静默丢覆盖，#382 教训）；`slow` 已登记备用、暂未使用。**CI 仍全量双跑**（`backend-test` SQLite + `backend-test-mysql` 同一批用例）：#469 原设想的「MySQL job 只跑 `-m "integration or dialect"`」经评估否决——省约 1.5 分钟，代价是 dialect 漏标即静默失去 MySQL 覆盖，收益不抵风险；marker 能力先落地，narrowing 待有实测依据再议（漏标已由上述守门测试兜住）。
- **覆盖率（#254 设防期，#171 观察期已结束）**：本地跑测试默认**不收集**覆盖率（不传 `--cov` 即零开销）；查看口径用 `pytest tests/ -q --cov=app --cov-report=term-missing`（带分支列与缺失行号）。口径与阈值配置在 `pyproject.toml [tool.coverage.*]`：`branch=true`（分支含口径，line+branch 合并计总覆盖率）+ `fail_under`（**当前值、基线来历与历次上调以该文件的注释为单一事实来源**；本指南与 `ci.yml` 都不复述数值——#621 之前 `82` 同时硬编码在 5 处、改一次要动 4 个文件，这本身就是棘轮长期没被执行的摩擦之一）。CI backend-test job 带 `--cov` 运行，跌破阈值即门禁失败；该 job 的 Step Summary 同时印出**门禁真正比较的合并口径 TOTAL** 与距阈值的 pp 差（#621）。
  - **棘轮规则（口径由 #621 钉死）**：`fail_under` 只升不降。此前条文只写「任何 PR 全量实测超阈值 ≥1pp 就上调到实测下取整」，没定义「实测」是哪一次运行、也没定余量，结果是 2026-09-09 定 82 之后 **94 个 merge 里至少 4 次触发点（84/85/86/87）全被跳过**，缺口长到 5.06pp（≈458 units：删/skip 掉这个量级的测试 CI 仍全绿，而增量门禁只看 `app/` 改动行、删测试不产生改动行，它看不见）。四条口径：
    - **触发（谁该动）**：任何 PR——只要「最近一次 main 的全量实测」超当前阈值 ≥1pp 即触发，**不看本 PR 自身增量多少**（+0.01pp 的 PR 照样触发）。刻意用「距阈值的缺口」而非「本 PR 的贡献」作判据：后者要 CI 多测一次 base 全量（≈3 分钟/PR），且缺口会永久留在原地——自愈性是这条规则唯一的执行力来源。与覆盖率无关的 PR 触发时，可另开独立 chore PR 补齐，不强制夹带（铁律 3）。
    - **「实测」= 最近一次 main 的全量运行**，不是本 PR head/merge 那次的数：后者会因 base 落后而系统性偏低（实测 #614 的 base 落后 main 8 小时即低 0.60pp）。
    - **目标值 = `floor(main 实测 − 1pp)`**，与前端 `vitest.config.ts` 同口径；并受硬约束「**不得高于本 PR 自身那次全量实测的下取整**」（否则本 PR 会被自己刚抬的阈值弄红：#614 自身运行 86.47%，写 87 即 CI 红，而它的改动行覆盖是 100%）。两者取小。留 1pp 不是保守而是必要：分母 9050 units 下 1pp = 90 units，而同一 commit 两次运行的噪声就有 5 units（CI 87.06 / 本地 87.12）；若按 `floor(实测)` 定 87，余量只剩 0.06pp ≈ 5 units，base 漂移容忍 ≈5 小时，且新代码按增量门禁最低档 80% 覆盖时加 ~79 units 就会让全局门禁红——等于把「新码 ≥80%」偷偷抬成「≈100%」。
    - **「实测」指合并口径 TOTAL**（stmts 与 branches 一起算），即 `--cov-report=term` 的 `TOTAL` 行 / Step Summary 的「合并口径 TOTAL」行。**不是** `line-rate`（行覆盖）也不是 `branch-rate`（分支覆盖）：main@e882912 三者分别是 87.06 / 89.63 / 78.67，照 line-rate 下取整会写 89 而立刻长红。分支覆盖不单设独立阈值（`branch=true` 下 `fail_under` 已是分支含口径）。
  - 注意 fail_under 作用于 `--cov` 收集的那次运行：本地跑**子集**加 `--cov` 必然跌破阈值（子集覆盖不了全量代码），属预期，阈值只对全量运行有语义。
- **增量覆盖率门禁（#464）**：CI `backend-test` 在 pytest 之后跑
  `diff-cover backend/coverage.xml --compare-branch=<PR base.sha> --fail-under=80`，
  只约束**本 PR 改动行**——堵住「新模块 0% 覆盖被既有高覆盖稀释通过」。与全局
  `fail_under` 职责正交（增量防新码裸奔、全局防整体退化），两者并行。本地复现：
  `cd backend && pytest tests -q --cov=app --cov-report=xml`，再从**仓库根**跑 diff-cover
  （diff-cover 自行按 git root 对齐 `<source>` 绝对路径，实测 cwd 不影响选行；从仓库根跑
  是为了 `diff-cover.md` 落在仓库根，Summary/artifact 依赖该相对路径）。阈值 80 低于全局
  `fail_under`：首次启用避免大面积红，观察一期后可上调。该门禁与全局阈值度量的是**同一个集合**——
  只有 `--cov=app` 收集到的文件（`pyproject.toml` `source = ["app"]`），alembic/scripts
  等口径外文件**不受任何覆盖率门禁约束**。**退化可见化**：若映射断裂，diff-cover 会
  报 `No lines with coverage information` 并 exit 0（静默空转），故 CI 在有新增行落
  `backend/app/` 时发 `::warning::` 提示人工确认（不 fail：只改注释的 PR 也会合法地无可测行）。

## 3. 种子数据（单一事实来源）

`tests/seed_base.py` 提供两个入口，改种子只改这一处：

* **`seed_base_data(db)`**——基础数据：维度字典、适用关系、4 平台、10 产品（4 虚拟 + 6 业务，含 #522 分红在途）、交易日历（2025-01-01 起、终点滚动到 today + 1 年，见下）、draft 组合 `E2E_PORT`（零交易零快照）、ADMIN/admin@2026、VIEWER/viewer123。**三处消费**：pytest（conftest `_seed_base_data`）、CI E2E（`scripts/seed_e2e.py`）、本地 E2E（`scripts/run_e2e_backend.py`）。日历起点固定（存量测试大量依赖 2025 固定日期），终点以 `date.today() + 365 天` 滚动（#468），幂等守卫为**增量补尾**（表内有旧终点时从其后一天续写，不是表空才写）——本地复用库同样能延伸到新终点。
* **`seed_e2e_active(db)`**——E2E 专属活跃组合 `E2E_ACTIVE`（#354）。**仅两处 E2E 脚本在 `seed_base_data` 之后调用，不进 pytest session 种子**：其业务交易/申购/价格数据会泄漏进按「全局账本」精确断言的存量后端测试（`test_filter_by_status`/`test_sort_apply_date_desc`/`test_trades` 系列/价格 upsert 计数），且后端 pytest 本就不需要它。

**前端 E2E 依赖两个组合的形态契约**（spec 经 `frontend/e2e/helpers.ts` 按 code 直达，缺组合或形态退化会硬失败，不再优雅 skip），勿删勿改形态：

* `E2E_PORT`：draft、零交易/申赎/快照——platform-select-search、trade-buy-amount-linkage（用例 7 依赖零快照的 1.0000 首购窗口）等 spec 的目标。
* `E2E_ACTIVE`：动态日期编排——锚定 `date.today()` 回溯 4 个交易日 D1<D2<D3<D4：D1 申购（ADMIN/HBZQ，10 万）→ D2 确认（首窗净值 1.0000、组合转 active）+ 场内买入 510300.SH 并确认（成交价 4.0000，不依赖行情同步）+ 首快照（首快照日 == 最早 confirm_date，#180）→ D3 第二快照（价 4.2000，连续原则）→ D4 pending 场内买入（8,200 元，供编辑类用例；trade_date > 最新快照日且落在前端「近1年」默认过滤窗内，故必须动态日期）。业务数据走 service 层造（复用 CASH 配对腿/激活状态机/快照三表全部不变量），仅 Portfolio/PriceRecord 直接 ORM；整段单事务末尾一次 commit，幂等守卫 =「组合已存在则跳过」。**create_trade 后先 `db.flush()` 再 confirm**——买入的配对 CASH 扣款腿在创建期即落库（#493），flush 让金额/状态在同事务内可见，避免后续读取到未落库的中间态。**禁止对共享 `E2E_ACTIVE` 跑 recalculate/catch-up/generate-next**：它承诺固定快照与可编辑窗口，不用于推进或重算测试；auto_confirm 不确认调仓，到期 pending 调仓会阻断推进，相关场景须自建隔离组合。

日历终点随 today 滚动（#468，原固定 2026-12-31 会形成日期时间炸弹：2027-01-04 起 `frontend-e2e` 红、2028 起 `test_seed_contract` 红）。「日历未同步」哨兵测试（`test_trading_day`/`test_snapshot_service`）取 today + 400 天动态日期，语义不随终点滚动漂移；today+400 哨兵**必须保留**（`test_seed_contract` 仍断言该形态），它测的是**未来侧**耗尽。`seed_e2e_active` 的 RuntimeError 守卫同为防御性校验：它经 `/api/trading-calendar` 回溯取日期，方向是**向过去**（`get_prev_trading_day`），耗尽只可能发生在贴近日历**起点** 2025-01-01 时，与滚动终点无关；#591 起 helper 在覆盖不足时抛 `CALENDAR_NOT_SYNCED`（不再回退），该守卫保留以在种子日期真的越界时响亮失败。E2E spec 的日期锚定一律经 `/api/trading-calendar` 取「today 起最近一个交易日」，禁止以 `new Date()` 直接当交易日或写死年份。

契约由 `tests/integration/test_seed_contract.py` 锁死（形态查询与 auto_confirm 不确认调仓的回归断言；E2E_ACTIVE 在 function 级 fixture 现造，不操作共享 E2E 库），改种子形态先改契约测试。

## 4. 本地启动

先确认配置指向要操作的数据库（见 `.env.example`），从 `backend/` 运行：

```bash
python -m app.bootstrap status
python -m app.bootstrap check
uvicorn app.main:app --reload
```

首次初始化或 schema 待升级时，在明确授权目标库后执行 `python -m app.bootstrap prepare --expect-state "<status 输出的 fingerprint>"`，再运行 check。`status` 只报告状态（探测成功退出 0 不等于已准备）；`check` 仅 ready 退出 0，未准备退出 1；执行错误退出 2。指纹绑定目标身份、当前 schema（含列与必需非空约束）、revision、任务缺失项及本次迁移内容；prepare 在数据库锁内再次比对，状态改变即拒绝，不自动接受新状态。

应用 import 不连接数据库；lifespan 先 check，再同步已有任务文案、恢复孤儿任务和启动调度，**不运行 DDL/迁移**。调度 JobStore 缺表拒绝启动，容器 entrypoint 仅透传命令（#537）。prepare 显式执行 `create_all → alembic upgrade head → JobStore 建表与任务种子`；**alembic 不能单独从零建库**（如 0007 直接 UPDATE product）。MySQL 使用同一连接持有初始化锁并执行迁移；失败不自动 downgrade，不以 revision 未变推断 DDL 未执行。

SQLite 的 prepare 仅建模型/调度表与任务种子，**不跑 MySQL 历史迁移、不 stamp 伪造迁移完成**，不能替代 MySQL 验收。只读探测不创建或修改数据库本体；SQLite 自身可能生成 WAL/SHM 协调文件，不使用会忽略 WAL 内容的 immutable 模式。后端 pytest 的自有建库入口和本地 E2E 的空 lifespan 仍独立于生产迁移链。

## 5. 迁移（alembic）

- 新迁移必须提供可逆 downgrade；CI（backend-test-mysql）对最新一条迁移做 downgrade/upgrade 往返验证；确不可逆时 PR 设 `SKIP_DOWNGRADE` 豁免、合入后移除。
- **采纳型迁移**（把早已存在的事实纳入管理，`upgrade()` 对已存在的库近乎 no-op）的 **downgrade 刻意 no-op、且不得 `raise`**：**0013**（四张日志表纳入 alembic）与 **0016**（`nav_sync_detail.job_id` 外键去重收敛）。no-op 的逆操作本身无物可还原，`raise NotImplementedError` 还会把 `ci.yml` 的 `alembic downgrade -1` 弄红。理由逐条写在各自 `downgrade()` 的注释里。
- 现有不可逆迁移：**0006、0008**（含 DROP 列），回滚只能靠备份。
- **部分条件下不可逆**：**0014**（日志/同步明细表 utf8mb3→utf8mb4）与 **0015**（全库 utf8mb4）的 downgrade 在目标表已含 4 字节字符时跳过该表并打 WARNING（反向转码必然失败，跳过优于半途而废），故对空库/无 4 字节数据的库仍是完整可逆迁移——CI 往返可过、不需要豁免。0015 的关系是 0014 → 0015（`down_revision = '0014'`）：0014 只转五张日志/同步明细表，0015 把库级默认与其余表一次转净，**0014 已在生产执行过、刻意不改**。
- **条件性拒绝降级（零写入整体中止）**：**0019**（`share_change_event.cash_pay_date` + `IN_TRANSIT_DIVIDEND` 种子，#522）的 downgrade 先探测「存在非空 `cash_pay_date`」与「该产品被外键引用」，任一命中即 `RuntimeError`，且拒绝发生在任何 DELETE/DROP 之前——与 0014/0015 的「跳过 + 继续」不同，是整体中止，故功能一旦被使用即不可回退（走前滚或快照），未被使用的库（含 CI 空库往返）完整可逆。守门：`tests/unit/test_migration_0019.py` 逐表断言拒绝路径零写入。运维速查见[回滚手册的迁移可逆性表](../docs/runbooks/deploy-rollback.md#43-现有迁移可逆性速查downgrade-风险)。
- 种子类 DML 写迁移时双方言（SQLite/MySQL）都要过——迁移文件头注释写清幂等设计。

## 6. 依赖

- `requirements.txt` 是镜像与 CI 的**唯一安装来源**（`Dockerfile:30-32` + CI 侧三个安装点均裸 `pip install -r requirements.txt`——`ci.yml` 两个 job、`e2e-stack.yml` 一个）；`pyproject.toml` 的 `dependencies` 全是 `>=` 下界，**不参与构建**，改它不改变任何安装结果。加依赖须更新 requirements.txt。
- **传递依赖不显式钉版就等于没钉**：未出现在 requirements.txt 的包，版本由构建时解析决定、仓库零记录。已钉：`click`（uvicorn 传递）、`starlette`（fastapi 传递，#314）。干净环境实测解析 79 个包、requirements.txt 仅声明 30 个，**其余 50 个传递依赖仍浮动**（清单见 issue #314；含 `anyio`、`typing_extensions`、`pydantic_core`、`cryptography`、`greenlet`、`h11`/`httptools`/`websockets` 等可能跨大版本者）。
- **本地 ≠ CI**：pip 不升级已满足下界的已装包 → 同一份 requirements.txt 在本地可能是旧版、干净环境解析成新版。dependabot 只 bump 文件、不碰本地 venv，长期不重装即漂移（实测曾落后 28/31 个钉版）。跑后端测试前从仓库根 `.venv/bin/python scripts/verify.py run env` 核对**声明钉版**与当前解释器已装包（不查传递依赖，见上条）；报漂移按输出给出的命令同步（`uv pip install --python .venv/bin/python -r backend/requirements.txt`，幂等、已满足时秒级）。排查单个包版本现象仍可直接 `pip show <pkg>`。
- `pip-audit`（`security-scan.yml`）按**声明**解析，抓不到未声明的传递依赖，别当锁文件用。

## 7. E2E 相关脚本

- `scripts/run_e2e_backend.py`：本地 E2E 后端，先独占监听 `127.0.0.1`，每次独占新建 SQLite 并灌种子，空 lifespan 跳迁移；导入不启动。默认临时目录在退出时清理，`--database` / `E2E_DB_PATH` 指定的新文件保留，已有文件或符号链接拒绝、绝不删除；端口由 `--port` / `E2E_PORT` 指定，默认 8000。子进程隔离 cwd/应用配置，关闭行情和调度。前后端联调优先使用[本地隔离入口](../frontend/AGENTS.md#4-e2eplaywright)，不复用其他运行的库或服务（#619）。
- `scripts/seed_e2e.py`：CI E2E 种子入口，**未设 `DATABASE_URL` 直接拒绝**（防误连）。

## 8. OpenAPI 契约检查与导出

从仓库根、使用安装了 `backend/requirements.txt` 的 Python（发布使用 `.venv-openapi/bin/python`）：

```bash
python backend/check_openapi.py
python backend/export_openapi.py --offline
python ir-cli/scripts/gen_response_fields.py
python ir-cli/scripts/gen_response_fields.py --check
```

- 检查与离线导出共用 `openapi_runtime.py`（#539）：父进程不导入 app；子进程使用独立临时 cwd/SQLite，不加载业务 `.env`，不继承数据库、外部服务凭证或 `APP_VERSION`，强制关闭调度和 DEBUG。超时 60 秒，子进程退出后清理整个目录（含 WAL/SHM）；失败与清理异常均报错。
- `check_openapi.py` 始终只读当前仓库的 `backend/openapi.json`，退出码 **0 一致 / 1 漂移 / 2 执行失败**。不同 cwd 不改变检查目标。
- `--offline` 默认原子替换当前仓库的 `backend/openapi.json`，自定义输出用 `--output 路径`；生成失败不覆盖旧契约。线上 `export_openapi.py [URL] [输出路径]` 仅保留兼容，CI/release 使用隔离离线入口。
- 隔离反例测试：从仓库根运行 `python -m pytest scripts/tests/test_openapi_isolation.py -q`（仅 stdlib + pytest，不加载后端 conftest）。pytest 侧的同类隔离见「跑测试」节的 `tests/db_isolation.py`（#539 第二单元），守门反例集 `tests/unit/test_db_isolation.py`。
