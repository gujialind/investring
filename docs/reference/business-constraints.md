# 业务约束与边界速查

> 规则本体见根 `AGENTS.md` §2；错误码的「码名 ↔ 触发条件 ↔ HTTP 状态」穷尽清单见下文**错误码总表**（全仓无错误码注册表——`app/services/exceptions.py` 只有载体类 `BusinessError`/`NotFoundError`，`code` 是自由 str 形参；漂移由 `backend/tests/unit/test_error_codes_doc_sync.py` AST 守门。机读契约 `ir schema`）。本文件只列**读代码不易拼出**的规则语义、错误码触发条件与字段级清单。

## 可用量口径

冻结份额/现金必须**实时计算**，不能仅读快照 `frozen_shares` / `frozen_amount`。

* **基金可用份额**（#277）= 最新快照份额 − SUM(pending 卖出) − SUM(快照未覆盖的 confirmed 卖出) + SUM(快照未覆盖的 confirmed 事件**负向** `shares_change`，`ex_date > 最新快照日` [≤ T])。
  - 事件增量**只计平台级行**（`platform_code IS NOT NULL`）——基金级父记录持汇总值，父子同计会双算。
  - **正向变动不计入**：入快照前保守低估，防事件被撤销后已放行的卖出成为事实超卖。
* **投资人可用份额** = 最新快照份额 − SUM(pending 赎回) − SUM(快照未覆盖的 confirmed 赎回)。份额变动事件不并入——组合份额仅因申赎变化，事件作用于基金/平台维度、不改投资人份额账本。
* **可用现金**两条口径的函数级表达式见 `backend/AGENTS.md` §1.3（`calculate_available_cash` / `compute_cash_balance`）。
* 卖出/赎回输入份额**先量化到 2 位再与可用份额精确比较**（无容差），超出报 `INSUFFICIENT_SHARES`；买入/转移金额同理先量化再与可用现金精确比较，不足报 `INSUFFICIENT_CASH`。`skip_available_check` 仅限 auto\_confirm 路径。

## 申购赎回

* 申购输入**金额**（份额 = 金额 / 申请日净值）；赎回输入**份额**（金额 = 份额 × 申请日净值）。

* **确认日恒为申请日的下一交易日（T+1）**，与产品 `confirm_days` 无关（后者只作用于调仓）；pending 记录的 `confirm_date` 是预计确认日。

* **首窗判定**：确认时申请日无快照**且**不存在 `confirm_date <= apply_date` 的 confirmed 申购（等价于申请日零持仓、净值结构性恒 1.0）→ 按 1.0000 计价；已有资金到账却无申请日快照 → `NAV_NOT_AVAILABLE`（禁止回退旧净值或当前净值）。

* **乱序补录**：确认日早于组合 `started_at` → `CONFIRM_BEFORE_STARTED`（等于则放行）；乱序单 auto\_confirm 记 `auto_confirm_failed`，需手动按序处理。

* 申请日必须晚于最新快照日（`DATE_BEFORE_SNAPSHOT`）。

* 申赎必填 `platform_code`（现金归属平台）；非首次申购要求申请日存在组合快照（`NAV_NOT_AVAILABLE`）。

## 调仓交易

* 金额：买入 `amount = actual_amount − fee`、`shares = amount/price`；卖出 `amount = actual_amount + fee`。**卖出金额为纯派生量**（#190）：有价格时 `amount = quantize(shares × price)`、`actual_amount = amount − fee`；创建时显式传入的 amount/actual\_amount（两参同义、`actual_amount` 优先）仅作一致性校验（差值超 0.01 报 `AMOUNT_MISMATCH`），落库恒用推导值——金额即 shares/price/fee 的「校验和」，用于对账；无价格（场外未传价）时创建期占位，确认按 T 日净值重算。

* **PUT 直改与创建同口径**（#182）：编辑 pending 交易时 buy 的 amount/actual\_amount 视为含费现金支出（`actual_amount` 优先），service 层联动重算净额列并镜像 CASH 腿；sell 有价格时与创建同口径（#190）：按新 shares/price/fee 重推导、显式金额仅作对账（场内超差拒绝、场外静默），无价格占位单仍输入为准；改金额/份额/日期实时校验可用量（加回自身 pending 旧值）、非交易日直接拒绝不静默滚交易日、自然键防重排除自身（无 `allow_duplicate`）；CASH 腿仅 notes 放行；校验全部通过前零写入。

* **跨平台现金腿**：基金买/卖可传 `cash_platform_code`（买 = 扣款平台、卖 = 到账平台；CLI `--cash-platform-code`），CASH 腿落在指定平台、**缺省同基金腿**；两腿仍同 `transfer_group` 原子翻转。

* **可用现金时点口径**：pending 卖出不增加可用现金；买入按扣款平台校验可用现金（根 §2.5），确认时不足同样拒绝（卖出确认对称校验份额；`skip_available_check` 仅限 auto\_confirm 路径）。

* **确认取价**：`confirm_date` 创建时即按 `product.confirm_days` 设定（`confirm` 可传参覆盖，补录用）；场内用成交价（录入时必填）、场外严格用 T 日净值（含 QDII；未同步则拒绝，禁止向前查找；可传 `sync_nav`/`--sync-nav` 在 MISSING\_NAV 时自动回填净值并重试一次，#90）。场外确认可选传入价格，仅与 T 日净值做一致性校验（不一致 `PRICE_NAV_MISMATCH`），不覆盖净值。快照估值侧与此正交：按产品 `nav_lag_days` 取价（`0`=当日、`N`=前第 N 个交易日），详见根 §2.6。

* 防重：同组合/产品/市场/平台/方向/交易日且金额（买）或份额（卖）相同的 pending/confirmed 交易，未传 `allow_duplicate` 报 `DUPLICATE_TRADE`（cancelled 不算）。

* 仅给 product\_code 且一码多市场（LOF）须显式指定 market（`MARKET_AMBIGUOUS`，`details.available_markets` 列可选项）；场内 trade 不可 cancel。

* `trade.transfer_group` NOT NULL；REST 直接创建 `product_code="CASH"` 的交易 → `CASH_TRADE_FORBIDDEN`。

