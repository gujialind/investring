# InvestRing 开发指南 (AGENTS.md)

> 为 AI 编程助手提供项目级快速参考。本文只记录**读代码发现不了**的内容：设计决策、业务不变量、组织约定与易踩坑；路由、枚举、错误码、表结构、版本号等以源码为准。

***

## 1. 项目概览

**InvestRing** 是我设计的供自己使用的投资组合管理工具，本质是一个支持净值化，多投资人的记账系统，为个人和家庭财富管理、大类资产配置提供完整的数据体系。适用持仓资产：公募基金（场内 ETF、场外 OEF/LOF、港互认基金）与现金；**股票仅作为资产分类维度存在**（`ASSET_STOCK` 用于标注股票型基金），暂不支持个股作为持仓产品。所有资产人民币计价，无汇率换算。

**Monorepo 布局**（技术栈版本明细以 `frontend/package.json` / `backend/pyproject.toml` 为准）：

| 目录                                        | 内容                                                           |
| ----------------------------------------- | ------------------------------------------------------------ |
| `backend/`                                | FastAPI + SQLAlchemy 后端；含 `app/`（应用）、`alembic/`（迁移）、`tests/` |
| `frontend/`                               | Next.js 前端（App Router，双端路由，技术栈见 `frontend/AGENTS.md`）                |
| `ir-cli/`                                 | 独立轻量 HTTP 客户端 CLI（typer + httpx）                             |
| `nginx/`、`scripts/`、`docker-compose*.yml` | 部署与运维                                                        |

**运行入口**：后端 `backend/app/main.py`（启动初始化行为读源码）；前端 `npm run dev`；`ir` CLI 的说明见 `ir-cli/AGENTS.md`。

**模块指南分层**（issue #224）：各模块的架构约定与操作级细节（怎么跑测试/E2E、种子来源、契约流程、易踩坑）在 `backend/AGENTS.md`、`frontend/AGENTS.md`、`ir-cli/AGENTS.md`；业务约束速查见 `docs/reference/business-constraints.md`（改后端业务代码时经 Rule 自动提醒）；日志规范（后端 JSON 字段/级别口径、审计 action、任务执行记录、前端 logger 用法）见 `docs/reference/logging.md`；项目版本以根 `VERSION` 为单一事实来源，版本号规范与发布流程（`scripts/release.py`）见 `docs/reference/versioning.md`。本文件只保留全局业务不变量与组织约定，不重复。

***

## 2. 核心领域模型

> 本章是所有业务规则的**单一事实来源**，其他章节与文件只引用不重复。每个聚合一小节：定义 → 关键行为 → 不变量。
> 错误码触发条件与字段级清单见 `docs/reference/business-constraints.md`；函数级公式、事务顺序与实现细节见 `backend/AGENTS.md` §1。
> 标注与引用约定：规则形态由某 issue 决策塑造的（代码可见「是什么」、不可见「为什么是这个形态」），在首次陈述处标注首个决策 issue 号，后续 issue 改变规则形态的以「#X 起…」附注；纯事实描述（字段、枚举、公式等代码可直接读出的内容）不标注。源码注释引用仓库文档时优先指向稳定符号（函数/配置键），其次章节标题关键词，避免纯章节编号——重排即静默失效。

### 2.1 概念地图：双层账本

InvestRing 是**净值化记账系统**：投资人按净值申购/赎回组合份额，组合内部再把钱配置到具体产品。**两层账本各记各的，由每日组合净值唯一连接**：

```
投资人层账本   investor_holding（份额 / 成本价 / 市值）
   ▲   只由申购·赎回驱动 —— 组合总份额仅因申赎变化
   │
   ├── 枢纽   portfolio_value_snapshot：unit_price = total_value / total_shares
   │
   ▼   由调仓·份额事件·手动重估驱动 —— 只改资产构成与总市值，不改组合份额
资产层账本   portfolio_position（净值型持仓 + 现金 + 在途）
```

由此可直接判定任何变更的落点：只动资产层的（调仓 / 份额事件 / 现金重估）不改组合份额、可能改净值；**申赎同时动两层**——既按净值改组合份额，又经配对 CASH 腿改资产层现金，但不改净值。**净值只在快照生成时被定义**：没有当日快照就没有当日净值，一切依赖净值的操作都要先有快照；快照日及其之前的事实不可再改（须先删快照重算，§2.6）。

五个核心实体：