## 份额变动事件

* **分级**：基金级（`share_split`/`share_merge`/`bonus_share`，`platform_code` 空，确认时在 `event.market` 内按平台自动拆子记录）；平台级（`cash_dividend`/`reinvest_dividend`/`forced_adjustment`，每个有持仓 `(market, 平台)` 各录 1 条）。两类事件均以 `event.market` 为边界（#461）：LOF 一码多市场时另一市场的持仓不参与计算与覆盖校验，两市场须分别录入。

* 日期约束：`ex_date > entitlement_date` 且均为交易日；`ex_date` 须晚于最新快照日。平台级未全覆盖该 market 下有持仓平台默认阻断（`PLATFORM_NOT_COVERED`），`force_cover=true` 降为 warning。

* 输入校验（#279，创建/更新/确认三路径同口径）：`forced_adjustment` 必须至少一项（`shares_change`/`cash_change`）非空，否则 `EMPTY_ADJUSTMENT`；现金型产品（`product_type` 为 CASH/IN_TRANSIT）不接受份额变动（结构型事件无条件拒、其余类型显式 `shares_change` 拒，`SHARES_CHANGE_ON_CASH_PRODUCT`）。

* **market 补全口径**（#258，与调仓 #83 同口径）：创建时 `market` 省略/空串按产品唯一市场自动补全；一码多市场（LOF）报 `MARKET_AMBIGUOUS`；产品不存在报 `PRODUCT_NOT_FOUND`——杜绝 `(product_code, market)` 复合外键违约 500。

* 持仓存在性防线（#278）：`forced_adjustment` 确认时精查权益登记日 `(产品, market, 平台)` 持仓行，无行拒绝 `POSITION_NOT_FOUND`（LOF market 误填提前快失败）；快照生成对份额事件硬拒绝 `POSITION_NOT_FOUND`：①指向不存在的持仓行（不静默新建 0 份额行）、②作用于现金行（`cash_amount IS NOT NULL` 的行存在但不得承载份额变动），负向调整打空持仓行产出 `event_zeroed_position` 告警（不阻断）。

## 组合管理

* 关闭/重开/删除投资人的生命周期保护见根 `AGENTS.md` §2.2/§2.3（`started_at` 语义、份额为零才能删投资人等）。

* 存在 pending 申赎或 pending trade 时关闭 → `PENDING_TRANSACTIONS_EXIST`；已关闭再关 → `PORTFOLIO_ALREADY_CLOSED`；非 `closed` 调 reactivate → `PORTFOLIO_NOT_CLOSED`。

* 持仓表禁止手动 CRUD（`POSITION_TABLE_PROTECTED`），现金修正走 `cash-position` 覆盖层（见下）。

## 生命周期通用错误码

* 已 confirmed 的 trade/subscription 直接 PUT → `CANNOT_MODIFY_CONFIRMED`；直接 DELETE → `CANNOT_DELETE_CONFIRMED`（须先 unconfirm）。

* **三态状态门的通用拒绝码 `INVALID_STATUS`**（与上条是同一次 dispatch 的兄弟分支）：confirm 与确认预览要求 `pending`、cancel 要求 `pending`、unconfirm 要求 `confirmed`、PUT 拒绝 `cancelled`。**不对称点**：份额变动事件的 PUT 只拒 `confirmed`（`cancelled` 事件放行），与调仓/申赎不同。**确认预览的三条路径同码不同层**（#424）：申赎 preview 走子类 `InvalidStatusError`、调仓 preview 在 router 抛 `HTTPException`、事件 preview 由 service 抛同码 `BusinessError`（与事件 confirm 共用同一句常量消息）。逐站点触发条件见下文错误码总表。

* 场内 trade cancel → `CANNOT_CANCEL_EXCHANGE`。

* unconfirm 时确认日（事件为 `ex_date`）及之后已有快照 → `SNAPSHOT_DEPENDENCY`。

* 非交易日操作 → `NON_TRADING_DAY`。

* 快照生成对 CASH `cash_amount < 0` 硬阻断 → `NEGATIVE_CASH`。

## 错误码总表

> **穷尽口径**：本表收录 `backend/app` 全部在用错误码，**无刻意省略**——它是「码名 ↔ 触发条件 ↔ HTTP 状态」的唯一事实来源（全仓无码注册表，码为抛出点的字面量）。由 `backend/tests/unit/test_error_codes_doc_sync.py` AST 扫描在用码与本表首列比对守门：**加码 / 改名 / 删码必须同一次提交更新本表**，缺项与死码名都判红。
>
> **HTTP 列**取抛出点实际状态码：`BusinessError` 默认 422、`NotFoundError` 固定 404，显式 `http_status=` 与 router 的 `HTTPException(status_code=...)` 优先。**抛出位置**为相对 `backend/app/` 的取样站点（同码多站点只列代表性的 1-2 处，完整集合以守门测试的 AST 结果为准；行号会随代码演进漂移，以码字面量所在行为准）。
>
> 两个**非 HTTP** 形态：`SESSION_ABORTED` 与 `VALIDATION_FAILED` 的逐日条目形态，是重算 / catch-up 响应里 `results[].errors` 与 `auto_confirmed` 条目的 `code`（响应仍 200，见根 `AGENTS.md` §2.6「重算 = 单一事务」）。