* **组合 portfolio**：净值化记账主体，每个交易日算一次净值；初始净值 1.0000、初始份额 0。
* **投资人 investor**：组合份额的持有人；申赎是资金进出组合的**唯一**通道。
* **产品 product**：组合内部的具体资产（公募基金 / 现金 / 在途资金），价格来自外部数据源或显式流水。
* **平台 platform**：券商与基金销售渠道；持仓与现金**按平台分账**，组合净值不分平台。
* **快照 snapshot**：每日把持仓、市值净值、投资人份额定格成三张只增不改的表，是估值与可用量计算的基线。

### 2.2 组合 portfolio

```
draft ──首次申购确认──▶ active ──close──▶ closed ──reactivate──▶ active
          ▲                        │
          └─unconfirm 至零确认申购─┘
```

* 创建即 `draft`，首次申购确认自动置 `active`；已关闭组合禁止申赎/调仓，但可查历史。关闭/重开的保护条件与错误码见 `business-constraints.md`。
* **`started_at` = 现存 confirmed 申购的最小 `confirm_date`**（#180，到账事实，与激活轮次正交）：确认时写入（条件 `started_at is None`，故 reactivate 空组合后的新首购不漏设）；unconfirm 后取最小值重算；close/reactivate 不触碰。重算后若无 confirmed 申购且 `status == active` 回退 `draft`（`closed` 不回退——那是用户意图态，级联删快照只是数据修复副作用）。
* 组合自身无份额列，总份额只存在于每日 `portfolio_value_snapshot.total_shares`。
* 两个组合级开关：`auto_snapshot_enabled`（#156，默认 False、opt-in，只约束自动任务，手动生成/重算不受影响）、`display_config`（#144，持仓明细二级分组维度覆盖，JSON 只存显式覆盖项，NULL = 前端默认）。

### 2.3 投资人 investor 与份额

* 份额记在 `investor_holding`，唯一约束 `(portfolio, investor, snapshot_date)`——**投资人份额不分平台**；平台只决定现金归属，确认后与投资人不再关联。
* **份额只由申赎变化**：份额变动事件作用于产品/平台维度，不并入投资人份额账本；分红再投资只改成分基金份额。
* 市值 = 份额 × 组合净值。**成本价**首次 = 组合净值，后续 = `(old×cost + new×price)/(old + new)`。
* **可用份额必须实时计算**（#277），不能只读快照 `frozen_shares`：最新快照份额 − pending 赎回 − 快照未覆盖的 confirmed 赎回（完整口径见 `business-constraints.md`）。
* 投资人不支持强制物理删除，份额为 0 才能删。

### 2.4 产品 product 与三个市场

`product` 主键是 `(code, market)` 复合键，类型与市场取值以 `product_service.py` 为准。**场内 vs 场外是全系统最重要的二分，由 `market` 驱动**：

| 维度 | 场内 `CN_EXCHANGE` | 场外 `CN_OTC` / `HK_MUTUAL` |
| --- | --- | --- |
| 价格来源 | 收盘价（调仓录入时必填） | 单位净值（T 日；未同步则拒绝，禁止向前回退） |
| `confirm_days`（确认间隔） | 恒 0（校验强制） | 缺省推导：`CN_OTC` 1、QDII 2、`HK_MUTUAL` 1 |
| `nav_lag_days`（快照估值滞后） | 强制 0（`validate_nav_lag_days`） | **逐产品自设、不由类型/市场推导**（`product.nav_lag_days`，默认 0）；QDII/港互认惯例置 1（回填口径见 `backend/AGENTS.md` §1.4） |
| 可否 cancel | 否 | 是 |
| 数据源 | tushare / akshare | tushare 不支持 `HK_MUTUAL`，走 akshare |

* **`nav_lag_days` 与 `confirm_days` 是两套机制**：前者是 `product` 表的独立列（NOT NULL、默认 0）、**逐产品单独设置、不由产品类型/市场推导**；后者才按 `market`+`is_qdii` 推导。`is_qdii` 仅为展示标签、不参与取价分支（取价见 §2.6）。
* **一码多市场**：LOF 在场内场外各是一条独立产品记录，业务操作只给 `product_code` 时必须显式指定 `market`。
* **虚拟产品**：`CASH` 与 `IN_TRANSIT_BUY` / `IN_TRANSIT_SELL` 与基金同构（`market=""`、`confirm_days=0`），不可直接交易，只由业务流程生成（§2.5）。
* **五维分类**：产品挂 asset_class / region / style / size / segment 五个正交维度值，「必填/禁止」语义由 DB 两张规则表驱动（详见 `backend/AGENTS.md` §1.4）。分类信息只在读侧派生、**快照表无分类列**——不要在写侧或快照链路引入 `asset_type` 冗余（#128）。

### 2.5 平台 platform 与现金账本

现金**按平台分别分账**，所有现金变动**显式记录**、不从申赎/调仓隐式反推。三类影响源：

| 来源 | 记录表 | 关联方式 |
| --- | --- | --- |
| 交易（申赎/调仓/转移） | `trade` 的 CASH 腿 | `transfer_group` 关联同组 |
| 事件（现金分红等） | `share_change_event` | `cash_change` 字段，按 `ex_date` 生效 |
| 手动重估 | `manual_market_value` | 按日期**绝对替换**，不进 trade/event；优先级高于当日交易/事件，且作为后续快照增量基线；可删除，删后须重算快照回退自然值 |

各操作生成的 CASH 腿：

| 操作 | CASH 腿 | `transfer_group` |
| --- | --- | --- |
| 申购确认 | 1 条 buy（直接 confirmed） | `sub_{subscription.id}` |
| 赎回确认 | 1 条 sell（直接 confirmed） | `sub_{subscription.id}` |
| 基金买入 | 基金 buy + CASH sell（同状态/日期） | `rebal_{uuid}` |
| 基金卖出 | 基金 sell + CASH buy（同状态/日期） | `rebal_{uuid}` |
| 跨平台转移 | CASH sell + CASH buy | `{uuid}` |

* **可用现金必须实时计算**（快照基线 + 增量；无快照时降级为全量历史口径。函数级表达式见 `backend/AGENTS.md` §1.3）。**时点口径**（#70/#78）：流出（sell）的资金承诺锚定**下单日 `trade_date`**，不论 pending/confirmed；流入（buy）须 confirmed 且 `confirm_date <= T` 才计入。故 **pending 卖出不增加可用现金**，买入只能用已有可用现金，不足时须先卖后买两步操作。
* **CASH 腿来源受限**：仅由申赎、基金调仓配对、跨平台转移三条路径生成（均预置 `transfer_group`）；`trade.transfer_group` 为 NOT NULL，REST 禁止直接创建 CASH 交易。
* **在途资金**（#93）：`IN_TRANSIT_BUY` = 已扣款但基金份额未确认；`IN_TRANSIT_SELL` = 已卖出但到账未确认。两者每日独立计算、不继承前日，`cash_amount` 恒正，计入市值但不计入可用现金。
* **现金行判定一律用 `cash_amount IS NOT NULL`**（CHECK 约束保证与 `shares` 恰有其一），不看产品类型字符串；CASH 与在途行由此自然落入现金口径。
* **市值** = Σ(场内份额 × 收盘价) + Σ(场外份额 × 净值) + Σ(现金行 `cash_amount`)；**净值** `unit_price = total_value / total_shares`（4 位小数）；在途合计另记于 `portfolio_value_snapshot.in_transit_total`。

### 2.6 快照 snapshot

三张表**只增不改**（ORM `before_update`/`before_delete` 兜底，内部删除走 bulk delete 绕过），每天汇总生成一次、永不 UPDATE、保留完整历史，**生成顺序固定**：`portfolio_position`（持仓）→ `portfolio_value_snapshot`（市值净值）→ `investor_holding`（投资人份额）。