| 码 | HTTP | 触发条件 | 抛出位置（取样） |
| --- | --- | --- | --- |
| `ACCOUNT_LOCKED` | 403 | 账户处于登录失败锁定期（`utils/security.py` 进程内 `login_failure_tracker`：连续失败达 5 次锁 15 分钟）。登录端点两种触发：请求时已锁定；本次密码错误正好把计数推到阈值。已持 Token 的请求经 `get_current_user` 时 `is_account_locked` 为真同样拒绝 | dependencies.py:96; routers/auth.py:59 |
| `ALREADY_EXISTS` | 400 | 创建时自然键已存在（均显式 `http_status=400`）：投资人 `code`、组合 `code`、产品 `(code, market)` 复合键、维度值 `code`；另在产品 PUT 改 `market` 时目标 `(code, new_market)` 已被另一条产品占用 | services/product_service.py:450; services/portfolio_service.py:251 |
| `AMOUNT_MISMATCH` | 422 | 卖出调仓且已传价格时，显式输入的到账金额与推导值 `quantize(shares×price) − fee` 相差 > 0.01；仅场内 `CN_EXCHANGE` 做此对账，场外传价不对账（确认时按 T 日净值重算覆盖） | services/trade_service.py:611 |
| `BULK_DELETE_FAILED` | 500 | `DELETE /api/snapshots/{portfolio}/bulk/{from_date}` 的逐日删除循环中，某日抛出**非** `BusinessError` 的异常（`BusinessError` 如级联回退失败原样透传）；逐日 commit 语义下已成功的日期保留 | routers/snapshots.py:508 |
| `CALENDAR_NOT_SYNCED` | 422 | 交易日历覆盖不到所需日期：`/next`、`/prev` 查询返回 None 或回退等于 `from_date`（日历耗尽时 `get_next_trading_day` 返回入参本身）；`/is-open` 无该日 calendar 行；快照 catch-up / generate-next 时最新快照日的下一交易日为空或不晚于最新快照日 | routers/trading_calendar.py:42; services/snapshot_service.py:617 |
| `CANNOT_CANCEL_EXCHANGE` | 422 | cancel 调仓交易时 `trade.market == "CN_EXCHANGE"`（场内不可取消，须 PUT 改字段或 DELETE 重建）；状态门先于此判（非 pending → `INVALID_STATUS`） | services/trade_service.py:1118 |
| `CANNOT_DELETE_CONFIRMED` | 422 | 删除申赎或调仓交易时 `status == "confirmed"`（须先 unconfirm 回 pending）；pending/cancelled 放行 | services/subscription_service.py:670; services/trade_service.py:1191 |
| `CANNOT_MODIFY_CONFIRMED` | 422 | PUT 直改申赎 / 调仓交易 / 份额变动事件时 `status == "confirmed"`（含基金级子记录，其恒为 confirmed）；同函数内 `cancelled` 另抛 `INVALID_STATUS`（事件 PUT 例外，见该码） | services/trade_service.py:868; services/subscription_service.py:561 |
| `CANNOT_UNCONFIRM_CHILD` | 422 | 对 `parent_event_id` 非空的基金级事件子记录单独 unconfirm（须对父记录执行，由父级联删除子记录）；判在 `status != "confirmed"` 之后、快照保护之前 | services/share_change_event_service.py::unconfirm_share_change_event |
| `CASH_TRADE_FORBIDDEN` | 422 | 现金腿不得旁路操作：创建调仓时解析后 `product_code == "CASH"`（现金只能经申赎 / 基金调仓配对腿 / 跨平台转移生成）；PUT 修改 CASH 腿且改动字段集合超出 `{notes}` | services/trade_service.py:690; services/trade_service.py:874 |
| `CONFIRM_BEFORE_STARTED` | 422 | 申购确认预览与确认路径（同一实现）中，`sub_type == "subscribe"` 且组合 `started_at` 非空、T+1 确认日 **<** `started_at`（乱序补录闸门；等于放行以支持同日多平台，`started_at` 为空豁免，赎回不校验） | services/subscription_service.py:121 |
| `CONFIRM_REQUIRED` | 422 | 快照批量删除未显式传 `confirm=true`（破坏性操作守卫，因逐日 commit 不可中途回滚）；`dry_run=true` 在此之前直接返回预览、不触发本码 | routers/snapshots.py:466 |
| `DATA_SOURCE_NOT_CONFIGURED` | 503 | `POST /api/trading-calendar/sync` 捕获 `TushareNotConfiguredError`——`TUSHARE_TOKEN` 未配置（`services/tushare_client.py`） | routers/trading_calendar.py:125 |
| `DATE_BEFORE_SNAPSHOT` | 422 | 存在最新快照日时，业务日期 `<=` 最新快照日（要求严格晚于）：调仓 `trade_date`（创建与 PUT 改日期共用 `validate_trade_date`）、现金转移 `transfer_date`、申赎 `apply_date`（创建与 PUT）、事件 `ex_date`（创建与 PUT 共用 `_validate_event_dates`） | services/trade_service.py:47; services/subscription_service.py:465 |
| `DELETE_FAILED` | 500 | `DELETE /api/snapshots/{portfolio}/{date}` 删除中抛出**非** `BusinessError` 的异常；`BusinessError`（如级联回退失败整体中止）rollback 后原样透传，不降级为本码 | routers/snapshots.py:410 |
| `DIMENSION_RULE_CONFLICT` | 422 | 维度值 PUT 全量替换 `dimension_rules` 的收紧保护：某维度改为 `required`（原非 required）而该 asset_class 下存量产品该维度为空；或删除规则行（→ `forbidden`）而存量产品该维度非空 | services/asset_classification_service.py:200; :215 |
| `DIMENSION_VALUE_IN_USE` | 422 | 维度值 PUT 缩减 `applicable_asset_classes`（全量替换语义）时，被移除的 asset_class 下仍有产品引用该维度值（`details` 带产品清单） | services/asset_classification_service.py:272 |
| `DUPLICATE_TRADE` | 422 | 自然键防重：同组合/产品/市场/平台/方向/交易日下已有 `pending`/`confirmed` 交易，买入比 `actual_amount`、卖出比量化后 `shares` 相等即命中。创建路径可传 `allow_duplicate` 放行；PUT 因改日期 / 买入金额 / 卖出份额而与他人（排除自身 id）撞车时无放行口 | services/trade_service.py:794; :984 |
| `EMPTY_ADJUSTMENT` | 422 | `event_type == "forced_adjustment"` 且 `shares_change` 与 `cash_change` 均为 None（否则确认后零效果且无告警）；创建、PUT（按合并后生效值）、confirm（兜底防存量脏数据）三处共用同一校验 | services/share_change_event_service.py::_validate_adjustment_not_empty |
| `FORBIDDEN` | 403 | 权限门（非资源不存在）：`get_current_admin` 要求 `current_user.role == "admin"`，非 admin 访问 admin-only 端点即拒；改密端点非 admin 且 `target_code` 指向他人 | dependencies.py:135; routers/auth.py:142 |
| `INSUFFICIENT_CASH` | 422 | 支出超过扣款平台实时可用现金（先量化 2 位再精确比较、无容差）：调仓买入按 `as_of` 校验（创建/PUT/确认共用，加回自身 pending CASH sell 腿；确认侧 `skip_available_check` 跳过）；赎回确认按确认日校验该平台可用现金（`skip_cash_check` 跳过）；现金转移按转出平台校验 | services/trade_service.py:112; services/subscription_service.py:242 |
| `INSUFFICIENT_SHARES` | 422 | 卖出/赎回份额超过实时可用份额：调仓卖出（创建/PUT/确认共用 `validate_sell_shares_with_addback`，自身 pending 卖出份额加回防双重计数）；赎回创建按申请日投资人可用份额，赎回 PUT 按新份额（本条 pending 旧份额加回） | services/trade_service.py:161; services/subscription_service.py:504 |
| `INVALID_AMOUNT` | 422 | 金额入参为 None 或量化到 2 位后 `<= 0`：调仓买入含费现金支出（创建/PUT/确认共用）、现金转移金额（原值与量化后两道）、申购金额（创建原值 + 量化后、PUT 量化后）；另卖出调仓有价格时 `quantize(shares×price) − fee <= 0`（fee 不小于毛额） | services/trade_service.py:88; services/cash_transfer_service.py:75 |
| `INVALID_CLASSIFICATION` | 422 | 资产分类维度字典新建/编辑形态非法：`dimension` 不在五维白名单；`code` 非全大写或不带该维度前缀（ASSET_/REGION_/STYLE_/SIZE_/SEG_）；asset_class 值却传 `applicable_asset_classes`、非 asset_class 值传 `dimension_rules` 或适用大类为空（新建/更新均须 ≥1）；`dimension_rules` 的维度或规则值越界；关联的适用大类不存在/不是 asset_class 维度值，或其规则矩阵无该维度行（无行 = 禁止） | services/asset_classification_service.py:107; :65 |
| `INVALID_CONFIRM_DAYS` | 422 | 产品 `confirm_days` 非法（`validate_confirm_days`）：显式传 null、< 0，或场内（`CN_EXCHANGE`）不为 0。create 仅在显式传入时校验（未传按 market+is_qdii 推导）；update 对合并后终态**无条件**校验——即使本次没改该字段，存量脏值（NULL/负数）也会在任何 PUT 上被拦 | services/product_service.py:289; :277 |
| `INVALID_CREDENTIALS` | 401 | 登录：`code` 查无该投资人，或 `verify_password` 对 password_hash 校验不通过（两种情形同一分支、不区分用户是否存在），且账户进入时未被锁定、本次失败也未新触发锁定（锁定态一律 403 `ACCOUNT_LOCKED`） | routers/auth.py:68 |
| `INVALID_DATE_ORDER` | 422 | 份额变动事件 `ex_date <= entitlement_date`（除息日必须严格晚于权益登记日）；创建与 PUT 改日期（按合并后生效值重跑）共用 `_validate_event_dates` | services/share_change_event_service.py::_validate_event_dates |
| `INVALID_DATE_RANGE` | 422 | 区间查询同一组日期参数 start > end：调仓列表 trade_date 组与 confirm_date 组、申赎列表 apply_date 组与 confirm_date 组、快照历史 start/end（两参可选，均传才比）、净值覆盖 `get_nav_coverage` start/end（两参必填、无 None 短路）、事件列表 ex_date_start/ex_date_end（唯一在 router 内直接抛 `BusinessError` 的站点） | services/trade_service.py:1257; routers/share_change_events.py:47 |
| `INVALID_DIMENSION_TAGS` | 422 | 产品五维标签校验（`validate_dimension_tags`）四层任一不过：维度值不存在或 `dimension` 与字段不匹配；值 `is_active=False`（create 全查，update 只查实际变化字段）；`asset_class_code` 为空却填了其余维度；大类规则矩阵 required 维度缺失、或无规则行（= forbidden）的维度有值；所选值未在 `asset_dimension_applicability` 关联该 asset_class | services/product_service.py:108; :142 |
| `INVALID_DISPLAY_CONFIG` | 422 | 组合 `display_config`（持仓明细二级分组覆盖）非法：不是 dict；key 不是字典中 `dimension=asset_class` 的维度值（不校验 is_active）；value 未在该大类的 `asset_class_dimension_rule` 登记（无规则行的大类如 ASSET_CASH 任何配置均拒）。create 恒校验；update 仅在非 UNSET 哨兵时校验，null/{} 归一为清空、不触发校验 | services/portfolio_service.py:63; :69 |
| `INVALID_ENTITLEMENT_DATE` | 422 | 份额变动事件 `entitlement_date`（权益登记日）不是交易日（`trading_calendar.is_open`）；创建与 PUT 改日期共用，是双日期校验链的第一道（早于除息日交易日、日期先后、晚于最新快照日） | services/share_change_event_service.py::_validate_event_dates |
| `INVALID_EX_DATE` | 422 | 份额变动事件 `ex_date`（除息日）不是交易日；创建与 PUT 改日期共用，紧随权益登记日交易日校验之后 | services/share_change_event_service.py::_validate_event_dates |
| `INVALID_MARKET` | 422 | PUT 产品时 `market` **值实际变化**且新值不在枚举（CN_EXCHANGE/CN_OTC/HK_MUTUAL）；守卫在系统虚拟产品保护（`SYSTEM_PRODUCT_PROTECTED`）之后。create 路径不校验 market 枚举（虚拟产品 `market=""` 由此可落库） | services/product_service.py:342 |
| `INVALID_NAV_LAG_DAYS` | 422 | 产品 `nav_lag_days` 非法（`validate_nav_lag_days`）：为 null（NOT NULL 列的「清除」语义一并拒）、< 0，或场内（`CN_EXCHANGE`）不为 0。create 恒校验（默认 0）；update 按 market + nav_lag_days 合并终态**无条件**校验，故 CN_OTC→CN_EXCHANGE 迁移残留 lag>0、或存量脏值在任何 PUT 上都会被拦 | services/product_service.py:255; :243 |
| `INVALID_OLD_PASSWORD` | 400 | 改密时 `verify_password(old_password, hash)` 不通过；仅在「非 admin，或 admin 改自己密码」这条必须验旧密码的分支触发（admin 改他人密码不校验旧密码）。未传 old_password 是 400 `OLD_PASSWORD_REQUIRED`，非本码 | routers/auth.py:165 |
| `INVALID_PARAM` | 422 | 申赎 PUT 参数非法（`update_subscription`，状态门之后）：除 `notes`（null = 清除备注）外任一字段显式传 null——防绕过量化与可用份额闸门落库脏数据；或字段与 `sub_type` 错位——申购传 `shares`、赎回传 `amount` | services/subscription_service.py:572; :579 |
| `INVALID_PRODUCT_TYPE` | 422 | `product_type` 不在枚举（ETF/OEF/LOF/CASH/IN_TRANSIT）（`validate_product_type`）：create 恒校验；update 仅在 product_type **实际变化**时校验（前端编辑恒带该字段，显式传原值不进门禁） | services/product_service.py:220 |
| `INVALID_SHARES` | 422 | 份额输入为空或量化到 2 位后 <= 0：赎回创建（`shares` 为 None/≤0，以及量化后再判 ≤0）、赎回 PUT 改 `shares`；调仓卖出侧 `validate_sell_shares_with_addback`（`new_shares` 为 None，或量化后 ≤0），由卖出 create / update（shares 或 trade_date 变动时）/ confirm 三条路径共用 | services/subscription_service.py:495; services/trade_service.py:146 |
| `INVALID_STATUS` | 422 | 三态生命周期状态门（根 §2.7）的通用拒绝码：confirm 与确认预览要求 `status == "pending"`（申赎/调仓/事件；调仓 preview 与 confirm 两处由 router 抛 `HTTPException` 422，事件 preview 由 service 抛同码 `BusinessError`，#424）；cancel 要求 pending；unconfirm 要求 confirmed；PUT 拒绝 cancelled。申赎侧经子类 `InvalidStatusError`；事件 PUT 只拒 confirmed，cancelled 事件不拦（与调仓/申赎不对称） | services/subscription_service.py:60(class InvalidStatusError); services/trade_service.py:1116 |
| `INVALID_TYPE` | 422 | 创建时枚举兜底：申赎 `sub_type` 既非 `subscribe` 也非 `redeem`；调仓 `trade_type` 既非 `buy` 也非 `sell`（schema 为裸 `str`，REST/CLI 都能传非法值，故在 if/elif 链的 else 分支拦截） | services/subscription_service.py:512; services/trade_service.py:768 |
| `INVESTOR_HAS_SHARES` | 422 | 删除投资人时，其**最新快照日**的 `investor_holding` 行 `shares > 0`（份额未清零不允许物理删除；只看最新一行，历史行有份额不阻断） | services/investor_service.py:64 |
| `MANUAL_OVERRIDE_NOT_FOUND` | 404 | 删除现金手动重估记录时，按 (组合, 平台, `product_code="CASH"`, `value_date`) 查不到 `manual_market_value` 行 | services/position_service.py:922 |
| `MARKET_AMBIGUOUS` | 422 | `resolve_product_market`：只给 `product_code`、省略或传空 `market`，而该 code 在 `product` 表存在多个 market（LOF 一码多市场）时拒绝自动补全，`details` 带 `available_markets`。调仓创建、份额事件创建、不带 market 的产品详情共用 | services/product_service.py:194 |
| `MARKET_CHANGE_REFERENCED` | 422 | 更新产品且 `market` **实际变化**（新值非 None 且 != 原值）时，旧 `(code, market)` 已被任一 `trade` / `share_change_event` / `portfolio_position` 引用（三类计数任一 > 0）；系统虚拟产品先被 `SYSTEM_PRODUCT_PROTECTED` 拦截 | services/product_service.py:370 |
| `MISSING_NAV` | 422 | 净值严格匹配、禁止向前回退（根 §2.6）：① 快照侧按产品 `nav_lag_days` 定取价日（0 = 当日、N = 前第 N 个交易日），任一持仓缺该日 `price_record` 即拒绝生成（`db.add_all` 前抛，整体回滚）；预校验中**纯** price_data 失败同报此码，与其他检查项混合失败则降级 ValueError → `VALIDATION_FAILED`。② 调仓确认侧场外净值型基金（OEF/LOF × CN_OTC/HK_MUTUAL）缺 T 日（`trade_date`）净值即拒绝确认，`sync_nav=true` 时自动回填后重试一次、回填异常或仍缺也报此码。catch-up/recalculate 逐日循环捕获后写进 `results[].errors`（响应仍 200） | services/snapshot_service.py:1381; services/trade_service.py:390 |
| `MISSING_OR_INVALID_PRICE` | 422 | 创建调仓时价格闸门：`product.market == "CN_EXCHANGE"`（场内）且未传 `price`；或任意市场显式传入的 `price <= 0` | services/trade_service.py:715; :719 |
| `MISSING_POSITION_SNAPSHOT` | 422 | 份额变动事件缺基数快照（确认与**确认预览**同码，两侧口径一致）：① `entitlement_date` 该组合**无任何** `portfolio_position` 行（两路径同款前置，预览在 `compute_share_change_event_preview` 内）；② 基金级事件（`platform_code is None`）自动拆分时该产品在 `entitlement_date` 的 `event.market` 下无 `shares > 0` 的持仓行（确认侧 `_confirm_fund_level_event` 的 `ValueError` 被包装为此码，预览侧在计算前显式拒绝——否则会返回 0/0.00 的「假预览」，看着能确认、点下去才炸；#461：口径以 `event.market` 为边界，另一市场有持仓不算命中） | services/share_change_event_service.py::confirm_share_change_event; ::compute_share_change_event_preview; ::resolve_entitlement_shares |
| `NAV_NOT_AVAILABLE` | 422 | 申赎确认/预览的净值决策落到兜底：申请日无 `portfolio_value_snapshot`，**且**已存在 `confirm_date <= apply_date` 的 confirmed 申购（即资金已到账、不在 1.0000 首窗内，根 §2.8）。首窗内不抛、按 1.0000 计价 | services/subscription_service.py:48(class NavNotAvailableError); :166 |
| `NEGATIVE_CASH` | 422 | 快照持仓生成时，任一平台 CASH 行（`cash_amount IS NOT NULL`）计算出的 `cash_amount < 0` 即硬阻断（#203 由告警升级），抛出点在 `db.add_all` 之前、调用方整体回滚；存量脏数据另经 status 端点 `negative_cash_platforms` 暴露 | services/snapshot_service.py:1410 |
| `NON_TRADING_DAY` | 422 | 目标日期不在 `trading_calendar.is_open`：调仓创建/PUT 的 `trade_date`（`validate_trade_date` 共用）、申赎创建/PUT 的 `apply_date`、跨平台现金转移的 `transfer_date`、现金手动重估的 `update_date`（缺省取 `date.today()`）。快照生成侧同型检查抛 ValueError 而非此码 | services/trade_service.py:44; services/position_service.py:780 |
| `NOT_FOUND` | 404 | 通用「资源不存在」码，全部站点均走 `NotFoundError`（无显式 `http_status` 覆盖，恒 404）：组合（`_get_portfolio_or_404` 及创建交易/事件/申赎时的组合校验）、投资人、产品 `(code, market)` 组合（`details` 带 `available_markets`）、维度值、组合最新一条市值快照（`get_latest_value_snapshot`）。与专用码分工：快照 catch-up/generate-next 与现金重估路径用 `PORTFOLIO_NOT_FOUND`，市场解析用 `PRODUCT_NOT_FOUND` | services/portfolio_service.py:29; services/investor_service.py:43 |
| `NO_SNAPSHOT_BASELINE` | 422 | 组合尚无任何快照（`get_latest_snapshot_date` 为 None）时调 `catch_up_snapshots` 或 `generate_next_snapshot`——两者都以最新快照日为增量基线，无基线须改用 recalculate 从最早交易日重建 | services/snapshot_service.py:589; :688 |
| `OLD_PASSWORD_REQUIRED` | 400 | `PUT /api/auth/password`：需验旧密码的分支（`current_user.role != "admin"` **或** `target_code == current_user.code`，即改自己的密码）下请求体未提供 `old_password`。router 直接抛 `HTTPException`，不经 `BusinessError` | routers/auth.py:157 |
| `PENDING_TRANSACTIONS_EXIST` | 422 | ① 关闭组合时该组合存在 pending 申赎或 pending 调仓（两类计数任一 > 0）；② 修改产品 `product_type` 且值**实际变化**时，该产品存在 pending `trade` 或 pending `share_change_event`（`details` 带两类计数） | services/portfolio_service.py:303; services/product_service.py:333 |
| `PLATFORM_NOT_ALLOWED` | 422 | 创建份额变动事件时 `event_type` 属基金级（`share_split` / `share_merge` / `bonus_share`）却指定了 `platform_code`（空串已在入口归一为 None，故只有真值触发） | services/share_change_event_service.py::create_share_change_event |
| `PLATFORM_NOT_COVERED` | 422 | 创建平台级事件（`cash_dividend` / `reinvest_dividend` / `forced_adjustment`）时，`entitlement_date` 该产品**同 `market`** 有 `shares > 0` 持仓的平台，未被「同 `(产品, market)`、同 `ex_date` 的非 cancelled 平台级事件 ∪ 本次 `platform_code`」全覆盖（#461：持仓侧与已录事件侧都按 market 收窄，LOF 另一市场的持仓/事件不参与）；传 `force_cover=true` 降级为 warning | services/share_change_event_service.py::check_platform_coverage |
| `PLATFORM_NOT_FOUND` | 404 | 传入平台 code 在 `platform` 表查无记录（全部站点均 `NotFoundError`，恒 404）：调仓的 `platform_code` 与 `cash_platform_code`、申赎创建与 PUT 的 `platform_code`、平台级事件的 `platform_code`、现金转移的转出/转入平台、现金手动重估的 `platform_code` | services/trade_service.py:700; services/subscription_service.py:476 |
| `PLATFORM_REQUIRED` | 422 | 创建份额变动事件时 `event_type` 属平台级（cash_dividend / reinvest_dividend / forced_adjustment）而 `platform_code` 为空（空串先归一为 None，#343）；创建调仓交易未传 `platform_code`——基金腿平台决定持仓归属，缺省会让买入现金闸门退化为全组合聚合 | services/share_change_event_service.py::create_share_change_event; services/trade_service.py:698 |
| `PORTFOLIO_ALREADY_CLOSED` | 422 | `close_portfolio`：目标组合 status 已为 closed 时重复关闭被拒（组合不存在走 `_get_portfolio_or_404` 的 404） | services/portfolio_service.py:294 |
| `PORTFOLIO_NOT_ACTIVE` | 422 | 组合状态非 active 的写操作闸门：创建调仓、编辑调仓、创建跨平台现金转移均要求 `status == "active"`；创建申赎口径较宽——status 不在 (active, draft) 才拒，draft 放行以支持首购确认时激活（根 §2.2） | services/trade_service.py:664; services/subscription_service.py:460 |
| `PORTFOLIO_NOT_CLOSED` | 422 | `reactivate_portfolio`：组合 `status != "closed"`（draft / active）时不可重开 | services/portfolio_service.py:318 |
| `PORTFOLIO_NOT_FOUND` | 404 | 按 code 查不到 portfolio 行：快照 status / list / bulk-delete 端点与 recalculate-async 的组合前置校验（router 侧 `HTTPException` 404）；catch-up、generate-next、现金转移创建、现金手动重估的写入/列表/删除（service 侧 `NotFoundError`）。注意单日 generate 的组合不存在不落本码，走 ValueError → `VALIDATION_FAILED` | routers/snapshots.py:150; services/snapshot_service.py:584 |
| `POSITION_NOT_FOUND` | 422 | 份额变动事件指向不存在的持仓行：① 确认平台级 `forced_adjustment` 时，`entitlement_date` 当日快照无 (product_code, market, platform_code) 持仓行（提前快失败，否则要到快照生成才炸）；② 快照生成应用窗口内 confirmed 事件时，该三元组键在持仓字典中不存在（LOF market 误填为典型），或键存在但命中的是现金行（`cash_amount IS NOT NULL`） | services/snapshot_service.py:1225; services/share_change_event_service.py::resolve_entitlement_shares |
| `POSITION_TABLE_PROTECTED` | 422 | POST / PUT / DELETE `/api/positions` 三端点无条件拒绝（handler 首行即抛，不读请求体、不查记录）：`portfolio_position` 是系统生成的快照表，现金修正须走 cash-position（`manual_market_value` 覆盖层），删快照走 `DELETE /snapshots/{portfolio_code}/{snapshot_date}` | routers/positions.py:149; :178 |
| `PRICE_NAV_MISMATCH` | 422 | 确认或预览确认场外净值型基金（product_type ∈ OEF/LOF 且 market ∈ CN_OTC/HK_MUTUAL）时显式传入 price，经 `quantize_nav` 归一到 4 位（ROUND\_HALF\_UP，#428）后与 T 日（`trade_date`）净值不等——手动价只作一致性校验，不参与计算也不覆盖净值，不传价则直接取净值 | services/trade_service.py:403 |
| `PRODUCTS_PARAM_CONFLICT` | 422 | 列表过滤参数互斥（两处均显式 `http_status=422`）：调仓列表 `products`（逗号分隔 `code\|market` 多选）解析出非空项时又传了 product_code 或 market（判据为 `market is not None`）；份额变动事件列表 products 非空时又传了 product_code | services/trade_service.py:1281; routers/share_change_events.py:63 |
| `PRODUCT_NOT_FOUND` | 404 | `resolve_product_market`：调用方省略 market，而按 product_code 查不到任何 Product 行（markets 集合为空）。省略 market 但命中多个市场走 `MARKET_AMBIGUOUS`；显式给了 `(code, market)` 却不存在时各入口抛 `NOT_FOUND`，不落本码 | services/product_service.py:189 |
| `PRODUCT_REQUIRED` | 422 | 创建份额变动事件时 `product_code` 为空（falsy）——事件必须挂具体产品；基金级事件靠 `platform_code` 为空区分，不能省略产品 | services/share_change_event_service.py::create_share_change_event |
| `RECALCULATION_FAILED` | 500 | `POST /snapshots/recalculate` 的兜底 `except Exception`：ValueError 已被前一分支翻成 422 `VALIDATION_FAILED`，其余任何异常（逃出逐日 try 的 `BusinessError`、router 内 `db.commit()` 失败、DB/连接错误等）rollback 后统一 500。**逐日失败不走这里**——它们进 `results[].errors` 并以 200 返回；该端点无 `except BusinessError` 分支（generate/generate-next 有，领域异常原样上抛） | routers/snapshots.py:117 |
| `RECALC_JOB_CONFLICT` | 409 | `POST /snapshots/recalculate-async`：`submit_snapshot_recalc_job` 发现 sync_job 表已有 `job_type=snapshot_recalc` 且 `status ∈ (pending, running)` 的记录（同类型单 active 锁）抛 `ConflictError`，router 翻成 409 | routers/snapshots.py:168 |
| `SAME_PLATFORM` | 422 | `create_cash_transfer`：`from_platform == to_platform`，跨平台现金转移两端必须不同 | services/cash_transfer_service.py:57 |
| `SESSION_ABORTED` | —（非 HTTP 码：200 + `results[].errors` / `auto_confirmed` 条目） | auto_confirm 单条守护判定 DB session 不可恢复时写入的根因 code（#305/#419）：进守护前 `db.is_active` 为假；或守护内 savepoint 回滚自身失败、捕获 `PendingRollbackError`、`DBAPIError.connection_invalidated`、失败后 `is_active` 仍为假。重算逐日循环见到它即 break 并追加进 `results[].errors`（触发 router 整体 rollback），catch-up / 调度路径经 `auto_confirmed` 条目透传 | services/snapshot_service.py:1666; :499 |
| `SHARES_CHANGE_ON_CASH_PRODUCT` | 422 | 事件标的为现金型/在途产品（按 product_code(+market) 查得 `product_type ∈ {CASH, IN_TRANSIT}`），且 `event_type` 属结构性份额类型（share_split / share_merge / bonus_share / reinvest_dividend，确认时必产生份额变动）或显式填了 `shares_change`；创建、PUT（按合并后值）、确认三处均校验。纯现金调整（`shares_change` 为空，如 #279 对 CASH 的现金修正）不触发 | services/share_change_event_service.py::_validate_product_allows_shares_change |
| `SNAPSHOT_DEPENDENCY` | 422 | unconfirm 的快照保护：已 confirmed 记录的确认日（申赎/交易取 `confirm_date`、事件取 `ex_date`）及之后已存在该组合快照即拒绝，须先删快照走级联回退。申赎侧 `check_snapshot=False`（快照删除级联自身调用）跳过本检查；交易侧 `confirm_date` 为空时不检查 | services/subscription_service.py:361; services/trade_service.py:1158 |
| `SNAPSHOT_GENERATION_FAILED` | 500 | `POST /snapshots/generate` 与 `/generate-next` 的兜底 `except Exception`：`BusinessError` 已原样上抛交全局 handler、ValueError 已翻成 422 `VALIDATION_FAILED`，剩下的非预期异常（DB/连接错误、commit 失败等）rollback 后统一 500 | routers/snapshots.py:73; :237 |
| `SNAPSHOT_NOT_CONTINUOUS` | 422 | 单日生成的连续性校验（`check_continuity=True`；重算逐日重建 bypass）：组合已有快照时，`target_date` 既不等于最新快照日（重建最新一日）也不等于最新快照日的下一交易日——早于最新日属单独重建中间快照、晚于属跳过交易日，两者都拒；零快照首次生成不受限 | services/snapshot_service.py:874 |
| `SNAPSHOT_REQUIRES_RECALCULATE` | 422 | 单日 generate 的「零快照失忆」守卫（#180）：组合无任何 `portfolio_value_snapshot`，且存在 `confirm_date < target_date` 的 confirmed 申赎或交易（`confirm_date` 为 NULL 的异常数据回退按 apply_date / trade_date 比较）——增量窗口无前序基线会退化为仅目标日，早期到账被静默漏掉，须改用 recalculate 从最早 `confirm_date` 逐日重建；目标日即最早到账日（`confirm_date == target_date`）不受影响 | services/snapshot_service.py:216 |
| `SYNC_FAILED` | 500 | `POST /trading-calendar/sync`：Tushare 接口报错（`TushareAPIError`）或任何其他异常导致同步失败，两分支均 500（Tushare 未配置走 503 `DATA_SOURCE_NOT_CONFIGURED`，不落本码） | routers/trading_calendar.py:132; :137 |
| `TASK_NOT_FOUND` | 404 | `run_task` 的派发表守卫（#406）：`task_code` 不在 `_TASK_DISPATCH` 覆盖的四个任务码（`nav_sync` / `snapshot_generate` / `trading_calendar_sync` / `log_cleanup`）内，`details.available_tasks` 回传可选集；**不建执行记录**——没执行过的任务不该有执行历史。经 `POST /system/tasks/{code}/run` 时通常由 router 的前置校验（查 `scheduled_task`）先命中，本码覆盖「任务表有行但派发表未覆盖」的漂移 | services/task_runner.py:509 |
| `SYSTEM_PRODUCT_PROTECTED` | 422 | `update_product` 的身份字段守卫：产品 code ∈ (CASH, IN_TRANSIT_BUY, IN_TRANSIT_SELL)，且本次更新**实际改变** `product_type` 或 `market` 的值（显式传原值不进门禁）；这两个字段以外的编辑不受本码限制 | services/product_service.py:315 |
| `TRANSFER_NOT_FOUND` | 404 | `confirm_cash_transfer`：按 (portfolio_code, transfer_group) 查不到 `product_code=CASH` 且 `status=pending` 的腿——组不存在或组内两腿已全部 confirmed（`NotFoundError` → 404） | services/cash_transfer_service.py:173 |
| `TRANSFER_NOT_READY` | 422 | `confirm_cash_transfer`：存在 pending CASH 腿，但其生效确认日（首条腿的 `confirm_date`，为空则取该腿 `trade_date` 的下一交易日）晚于今天——跨天转移尚未到账日，不允许提前确认（根 §2.9 非对称状态） | services/cash_transfer_service.py:179 |
| `VALIDATION_FAILED` | 422（router 包装 ValueError）；逐日条目形态为 200 | 两形态同名：① generate / recalculate / generate-next 端点把下游 ValueError 翻成 422——组合不存在或非 active（`_validate_portfolio`）、目标日非交易日、依赖校验有 failed 项（纯 price_data 缺失走 `MISSING_NAV`、不落本码）、重算整区间静态预校验失败；② recalculate 逐日循环里 `validate_snapshot_dependencies` 出现 failed 项时写入 `results[].errors` 的 code（响应 200，随后 break、整体 rollback） | routers/snapshots.py:67; services/snapshot_service.py:461 |