* **生成前提**：`confirm_date`/`ex_date` <= 快照日的申赎/交易/事件均已确认，不存在会影响该日的 pending 记录。
* **连续原则**：快照有前后依赖，必须严格按交易日顺序连续生成（从最新快照日的下一个交易日起），失败即停、不允许跳过。单日生成只接受「最新快照日（重建最新一日）」或「其下一个交易日」。
* **增量累加**：当日持仓与现金 = 前日基线 + 窗口内 confirmed 交易 + 事件增量 + `manual_market_value` 绝对覆盖。
* **净值严格匹配**（#96/#178，#228 起泛化）：取价日**只由产品 `nav_lag_days` 决定**——`0` 取 `price_date == snapshot_date` 当日价格，`N` 取交易日历上前第 N 个交易日；禁止向前回退，任一持仓缺价即拒绝生成（`MISSING_NAV`）。**trade 确认侧与此正交**：确认恒取 T 日价格，确认间隔由落库的 `confirm_days` 决定。
* **删除必级联**：删某日快照则其后所有快照一并删除（连续原则）。级联回退以**数据依赖**为准——`apply_date` 落在被删区间的 confirmed 申赎（以该日快照净值定价）、`entitlement_date` 落在被删区间的 confirmed 事件（基数份额回写自该日快照）自动退回 pending，配对 CASH 腿删除、基金级父事件的子记录物理删除；**交易不级联**——其取价依赖产品行情而非组合快照。**级联任一笔回退失败即整体中止、不删任何快照**（#203：异常被吞曾产生孤儿记录）。
* **重算 = 单一事务**：删任何快照前先对整区间做净值完整性预校验，失败直接拒绝、不删任何快照；随后逐交易日「删旧 → 级联回退 → 重建 → auto_confirm」全程不 commit，任一日失败即停，对外表现为「要么完整成功、要么无变化」。`auto_confirm` 每日快照后确认到期的 pending 申赎/交易/事件（重算时被级联回退的记录由此重新确认；日期键见 `backend/AGENTS.md` §1.3），单笔失败只记 `auto_confirm_failed`、不阻断当日流程。
* **零快照 + 目标日前已有确认交易**（#180）：单日 generate 拒绝（`SNAPSHOT_REQUIRES_RECALCULATE`）——增量窗口无前序快照时会退化为仅目标日，早期到账被静默漏掉（首快照「失忆」）；须用 recalculate 从最早 `confirm_date` 逐日重建。目标日即最早到账日的真正首次生成不受影响。

### 2.7 通用生命周期（三态与配对腿）

交易、申赎、事件共用 `pending / confirmed / cancelled`，均支持 confirm / unconfirm / cancel：

* **confirm**：把待定的量算实（申赎算份额/金额、trade 取 T 日价格、事件回写权益登记日份额并算变动值），并生成配对记录。
* **unconfirm**：回退至 pending。**快照保护**——若确认日（事件为 `ex_date`）及之后已有快照则拒绝（`SNAPSHOT_DEPENDENCY`），须先删快照。申赎 unconfirm 会物理删除配对 CASH 腿。
* **cancel**：仅 pending 可取消。已 confirmed 的记录不可直接改删，须先 unconfirm。
* **`transfer_group` 原子翻转**：基金腿状态/日期/金额变化时配对 CASH 腿自动同步，删除基金腿级联删除 CASH 腿。**但各腿保持创建时设定的独立 `confirm_date`**——同步不传播确认日；unconfirm 时 CASH 腿按方向回退默认值（买入扣款 T 日即 `trade_date`、卖出到账与基金确认日一致），创建时亦可显式覆盖。
* **净值稳定性**：申购、赎回、现金分红、份额拆分/合并 → 净值不变；调仓 → 净值可能变化。
* **负现金两道防线**：unconfirm 本身放行（不设前置现金守卫），负现金由消费点拦截——① 赎回确认校验平台可用现金；② 快照生成对 CASH `cash_amount < 0` 硬阻断（`NEGATIVE_CASH`）。

### 2.8 申赎 subscription

**定价时间线**：下单日 `apply_date` = T（须为交易日、且晚于最新快照日，此刻 T 日净值尚未定）→ T 日收盘生成快照、净值定档 → T+1（下一个交易日，创建时即写入 `confirm_date`）确认，按 **T 日（申请日）净值**计价、不是确认日净值。两道日期闸门——创建期要求 T 晚于最新快照日、确认期要求 T 已有快照——是同一条时间线的两端，不矛盾。申赎确认日恒为 T+1，与产品 `confirm_days` 无关（后者只作用于调仓）。

* **申购输入金额**（份额 = 金额 / 申请日净值）、**赎回输入份额**（金额 = 份额 × 申请日净值）。
* **初始净值固定 1.0000**（#179 首窗统一处理）：首窗内（申请日无快照且不存在更早的 confirmed 申购 ⟺ 申请日零持仓）按 1.0000 计价、份额 = 金额，无需行情，覆盖首日多平台/分笔申购；已有资金到账则必须有申请日快照，否则拒绝（`NAV_NOT_AVAILABLE`）。
* **乱序补录闸门**：确认日早于组合 `started_at` 则拒绝（**等于则放行**——同日多平台是生命线，#180），防回溯污染首窗定价。
* 确认时生成 1 条配对 CASH 腿（`sub_{id}`）落到指定平台的现金账上；`platform_code` 必填、决定现金归属，与投资人份额无关。

### 2.9 调仓 trade 与现金转移

调仓是组合内部的资产互换，**每条基金腿必有一条等额现金腿**（同 `transfer_group`、同状态、同交易日）。