## 量化产生点清单

份额与金额统一 2 位小数、净值/市值/成本价 4 位小数、**三者均 ROUND\_HALF\_UP**、负数按绝对值对称；**量化只发生在下列产生点**，读取/累加路径不量化（规则本体见根 `AGENTS.md` §2.11）。每个精度各有唯一 helper，调用点不得写 `Decimal("0.01"/"0.0001")` 字面量——`quantize` 不传 `rounding=` 会走 Decimal 缺省的 HALF\_EVEN（#428 的教训：4 位口径曾静默漂移 7 处）。

* **份额产生点**（`quantize_shares`）：申购确认 `amount/nav`、调仓买入 `amount/price`、卖出与赎回的用户输入、份额事件的变动计算。

* **金额产生点**（`quantize_amount`）：卖出与赎回确认 `shares×nav`、买入金额与手续费的用户输入、申赎金额、现金分红 `cash_change`、`forced_adjustment` 用户填写、`manual_market_value` 写入、现金转移金额、trade PUT 直改。

* **净值/估值产生点**（`quantize_nav`，#428）：`_generate_portfolio_value_snapshot` 构造快照行的 `total_value` / `unit_price` / `unit_price_change_pct` / `in_transit_total`；`_generate_investor_holding` 的 `cost_per_share`；以及**确认对账比较**——场外传入价与 T 日净值两侧归一到 4 位后精确比较（`PRICE_NAV_MISMATCH`，无容差）。