* **金额口径**：买入 `amount = actual_amount − fee`（`actual_amount` 是含费现金支出）、`shares = amount / price`；卖出金额是**纯派生量**——有价格时 `amount = quantize(shares × price)`、`actual_amount = amount − fee`，显式传入的金额只作对账校验、落库恒用推导值。场外未传价时创建期占位，确认时按 T 日净值重算。
* **确认取价**：`confirm_date` 创建时即按 `product.confirm_days` 设定（可传参覆盖，补录用）；场内用录入的成交价，场外严格用 T 日净值（未同步则拒绝，禁止向前查找）。
* **可用量校验**：买入按**扣款平台**校验可用现金（创建与确认均是），卖出对称校验可用份额；pending 卖出不增加可用现金（§2.5）。基金买/卖可指定 `cash_platform_code` 让 CASH 腿落到另一平台（#91），免去前置的平台间现金转移。
* **跨平台现金转移**是 `transfer_group` 的特例（复用 `trade` 表，一次生成 CASH sell + buy 两腿）：
  - **当天完成**：两腿立即 confirmed，`confirm_date = transfer_date`。
  - **跨天到账**：转出腿当日 confirmed、转入腿 pending 且 `confirm_date = 下一交易日`，次日确认。**非对称状态是刻意的**——保证 D 日净值不因在途转移虚跌（转出方当日扣减、转入方在途不虚增）；在途期间转入腿不计入目标平台可用现金。
* 防重自然键、PUT 直改的字段联动与容差见 `business-constraints.md`。

### 2.10 事件 event

外部事实（分红、拆合、送股、强制调整）落地到持仓，**只改产品份额与现金，不改组合份额、不改投资人份额**。

* **两级**：**基金级**（份额拆分/合并/送股）`platform_code` 为空，确认时在 `event.market` 内按有持仓的平台自动拆子记录（`parent_event_id` 自引用）；**平台级**（现金分红/红利再投资/强制调整）每个有持仓 `(market, 平台)` 各录 1 条。**两类事件均以 `event.market` 为边界**（#461）：LOF 一码多市场时另一市场的持仓不参与计算与平台覆盖校验，两市场须分别录入事件。
* **双日期**：`entitlement_date`（权益登记日，变动基数）< `ex_date`（除息日，生效日），且均为交易日；`ex_date` 须晚于最新快照日。确认时从 `entitlement_date` 的快照回写基数份额。
* 现金分红经 `cash_change` 进现金账本；红利再投资只增成分基金份额。
* **分红再投资是唯一「金额 → 份额」的事件类型**（#425）：须先把红利金额量化到分（`quantize_amount(基数份额 × div_cash)`，与现金分红同口径、与基金公司「先按分确定应得红利、再折算份额」的算法一致），再按再投资净值折算份额并量化到 2 位——跳过中间金额量化会把金额量化损失（< 0.005 元）带进份额，跨四舍五入边界时与基金公司台账差 0.01 份。拆分/合并/送股是纯「份额 × 比例」，不涉及金额量化。
* **变动量在确认时才计算**：`shares_change` / `shares_after` / `cash_change` 对上述自动计算型事件在 pending 阶段为 NULL，读取方不得把 NULL 当 0 展示（#424：确认弹窗与列表列的行为见 `backend/AGENTS.md` §1.3）。
* 现金型产品（CASH / IN_TRANSIT）不接受份额变动；强制调整须至少一项（份额或现金）非空。
* 可用份额计算中**事件只计负向变动、正向不计入**（理由与完整口径见 `business-constraints.md`）。

### 2.11 数值口径与交易日

* **净值 4 位小数**；**份额与金额统一 2 位小数**，**三者舍入模式均为 ROUND_HALF_UP**（#428 起 4 位口径也显式承诺；此前 4 位只写在 `quantize.py` docstring 里、代码实际走 Decimal 缺省的 HALF_EVEN），负数按绝对值对称（远离零进位，符合场外基金行业惯例），量化误差计入基金财产。三个精度各有一个 helper（`quantize_shares` / `quantize_amount` / `quantize_nav`，`app/utils/quantize.py`）——**调用点一律走 helper，不写 `Decimal("0.01"/"0.0001")` 字面量**，否则缺省舍入会静默漂移。
* **量化只发生在产生点**（用户输入、确认计算、事件变动计算），读取与累加路径不量化；可用量闸门一律**先量化再精确比较**（无容差）。产生点清单与触发错误码见 `business-constraints.md`。
* 估值口径（`market_value` / `total_value` / `unit_price` / `cost_per_share`）保持 4 位，不进现金账本。
* **所有交易操作**（申购、赎回、调仓、现金进出、事件日期）**仅允许在交易日**进行，依据 `trading_calendar.is_open`。

***

## 3. 开发流程约定

> 单人 + AI 编程协作的工作流约定。**代码是唯一事实来源**；本约定只约束动作边界，不做过度流程。

### 3.1 分支模型（GitHub Flow，单长期分支，issue #211）

| 分支     | 角色            | 规则                                                                              |
| ------ | ------------- | ------------------------------------------------------------------------------- |
| `main` | 唯一长期分支（生产交付线） | 受 ruleset 保护：禁删除/禁强推 + require PR + required check `CI OK`；合入即触发 CI → CD 自动部署上线（**纯文档改动为例外**，见下） |

* **一切改动经 `feature/` → PR → `main`**：从最新 `origin/main` 不从本地旧 ref 重建。拉短命分支（`feature/<issue号>-<简述>`、`hotfix/<issue号>-<简述>`；AI 代理可用 `trae/xxx`、`codex/xxx` 前缀），

* **文档类例外（#456，对 #211 规则的修订而非推翻）**：`ci.yml` 的 `push` 触发器带 `paths-ignore`（`**/*.md`、`docs/**`），纯文档改动合入**不重建镜像、不重部署**。两条不可动摇的边界：**`pull_request` 触发器不得加任何 `paths-ignore`**（ruleset 的 required check `CI OK` 会在 docs-only PR 上永不产出 → PR 永久卡住，与 #377 同型死锁），故**合入前的完整 CI 验证一点没少**，省掉的只是合入后在 main 上重复跑一次；模式只做后缀/目录式匹配，绝不写宽（写宽会让代码改动静默不跑 CI）。副作用：`deploy/YYYYMMDD-SHA` 标签不再每次合入都推进（见 `docs/reference/versioning.md` §3）。审查侧的对应处置见 `docs/reference/code-review.md` §4.6。

* 手动部署（`deploy.yml` `workflow_dispatch`）只接受已有镜像 tag（回滚/重部署）。

### 3.2 Issue 约定

* **新功能 / 大改 / 涉及业务规则或 DB 迁移**：必须先提 issue 再动手；修 bug 若影响面大或需留痕，同样先提 issue。

* 按模板提交 `.github/ISSUE_TEMPLATE/`（bug\_report / feature\_request / chore）。

* **标题前缀与 Conventional Commits 对齐**：`[bug]` / `[feat]` / `[chore]`（含文档/运维类）。


### 3.3 PR 约定

* 使用 PR 模板 `.github/PULL_REQUEST_TEMPLATE.md`。

* **审查标准与流程**见 `docs/reference/code-review.md`（L2 语义审查清单、🔴/🟡/💭 分级与处置规则、PR 规模护栏）。分工边界：CI 已强制的 lint/类型/构建/测试/契约漂移属 L1，**审查评论不重复 L1**。

* **改 PR base 不触发 CI**：`on: pull_request` 默认只订阅 opened/synchronize/reopened，retarget（`edited` 事件）后 checks 为空、required check `CI OK` 永远无法满足；须 close/reopen PR 或推新 commit 重新触发（#377 实踩）。

### 3.4 commit 信息

* Conventional Commits 风格：`fix:` / `feat:` / `docs:` / `refactor:` / `chore:`，附简短说明并尽量带 issue 号（如 `fix(snapshot): 快照净值严格匹配 (#96)`）。

### 3.5 AI AGENT铁律

1. **改完必须验证**：本地跑**改动影响面**的测试且绿即可，不要求全量（全量回归由 CI 的 `CI OK` 在合入前兜底；影响面圈定宁宽勿窄，如动 `snapshot_service` 应连带快照/申赎/调仓相关测试；影响面圈定程序见 `backend/AGENTS.md`「跑测试」节）+ 能说明改动影响；验证不了的改动不提交。
2. **排查/审查中发现的问题只提 issue，不直接改代码**，由任务所有者决定修复方式。
3. **不得引入未要求的依赖或表结构改动**；涉及 DB 变更必须同步提供 Alembic 迁移脚本。
4. **仓库文档（README/AGENTS/runbook）随代码同一次提交更新**；设计决策与方案记录进 issue 讨论。
5. 当你执行一项任务发现有任何执行细节不明确时，你必须向我提问，而不是自做主张，在我回答之后仍有不明确的行细节时，你需要向我追问，直到了解了所有细节。