* 估值口径（`market_value` / `total_value` / `unit_price`）保持 4 位、不进现金账本；DB 字段精度收紧留作后续迁移。

## 易错陷阱

1. 现金市值修正走 `POST /positions/portfolio/{code}/cash-position` 写 `manual_market_value`（绝对替换），**不直接改 `portfolio_position`**；写入后需重新生成快照。
2. LOF 拆分为两条记录（场内/场外分别处理）。
3. 组合份额仅因申购赎回变化；分红再投资只影响成分基金份额。
4. 投资人不支持强制物理删除——份额需为 0 才能删。
5. 幂等性缓存（`idempotency_cache`）24 小时过期，批量调仓用 `Idempotency-Key`。
6. 分类信息只从 positions API 读侧派生（#128）：快照表无分类列，不要在写侧/快照链路重新引入 asset\_type 冗余；判断现金行用 `cash_amount IS NOT NULL`，不用产品类型字符串。产品维度标签改动走 product create/update（service 层矩阵校验），不直改 DB。
7. 传递依赖不写进 `backend/requirements.txt` 就等于没钉（#314，fastapi 的 starlette 曾长期浮动）；且 fastapi ≥0.141 改了 `app.routes` 结构，会让遍历路由的鉴权门禁（#256）静默空通过（#306）——升 fastapi/starlette 必须连带复核这道门禁是否还在真扫描。
