# 业务规则、约束与边界

> 本文件是完整领域规则的唯一正文；[根指南](../../AGENTS.md) 只提供导航和摘要。错误码的「码名 ↔ 触发条件 ↔ HTTP 状态」穷尽清单见[错误码总表](#错误码总表)，由 `backend/tests/unit/test_error_codes_doc_sync.py` 守门。源码说明实现事实，本文说明业务意图、边界和理由；冲突处理遵循[文档规范](documentation.md#doc-ownership)。

<a id="rule-ledger"></a>
## 双层账本

InvestRing 是净值化记账系统：投资人按净值申购/赎回组合份额，组合内部再配置具体产品。**两层账本各记各的，由每日组合净值唯一连接**：

```text
投资人层：investor_holding（份额 / 成本价 / 市值）
    ↕ 只由申购、赎回驱动组合份额变化
枢纽：portfolio_value_snapshot（unit_price = total_value / total_shares）
    ↕ 资产构成和市值由调仓、份额事件、手动重估驱动
资产层：portfolio_position（净值型持仓 + 现金 + 在途）
```

只动资产层的调仓、份额事件、现金重估不改组合份额，可能改净值；**申赎同时动两层**：按净值改组合份额，经配对 CASH 腿改资产层现金，但不改净值。**净值只在快照生成时定义**：没有当日快照就没有当日净值，依赖净值的操作须先有快照；快照日及之前的事实不可再改，须先删快照重算。

核心实体：组合是每日净值化记账主体（初始净值 1.0000、初始份额 0）；投资人持有组合份额，申赎是资金进出组合的唯一通道；产品是组合内部资产，价格来自外部数据源或显式流水；平台是券商/基金销售渠道，持仓与现金按平台分账而组合净值不分平台；快照将每日持仓、市值净值和投资人份额定格，作为估值与可用量基线。

<a id="rule-investor"></a>
## 投资人与份额

- 份额记在 `investor_holding`，唯一约束 `(portfolio, investor, snapshot_date)`；**投资人份额不分平台**，平台只决定现金归属，确认后与投资人不再关联。
- 份额只因申赎变化；产品/平台维度的份额事件不并入投资人账本，分红再投资只改成分基金份额。
- 市值 = 份额 × 组合净值。成本价首次取组合净值，后续为 `(old×cost + new×price)/(old + new)`。
- 可用份额必须实时计算（#277），不能只读快照冻结份额，见[可用量口径](#可用量口径)。份额为 0 才能删除投资人，不支持强制物理删除。

<a id="rule-product"></a>
## 产品与市场

`product` 主键为 `(code, market)`；类型与市场取值以 `product_service.py` 为准。**场内与场外的业务分支由 market 驱动**：

| 维度 | 场内（`market="CN_EXCHANGE"`） | 场外（`market="CN_OTC"` / `market="HK_MUTUAL"`） |
| --- | --- | --- |
| 价格来源 | 收盘价，调仓录入时必填成交价 | T 日单位净值，未同步则拒绝，不向前回退 |
| `confirm_days` | 恒 0，校验强制 | 缺省推导：境内场外 1、QDII 2、港互认 1 |
| `nav_lag_days` | 强制 0 | 逐产品独立设置，默认 0；QDII/港互认惯例置 1 |
| cancel | 不允许 | 允许 |
| 数据源 | tushare / akshare | tushare 不支持港互认，走 akshare |

- **估值滞后与确认间隔正交**：`nav_lag_days` 是 NOT NULL 独立列，不由产品类型/市场推导；只有 `confirm_days` 按 market + `is_qdii` 推导。`is_qdii` 只是展示标签、不参与取价分支。历史回填边界见 [backend 指南](../../backend/AGENTS.md) 的数据模型说明。
- **一码多市场**：LOF 的场内/场外是两条独立产品记录，只给 product_code 时必须显式指定 market。
- **虚拟产品**：`CASH`、`product_code="IN_TRANSIT_BUY"` / `product_code="IN_TRANSIT_SELL"` 与基金同构（`market=""`、`confirm_days=0`），不可直接交易，只由业务流程生成。
- **五维分类**：asset_class / region / style / size / segment 相互正交，必填/禁止关系由 DB 两张规则表驱动。分类仅在读侧派生、快照表无分类列；不得在写侧或快照链引入 asset_type 冗余（#128）。

<a id="rule-cash"></a>
## 平台与现金账本

现金按平台分账，变动必须显式记录，不从申赎/调仓隐式反推。

| 来源 | 记录 | 关联与生效 |
| --- | --- | --- |
| 申赎/调仓/转移 | trade 的 CASH 腿 | transfer_group 关联 |
| 现金分红等事件 | share_change_event | cash_change，按 ex_date 生效 |
| 手动重估 | manual_market_value | 按日期绝对替换，不进 trade/event；高于当日交易/事件，作为后续增量基线；删除后须重算快照回退自然值 |

下表中 T 为下单日、C 为基金确认日、A 为现金到账日。

| 操作 | CASH 腿 | transfer_group |
| --- | --- | --- |
| 申购确认 | 1 条 buy，直接 confirmed | `sub_{subscription.id}` |
| 赎回确认 | 1 条 sell，直接 confirmed | `sub_{subscription.id}` |
| 基金买入 | 创建即 CASH sell confirmed，现金日 T；基金腿 pending | `rebal_{uuid}` |
| 基金卖出 | 创建时无 CASH 腿；确认时 CASH buy confirmed，trade_date=C、confirm_date=A | `rebal_{uuid}` |
| 跨平台转移 | CASH sell + CASH buy | `{uuid}` |

- **可用现金实时计算**：快照基线 + 增量；无快照则基线为 0、全量流水各计一次（#515）。流出 sell 的资金承诺锚定下单日 trade_date（pending/confirmed 均计）；流入 buy 须 confirmed 且 confirm_date ≤ T 才计。pending 卖出不增加可用现金，不足时须先卖后买两步操作（#70/#78）。自身扣款加回见[可用量口径](#可用量口径)。
- **CASH 腿来源受限**：仅申赎、基金调仓配对、跨平台转移三条路径生成，均预置 transfer_group；该列 NOT NULL，REST 禁止直接创建 CASH 交易。
- **在途资金**（#93，#493 起为单腿确认口径）：买入在途为 CASH sell 已 confirmed 且现金日 ≤ D、基金买入腿尚未在 D 生效（pending 或确认日 C > D）；卖出在途为基金 sell 已 confirmed 且 C ≤ D、CASH buy 尚未生效（pending 或到账日 A > D）。两腿均 confirmed 的跨期窗口与跨天现金转移规则保留；配对方向显式限定、cancelled 腿一律排除，金额按现金腿平台归属。两类在途每日独立计算、不继承前日，cash_amount 恒正，计入市值但不计入可用现金。
- **快照现金行**按 `cash_amount IS NOT NULL` 判定（CHECK 保证与 shares 恰有其一），不看产品类型字符串，CASH 与在途均适用。**交易 CASH 腿**则按 `product_code == "CASH"` 判定；不能将两种数据对象的判据混用。
- 市值 = Σ(场内份额 × 收盘价) + Σ(场外份额 × 净值) + Σ(现金行 cash_amount)；净值 `unit_price = total_value / total_shares`（4 位），在途合计另记 `portfolio_value_snapshot.in_transit_total`。

<a id="rule-snapshot"></a>
## 快照

三表只增不改（ORM before_update/before_delete 兜底，内部删除走 bulk delete），每日汇总、保留完整历史，生成顺序固定：`portfolio_position` → `portfolio_value_snapshot` → `investor_holding`。

- **前提**：confirm_date/ex_date ≤ 快照日的申赎/交易/事件均已确认，不存在影响该日的 pending 记录。
- **生成前到期补确认**（#495）：生成依赖校验前自动确认「最新快照日 < confirm_date ≤ 目标日」的 pending 申赎（`auto_confirm_before_snapshot`），与生成后 `auto_confirm_after_snapshot`（按 apply_date 匹配）互补——当天补录（申请日 == 最新快照日、确认日 == 目标日）由此在 pending 校验前消化，否则其 confirm_date == 目标日会被 pending 校验阻断、而生成后 auto_confirm 只在快照落地后运行，死锁。confirm_date ≤ 最新快照日的历史/异常 pending 不在窗口，仍阻断、须人工处理（防静默漏记，与零快照守卫同族）。
- **连续原则**：严格从最新快照日的下一交易日起连续生成，失败即停，不能跳日；单日生成只接受最新日重建或下一交易日。
- **增量累加**：当日持仓/现金 = 前日基线 + 窗口内 confirmed 交易 + 事件增量 + manual_market_value 绝对覆盖。
- **严格取价**（#96/#178，#228 起泛化）：只由产品 nav_lag_days 决定取价日，0 取当日、N 取交易日历上前第 N 个交易日；缺指定日行情必须报 `MISSING_NAV`，不得回退。调仓确认始终取 T 日价格、按落库 confirm_days 决定间隔，与快照估值正交。
- **删除必级联**：删某日及以后全部快照。按数据依赖回退：apply_date 落在区间的 confirmed 申赎、entitlement_date 落在区间的 confirmed 事件退回 pending；配对 CASH 腿删除，基金级父事件的子记录物理删除。交易依赖产品行情而非组合快照，**不级联**。任一笔回退失败整体中止、不删任何快照（#203）。
- **重算为单一事务**：先对整区间做行情完整性预校验，再逐交易日删旧、级联回退、重建、auto_confirm，全程不 commit；任一天重建失败即停，对外完整成功或无变化。每日 auto_confirm 处理到期 pending 申赎、事件、跨天现金转移，**不含调仓**（#493/#471）；被级联回退的记录由此重确认，日期键见后端指南的 `auto_confirm_after_snapshot` 说明。允许的单笔失败记 auto_confirm_failed 并继续，不能与需要整体回滚的失败混同。
- **零快照但目标日前已有确认交易**（#180）：单日 generate 报 `SNAPSHOT_REQUIRES_RECALCULATE`，须从最早 confirm_date 逐日重建，避免首快照漏早期到账；目标日即最早到账日的真正首次生成不受影响。

实现入口见 [snapshot_service.py](../../backend/app/services/snapshot_service.py)；代表测试见 [test_snapshot_service.py](../../backend/tests/unit/test_snapshot_service.py)。

## 可用量口径

冻结份额/现金必须**实时计算**，不能仅读快照 `frozen_shares` / `frozen_amount`。

* **基金可用份额**（#277）= 最新快照份额 − SUM(pending 卖出) − SUM(快照未覆盖的 confirmed 卖出) + SUM(快照未覆盖的 confirmed 事件**负向** `shares_change`，`ex_date > 最新快照日` [≤ T])。
  - 事件增量**只计平台级行**（`platform_code IS NOT NULL`）——基金级父记录持汇总值，父子同计会双算。
  - **正向变动不计入**：入快照前保守低估，防事件被撤销后已放行的卖出成为事实超卖。
* **投资人可用份额** = 最新快照份额 − SUM(pending 赎回) − SUM(快照未覆盖的 confirmed 赎回)。份额变动事件不并入——组合份额仅因申赎变化，事件作用于基金/平台维度、不改投资人份额账本。
* **可用现金**的业务时点见[现金账本](#rule-cash)，两种实现入口及不可混用的边界见[后端核心服务](../../backend/AGENTS.md#13-核心服务)（`calculate_available_cash` / `compute_cash_balance`）。
* **自身扣款加回**（#493，`validate_buy_cash_with_addback` 内）：校验买入支出时把**自身组内 CASH sell 腿**（pending/confirmed，买入扣款腿创建即 confirmed）的金额加回一次——它已在本时点的余额口径中被扣掉（快照基线或快照后增量），不加回会把合法确认误拒。**查询时点之后**的扣款（`trade_date > as_of`）尚未被计提，**不加回**；cancelled 腿不计入；扣款平台取自身腿的实际平台（跨平台买入不因日期前移而丢失）。
* 卖出/赎回输入份额**先量化到 2 位再与可用份额精确比较**（无容差），超出报 `INSUFFICIENT_SHARES`；买入/转移金额同理先量化再与可用现金精确比较，不足报 `INSUFFICIENT_CASH`。`skip_available_check` 仅限 auto\_confirm 路径。

<a id="rule-subscription"></a>
## 申购赎回

* 申购输入**金额**（份额 = 金额 / 申请日净值）；赎回输入**份额**（金额 = 份额 × 申请日净值）。

* **定价时间线**：申请日 T 必须是交易日，且其确认日（T+1）必须晚于最新快照日——T 日快照已生成后仍允许补录 `apply_date == T` 的申赎（T 日真实下单、收盘后快照才生成的场景），按 T 日净值计价、T+1 确认生效；T 日收盘生成快照定净值，T+1 确认仍按 **T 日申请日净值**计价，不是确认日净值。创建期“确认日晚于最新快照”和确认期“申请日已有快照”是同一时间线的两端。

* **确认日恒为申请日的下一交易日（T+1）**，创建时即写入，与产品 `confirm_days` 无关（后者只作用于调仓）；pending 记录的 `confirm_date` 是预计确认日。确认生成一条 `sub_{id}` 配对 CASH 腿，平台决定现金归属，不改变投资人份额不分平台的口径。

* **首窗判定**（#179）：确认时申请日无快照**且**不存在 `confirm_date <= apply_date` 的 confirmed 申购（等价于申请日零持仓、净值结构性恒 1.0）→ 按 1.0000 计价，申购份额等于金额、无需行情，覆盖首日多平台及分笔申购；已有资金到账却无申请日快照 → `NAV_NOT_AVAILABLE`（禁止回退旧净值或当前净值）。

* **乱序补录**（#180）：确认日早于组合 `started_at` → `CONFIRM_BEFORE_STARTED`（等于放行，以支持同日多平台申购）；乱序单 auto\_confirm 记 `auto_confirm_failed`，需手动按序处理。

* **创建期日期闸门**（#495）：记账不变量是确认日必须晚于最新快照日（`DATE_BEFORE_SNAPSHOT`）——快照增量窗口按 `confirm_date` 过滤，申请日更早的申赎其确认日必然落入已冻结区间、会静默漏记；`apply_date` == 最新快照日当天放行（恒 T+1 下两者等价，取对准真实不变量的表述）。调仓、现金转移、份额事件的同源闸门不在本次放宽范围，见各自章节。

* 申赎必填 `platform_code`（现金归属平台）；申请日快照要求按上述首窗判据处理，不以“是否第一笔申购”代替。

<a id="rule-trade"></a>
## 调仓交易

调仓是组合内部资产互换，不改组合份额、可能改净值。每条基金腿最终都有等额 CASH 腿，金额恒等基金腿 actual_amount；#493 起状态与生效日可以不同，卖出确认前只有基金腿是刻意的半成品组，不是数据缺失。

* **跨平台现金转移**复用 trade 表：当天完成时 sell/buy 两腿直接 confirmed、confirm_date=transfer_date；跨天时转出腿当日 confirmed，转入腿 pending 且 confirm_date=下一交易日。非对称状态保证转出当日净值不因在途虚跌；在途不计入目标平台可用现金。
* **调仓不参与 auto_confirm**（#493 决策 6 / #471）：自动任务、追平和重算均不确认调仓基金腿，跨天转移自动确认排除 rebal_ 组；到期 pending 调仓阻断快照，须人工确认。申赎、事件和跨天现金转移的自动确认不变。

* 金额：买入 `amount = actual_amount − fee`、`shares = amount/price`；卖出 `amount = actual_amount + fee`。**卖出金额为纯派生量**（#190）：有价格时 `amount = quantize(shares × price)`、`actual_amount = amount − fee`；创建时显式传入的 amount/actual\_amount（两参同义、`actual_amount` 优先）仅作一致性校验（差值超 0.01 报 `AMOUNT_MISMATCH`），落库恒用推导值——金额即 shares/price/fee 的「校验和」，用于对账；无价格（场外未传价）时创建期占位，确认按 T 日净值重算。

* **PUT 直改与创建同口径**（#182）：编辑 pending 交易时 buy 的 amount/actual\_amount 视为含费现金支出（`actual_amount` 优先），service 层联动重算净额列并镜像 CASH 腿；sell 有价格时与创建同口径（#190）：按新 shares/price/fee 重推导、显式金额仅作对账（场内超差拒绝、场外静默），无价格占位单仍输入为准；改金额/份额/日期实时校验可用量（加回自身 pending 旧值）、非交易日直接拒绝不静默滚交易日、自然键防重排除自身（无 `allow_duplicate`）；CASH 腿仅 notes 放行；校验全部通过前零写入。

* **确认的计划与写入分离**（#493）：`compute_confirm_plan`（NAV/份额/金额 + 现金腿计划）供 confirm 与 preview 共用，**纯只读**；confirm 在全部校验通过后才 setattr，配对腿校正的前置校验也在 setattr 之前（拒绝即零写入）。preview 不构腿、不改 ORM、不写审计，`sync_nav` 只属于 confirm。

* **跨平台现金腿**（#91，#493 起按方向分时机）：基金买可传 `cash_platform_code`（= 扣款平台，缺省同基金腿），免去前置平台间转移；基金卖**创建时不接受到账信息**，到账平台与到账日一律在 **confirm/preview** 传 `cash_platform_code` / `cash_confirm_date`（缺省 = 基金腿平台 / 本次有效确认日 C），创建期传入报 `CASH_PLATFORM_NOT_ALLOWED` / `CASH_CONFIRM_DATE_NOT_ALLOWED`。买入的扣款日固定为下单日 T（`cash_confirm_date` 只接受等于 T），确认期不允许借机改扣款平台/日期。

* **买入创建即扣款**（#493）：创建买入时配对 CASH sell 腿直接 `confirmed`、`trade_date = confirm_date = T`（基金腿仍 pending）——D 日快照不再被 pending 校验阻断，现金当日实扣并等额记在途（IN\_TRANSIT\_BUY）。确认时**核验**该腿状态/金额：不一致且扣款日**未被快照消费**时校正到一致，已被消费则 `SNAPSHOT_DEPENDENCY` 拒绝（决策 5，不静默改写历史）。

* **卖出确认时建到账腿**（#493）：创建只建基金腿（组号照常分配，`rebal_` 前缀）；确认按本次**确认后净额**新建 CASH buy 腿（`status=confirmed`、`trade_date` = 基金确认日 C、`confirm_date` = 到账日 A，缺省 A=C）。C ≤ D < A 的快照记等额在途（IN\_TRANSIT\_SELL），A 日起转 CASH。

* **整组生命周期口径**（#493）：cancelled 整组回退（含 CASH 腿）；删除基金腿级联删除 CASH 腿。unconfirm 买入组**保留 CASH 扣款腿 confirmed**（扣款既成事实，日期金额不动），卖出组删除 CASH 腿、回到未创建态，再确认沿用原组号重建。扣款生效锚定下单日 T，不向 CASH 腿传播基金 confirm_date。**组级快照保护**：组内任一腿确认日（缺省下单日）及之后已有快照 → 拒绝 update/cancel/delete/unconfirm（`SNAPSHOT_DEPENDENCY`，`details.from_date` 给出需删除的起始日）；改日期时**新值**一并纳入保护。调仓 CASH 腿只能由基金腿驱动，直接对其 confirm/unconfirm/cancel/delete 或用 PUT 改财务字段报 `CASH_TRADE_FORBIDDEN`（**notes 例外**）。

* **confirmed 卖出的到账日窄例外**（#493）：PUT 只额外开放 `cash_confirm_date`（只同步配对 CASH 腿的 `confirm_date`，不调用基金金额重算）；**不传即保持、显式 null 报 `INVALID_PARAM`、混入任何其他字段整体拒绝**（`CANNOT_MODIFY_CONFIRMED`）。pending 交易传 `cash_confirm_date` 同样 `INVALID_PARAM`。**notes-only 是非会计更新**：pending/confirmed 均可改，不重算金额、不镜像配对腿、不触发快照保护（cancelled 仍拒）。

* **可用现金时点口径**：pending 卖出不增加可用现金；买入按扣款平台校验可用现金（[现金账本](#rule-cash)），确认时不足同样拒绝（卖出确认对称校验份额；`skip_available_check` 仅限 auto\_confirm 路径）。现金转移按 `transfer_date` 验资、available-cash 接口按当天时点（#493，`calculate_available_cash(as_of_date=…)`）。

* **确认取价**：`confirm_date` 创建时即按 `product.confirm_days` 设定（`confirm` 可传参覆盖，补录用）；场内用成交价（录入时必填）、场外严格用 T 日净值（含 QDII；未同步则拒绝，禁止向前查找；可传 `sync_nav`/`--sync-nav` 在 MISSING\_NAV 时自动回填净值并重试一次，#90）。场外确认可选传入价格，仅与 T 日净值做一致性校验（不一致 `PRICE_NAV_MISMATCH`），不覆盖净值。快照估值侧与此正交：按产品 `nav_lag_days` 取价（`0`=当日、`N`=前第 N 个交易日），详见[快照规则](#rule-snapshot)。

* 防重：同组合/产品/市场/平台/方向/交易日且金额（买）或份额（卖）相同的 pending/confirmed 交易，未传 `allow_duplicate` 报 `DUPLICATE_TRADE`（cancelled 不算）。

* 仅给 product\_code 且一码多市场（LOF）须显式指定 market（`MARKET_AMBIGUOUS`，`details.available_markets` 列可选项）；场内 trade 不可 cancel。

* `trade.transfer_group` NOT NULL；REST 直接创建 `product_code="CASH"` 的交易 → `CASH_TRADE_FORBIDDEN`。

<a id="rule-event"></a>
## 份额变动事件

分红、拆合、送股、强制调整等外部事实只改变产品份额和现金，不改变组合份额或投资人份额。

* **分红再投资**是唯一“金额 → 份额”的事件（#425）：先 `quantize_amount(基数份额 × div_cash)` 确定到分的红利金额，再除以再投资净值并量化份额到 2 位，与现金分红及基金公司台账同口径；跳过中间金额量化可能跨舍入边界差 0.01 份。拆分/合并/送股仅为“份额 × 比例”。
* **确认时才计算变动量**（#424）：自动计算型事件在 pending 时 shares_change / shares_after / cash_change 为 NULL，读取方不得当 0 展示。确认从权益登记日快照回写基数份额；现金分红经 cash_change 入现金账本，再投资只增加成分基金份额。
* **分级**：基金级（`share_split`/`share_merge`/`bonus_share`，`platform_code` 空，确认时在 `event.market` 内按平台自动拆子记录）；平台级（`cash_dividend`/`reinvest_dividend`/`forced_adjustment`，每个有持仓 `(market, 平台)` 各录 1 条）。两类事件均以 `event.market` 为边界（#461）：LOF 一码多市场时另一市场的持仓不参与计算与覆盖校验，两市场须分别录入。

* 日期约束：`ex_date > entitlement_date` 且均为交易日；`ex_date` 须晚于最新快照日。平台级未全覆盖该 market 下有持仓平台默认阻断（`PLATFORM_NOT_COVERED`），`force_cover=true` 降为 warning。

* 输入校验（#279，创建/更新/确认三路径同口径）：`forced_adjustment` 必须至少一项（`shares_change`/`cash_change`）非空，否则 `EMPTY_ADJUSTMENT`；现金型产品（`product_type` 为 CASH/IN_TRANSIT）不接受份额变动（结构型事件无条件拒、其余类型显式 `shares_change` 拒，`SHARES_CHANGE_ON_CASH_PRODUCT`）。

* **market 补全口径**（#258，与调仓 #83 同口径）：创建时 `market` 省略/空串按产品唯一市场自动补全；一码多市场（LOF）报 `MARKET_AMBIGUOUS`；产品不存在报 `PRODUCT_NOT_FOUND`——杜绝 `(product_code, market)` 复合外键违约 500。

* 持仓存在性防线（#278）：`forced_adjustment` 确认时精查权益登记日 `(产品, market, 平台)` 持仓行，无行拒绝 `POSITION_NOT_FOUND`（LOF market 误填提前快失败）；快照生成对份额事件硬拒绝 `POSITION_NOT_FOUND`：①指向不存在的持仓行（不静默新建 0 份额行）、②作用于现金行（`cash_amount IS NOT NULL` 的行存在但不得承载份额变动），负向调整打空持仓行产出 `event_zeroed_position` 告警（不阻断）。

<a id="rule-portfolio"></a>
## 组合管理

* 创建为 draft，首次申购确认置 active；active 可 close 为 closed，closed 可 reactivate。已关闭组合禁止申赎/调仓、允许查历史。
* **started_at** 为现存 confirmed 申购的最小 confirm_date（#180），代表到账事实、与激活轮次正交。确认时在 started_at 为空时写入（覆盖空组合 reactivate 后首购）；unconfirm 后重算最小值，close/reactivate 不修改。无 confirmed 申购且状态 active 时回退 draft，closed 不回退——关闭是用户意图，级联删快照只是数据修复。
* 组合自身无份额列，总份额只在每日 `portfolio_value_snapshot.total_shares`。
* **auto_snapshot_enabled**（#156）默认 False、opt-in，只约束自动任务，不影响手动生成/重算。**display_config**（#144）保存持仓明细二级分组的显式覆盖项，NULL 采用前端默认。
* 删除投资人的份额保护见[投资人与份额](#rule-investor)。

* 存在 pending 申赎或 pending trade 时关闭 → `PENDING_TRANSACTIONS_EXIST`；已关闭再关 → `PORTFOLIO_ALREADY_CLOSED`；非 `closed` 调 reactivate → `PORTFOLIO_NOT_CLOSED`。

* 持仓表禁止手动 CRUD（`POSITION_TABLE_PROTECTED`），现金修正走 `cash-position` 覆盖层（见下）。

<a id="rule-lifecycle"></a>
## 生命周期通用错误码

交易、申赎、事件共用 pending / confirmed / cancelled：confirm 计算待定量并生成配对记录；unconfirm 回到 pending；cancel 仅允许 pending，confirmed 不可直接改删、须先 unconfirm。

- **配对腿并非无条件同状态或同 trade_date**（#493）；调仓组级操作矩阵、日期与备注例外见[调仓规则](#rule-trade)。申赎 unconfirm 物理删除配对 CASH 腿。
- **净值稳定性**：申购、赎回、现金分红、份额拆分/合并不改变净值，调仓可能改变净值。
- **负现金两道防线**：unconfirm 不设前置现金守卫；消费点由赎回确认校验平台可用现金、快照生成拒绝负 CASH 余额（`NEGATIVE_CASH`）。

* 已 confirmed 的 trade/subscription 直接 PUT → `CANNOT_MODIFY_CONFIRMED`；直接 DELETE → `CANNOT_DELETE_CONFIRMED`（须先 unconfirm）。#493 的两个窄例外：**notes-only** 对 pending/confirmed 一律放行（非会计更新）；**confirmed 卖出**额外只开放 `cash_confirm_date`（见「调仓交易」节）。

* **三态状态门的通用拒绝码 `INVALID_STATUS`**（与上条是同一次 dispatch 的兄弟分支）：confirm 与确认预览要求 `pending`、cancel 要求 `pending`、unconfirm 要求 `confirmed`、PUT 拒绝 `cancelled`。**不对称点**：份额变动事件的 PUT 只拒 `confirmed`（`cancelled` 事件放行），与调仓/申赎不同。**确认预览的三条路径同码不同层**（#424）：申赎 preview 走子类 `InvalidStatusError`、调仓 preview 在 router 抛 `HTTPException`、事件 preview 由 service 抛同码 `BusinessError`（与事件 confirm 共用同一句常量消息）。逐站点触发条件见下文错误码总表。

* 场内 trade cancel → `CANNOT_CANCEL_EXCHANGE`。

* **快照保护**（#493 起为**组级**口径）：unconfirm/cancel/delete/update 时，**组内任一腿**的会计生效日（`confirm_date`，缺省 `trade_date`）及之后已有该组合快照 → `SNAPSHOT_DEPENDENCY`（`details.from_date` = 需删除的起始日）；申赎仍按自身确认日（事件按 `ex_date`）。confirm 侧保护**有效基金确认日 C**（C 及之后有快照即拒）；买入待校正的扣款腿则保护其扣款日。

* 非交易日操作 → `NON_TRADING_DAY`。

* 快照生成对 CASH `cash_amount < 0` 硬阻断 → `NEGATIVE_CASH`。

## 错误码总表

> **穷尽口径**：本表收录 `backend/app` 全部在用错误码，**无刻意省略**——它是「码名 ↔ 触发条件 ↔ HTTP 状态」的唯一事实来源（全仓无码注册表，码为抛出点的字面量）。由 `backend/tests/unit/test_error_codes_doc_sync.py` AST 扫描在用码与本表首列比对守门：**加码 / 改名 / 删码必须同一次提交更新本表**，缺项与死码名都判红。
>
> **HTTP 列**取抛出点实际状态码：`BusinessError` 默认 422、`NotFoundError` 固定 404，显式 `http_status=` 与 router 的 `HTTPException(status_code=...)` 优先。**抛出位置**为相对 `backend/app/` 的**稳定符号锚点**（`文件::函数/类名`；嵌套符号用点分路径、如 `外函数.内函数`；同文件续锚省略文件前缀、写 `::符号`；同码多站点只列代表性的若干处，完整集合以守门测试的 AST 结果为准）。**禁止行号锚点**（#521）——行号随代码演进静默漂移，符号存在性与「该符号行范围内确实抛出本码」由 `backend/tests/unit/test_error_codes_doc_sync.py` 的 AST 守门逐一验证。
>
> 两个**非 HTTP** 形态：`SESSION_ABORTED` 与 `VALIDATION_FAILED` 的逐日条目形态，是重算 / catch-up 响应里 `results[].errors` 与 `auto_confirmed` 条目的 `code`（响应仍 200，见[快照规则](#rule-snapshot)的重算事务约定）。

| 码 | HTTP | 触发条件 | 抛出位置（取样） |
| --- | --- | --- | --- |
| `ACCOUNT_LOCKED` | 403 | 账户处于登录失败锁定期（`utils/security.py` 进程内 `login_failure_tracker`：连续失败达 5 次锁 15 分钟）。登录端点两种触发：请求时已锁定；本次密码错误正好把计数推到阈值。已持 Token 的请求经 `get_current_user` 时 `is_account_locked` 为真同样拒绝 | dependencies.py::get_current_user; routers/auth.py::login |
| `ALREADY_EXISTS` | 400 | 创建时自然键已存在（均显式 `http_status=400`）：投资人 `code`、组合 `code`、产品 `(code, market)` 复合键、维度值 `code`；另在产品 PUT 改 `market` 时目标 `(code, new_market)` 已被另一条产品占用 | services/product_service.py::create_product; services/portfolio_service.py::create_portfolio |
| `AMOUNT_MISMATCH` | 422 | 卖出调仓且已传价格时，显式输入的到账金额与推导值 `quantize(shares×price) − fee` 相差 > 0.01；仅场内 `CN_EXCHANGE` 做此对账，场外传价不对账（确认时按 T 日净值重算覆盖） | services/trade_service.py::_derive_sell_amounts |
| `BULK_DELETE_FAILED` | 500 | `DELETE /api/snapshots/{portfolio}/bulk/{from_date}` 的逐日删除循环中，某日抛出**非** `BusinessError` 的异常（`BusinessError` 如级联回退失败原样透传）；逐日 commit 语义下已成功的日期保留 | routers/snapshots.py::delete_snapshots_bulk |
| `CALENDAR_NOT_SYNCED` | 422 | 交易日历覆盖不到所需日期：`/next`、`/prev` 查询返回 None 或回退等于 `from_date`（日历耗尽时 `get_next_trading_day` 返回入参本身）；`/is-open` 无该日 calendar 行；快照 catch-up / generate-next 时最新快照日的下一交易日为空或不晚于最新快照日 | routers/trading_calendar.py::get_next_trading_day; services/snapshot_service.py::catch_up_snapshots |
| `CANNOT_CANCEL_EXCHANGE` | 422 | cancel 调仓交易时 `trade.market == "CN_EXCHANGE"`（场内不可取消，须 PUT 改字段或 DELETE 重建）；状态门先于此判（非 pending → `INVALID_STATUS`） | services/trade_service.py::cancel_trade |
| `CANNOT_DELETE_CONFIRMED` | 422 | 删除申赎或调仓交易时 `status == "confirmed"`（须先 unconfirm 回 pending）；pending/cancelled 放行 | services/subscription_service.py::delete_subscription; services/trade_service.py::delete_trade |
| `CANNOT_MODIFY_CONFIRMED` | 422 | PUT 直改申赎 / 调仓交易 / 份额变动事件时 `status == "confirmed"`（含基金级子记录，其恒为 confirmed）；同函数内 `cancelled` 另抛 `INVALID_STATUS`（事件 PUT 例外，见该码）。#493 两个窄例外**不**落本码：调仓 notes-only 对 confirmed 放行；confirmed **卖出**且字段子集 ⊆ `{notes, cash_confirm_date}` 且真的传了 `cash_confirm_date` 时走「到账日修正」分支（混入其他字段则整体拒绝回落到本码） | services/trade_service.py::update_trade; services/subscription_service.py::update_subscription |
| `CANNOT_UNCONFIRM_CHILD` | 422 | 对 `parent_event_id` 非空的基金级事件子记录单独 unconfirm（须对父记录执行，由父级联删除子记录）；判在 `status != "confirmed"` 之后、快照保护之前 | services/share_change_event_service.py::unconfirm_share_change_event |
| `CASH_TRADE_FORBIDDEN` | 422 | 现金腿不得旁路操作：创建调仓时解析后 `product_code == "CASH"`（现金只能经申赎 / 基金调仓配对腿 / 跨平台转移生成）；PUT 修改 CASH 腿且改动字段集合超出 `{notes}`；#493 新增：**调仓组（`transfer_group` 以 `rebal_` 开头）的 CASH 腿**被直接 confirm / unconfirm / cancel / delete（只能由基金腿驱动；该限制以调仓组为边界，不动申赎与独立现金转移的生命周期）。注意 PUT 的 CASH 腿守卫先于状态门，故已 confirmed 的调仓现金腿改财务字段仍报本码 | services/trade_service.py::confirm_single_trade; ::create_trade; ::update_trade; ::cancel_trade; ::unconfirm_trade; ::delete_trade |
| `CASH_CONFIRM_DATE_NOT_ALLOWED` | 422 | 现金腿现金日的方向闸门（#493）：**卖出创建**时传 `cash_confirm_date`（到账日在确认时录入）；或**买入**（创建或确认）传入不等于下单日 T 的 `cash_confirm_date`（扣款日固定 T） | services/trade_service.py::resolve_cash_leg_plan; ::create_trade |
| `CASH_LEG_MISSING` | 422 | 已确认卖出 PUT `cash_confirm_date` 时，组内不存在配对 CASH buy 腿（存量异常数据，无法同步到账日）；提示先 unconfirm 再重新确认以重建到账腿 | services/trade_service.py::_update_confirmed_sell_arrival_date |
| `CASH_PLATFORM_NOT_ALLOWED` | 422 | 现金腿平台的方向闸门（#493）：**卖出创建**时传 `cash_platform_code`（到账平台在确认时录入）；或**买入确认**时传与既有扣款腿平台不同的 `cash_platform_code`（扣款平台创建时确定） | services/trade_service.py::resolve_cash_leg_plan; ::create_trade |
| `CASH_TRANSFER_NON_CASH_LEG` | —（非 HTTP 码：auto\_confirm 的 `auto_confirm_failed` 条目） | auto\_confirm 跨天转移分支在置 confirmed 前发现组内存在**非 CASH** 腿（`_confirm_pair` 的显式业务校验，替代可被 `-O` 关闭的 assert）：该分支服务跨平台现金转移，调仓腿须人工确认，不得被空确认（不取价、不建腿） | services/snapshot_service.py::auto_confirm_after_snapshot._confirm_pair |
| `CONFIRM_BEFORE_STARTED` | 422 | 申购确认预览与确认路径（同一实现）中，`sub_type == "subscribe"` 且组合 `started_at` 非空、T+1 确认日 **<** `started_at`（乱序补录闸门；等于放行以支持同日多平台，`started_at` 为空豁免，赎回不校验） | services/subscription_service.py::calculate_subscription_confirm_preview |
| `CONFIRM_REQUIRED` | 422 | 快照批量删除未显式传 `confirm=true`（破坏性操作守卫，因逐日 commit 不可中途回滚）；`dry_run=true` 在此之前直接返回预览、不触发本码 | routers/snapshots.py::delete_snapshots_bulk |
| `DATA_SOURCE_NOT_CONFIGURED` | 503 | `POST /api/trading-calendar/sync` 捕获 `TushareNotConfiguredError`——`TUSHARE_TOKEN` 未配置（`services/tushare_client.py`） | routers/trading_calendar.py::sync_trading_calendar |
| `DATE_BEFORE_SNAPSHOT` | 422 | 存在最新快照日时，业务日期 `<=` 最新快照日（要求严格晚于）：调仓 `trade_date`（创建与 PUT 改日期共用 `validate_trade_date`）、现金转移 `transfer_date`、申赎按**确认日**口径（`confirm_date <=` 最新快照日即拒，创建与 PUT；`apply_date` == 最新快照日当天放行，#495）、事件 `ex_date`（创建与 PUT 共用 `_validate_event_dates`） | services/trade_service.py::validate_trade_date; services/subscription_service.py::create_subscription |
| `DELETE_FAILED` | 500 | `DELETE /api/snapshots/{portfolio}/{date}` 删除中抛出**非** `BusinessError` 的异常；`BusinessError`（如级联回退失败整体中止）rollback 后原样透传，不降级为本码 | routers/snapshots.py::delete_snapshot |
| `DIMENSION_RULE_CONFLICT` | 422 | 维度值 PUT 全量替换 `dimension_rules` 的收紧保护：某维度改为 `required`（原非 required）而该 asset_class 下存量产品该维度为空；或删除规则行（→ `forbidden`）而存量产品该维度非空 | services/asset_classification_service.py::_apply_rule_changes |
| `DIMENSION_VALUE_IN_USE` | 422 | 维度值 PUT 缩减 `applicable_asset_classes`（全量替换语义）时，被移除的 asset_class 下仍有产品引用该维度值（`details` 带产品清单） | services/asset_classification_service.py::update_classification |
| `DUPLICATE_TRADE` | 422 | 自然键防重：同组合/产品/市场/平台/方向/交易日下已有 `pending`/`confirmed` 交易，买入比 `actual_amount`、卖出比量化后 `shares` 相等即命中。创建路径可传 `allow_duplicate` 放行；PUT 因改日期 / 买入金额 / 卖出份额而与他人（排除自身 id）撞车时无放行口 | services/trade_service.py::create_trade; ::update_trade |
| `EMPTY_ADJUSTMENT` | 422 | `event_type == "forced_adjustment"` 且 `shares_change` 与 `cash_change` 均为 None（否则确认后零效果且无告警）；创建、PUT（按合并后生效值）、confirm（兜底防存量脏数据）三处共用同一校验 | services/share_change_event_service.py::_validate_adjustment_not_empty |
| `FORBIDDEN` | 403 | 权限门（非资源不存在）：`get_current_admin` 要求 `current_user.role == "admin"`，非 admin 访问 admin-only 端点即拒；改密端点非 admin 且 `target_code` 指向他人 | dependencies.py::get_current_admin; routers/auth.py::change_password |
| `INSUFFICIENT_CASH` | 422 | 支出超过扣款平台实时可用现金（先量化 2 位再精确比较、无容差）：调仓买入按 `as_of` 校验（创建/PUT/确认共用，加回自身 CASH sell 腿——#493 起含 pending/confirmed，且只加回查询时点已计提的扣款，见「可用量口径」节；确认侧 `skip_available_check` 跳过）；赎回确认按确认日校验该平台可用现金（`skip_cash_check` 跳过）；现金转移按转出平台校验（#493 起按 `transfer_date` 时点） | services/trade_service.py::validate_buy_cash_with_addback; services/subscription_service.py::confirm_single_subscription |
| `INSUFFICIENT_SHARES` | 422 | 卖出/赎回份额超过实时可用份额：调仓卖出（创建/PUT/确认共用 `validate_sell_shares_with_addback`，自身 pending 卖出份额加回防双重计数）；赎回创建按申请日投资人可用份额，赎回 PUT 按新份额（本条 pending 旧份额加回） | services/trade_service.py::validate_sell_shares_with_addback; services/subscription_service.py::create_subscription |
| `INVALID_AMOUNT` | 422 | 金额入参为 None 或量化到 2 位后 `<= 0`：调仓买入含费现金支出（创建/PUT/确认共用）、现金转移金额（原值与量化后两道）、申购金额（创建原值 + 量化后、PUT 量化后）；另卖出调仓有价格时 `quantize(shares×price) − fee <= 0`（fee 不小于毛额） | services/trade_service.py::validate_buy_cash_with_addback; services/cash_transfer_service.py::create_cash_transfer |
| `INVALID_CLASSIFICATION` | 422 | 资产分类维度字典新建/编辑形态非法：`dimension` 不在五维白名单；`code` 非全大写或不带该维度前缀（ASSET_/REGION_/STYLE_/SIZE_/SEG_）；asset_class 值却传 `applicable_asset_classes`、非 asset_class 值传 `dimension_rules` 或适用大类为空（新建/更新均须 ≥1）；`dimension_rules` 的维度或规则值越界；关联的适用大类不存在/不是 asset_class 维度值，或其规则矩阵无该维度行（无行 = 禁止） | services/asset_classification_service.py::create_classification; ::_validate_applicable_classes |
| `INVALID_CONFIRM_DAYS` | 422 | 产品 `confirm_days` 非法（`validate_confirm_days`）：显式传 null、< 0，或场内（`CN_EXCHANGE`）不为 0。create 仅在显式传入时校验（未传按 market+is_qdii 推导）；update 对合并后终态**无条件**校验——即使本次没改该字段，存量脏值（NULL/负数）也会在任何 PUT 上被拦 | services/product_service.py::validate_confirm_days |
| `INVALID_CREDENTIALS` | 401 | 登录：`code` 查无该投资人，或 `verify_password` 对 password_hash 校验不通过（两种情形同一分支、不区分用户是否存在），且账户进入时未被锁定、本次失败也未新触发锁定（锁定态一律 403 `ACCOUNT_LOCKED`） | routers/auth.py::login |
| `INVALID_DATE_ORDER` | 422 | 份额变动事件 `ex_date <= entitlement_date`（除息日必须严格晚于权益登记日）；创建与 PUT 改日期（按合并后生效值重跑）共用 `_validate_event_dates` | services/share_change_event_service.py::_validate_event_dates |
| `INVALID_DATE_RANGE` | 422 | 区间查询同一组日期参数 start > end：调仓列表 trade_date 组与 confirm_date 组、申赎列表 apply_date 组与 confirm_date 组、快照历史 start/end（两参可选，均传才比）、净值覆盖 `get_nav_coverage` start/end（两参必填、无 None 短路）、事件列表 ex_date_start/ex_date_end（唯一在 router 内直接抛 `BusinessError` 的站点） | services/trade_service.py::list_trades; routers/share_change_events.py::get_share_change_events |
| `INVALID_DIMENSION_TAGS` | 422 | 产品五维标签校验（`validate_dimension_tags`）四层任一不过：维度值不存在或 `dimension` 与字段不匹配；值 `is_active=False`（create 全查，update 只查实际变化字段）；`asset_class_code` 为空却填了其余维度；大类规则矩阵 required 维度缺失、或无规则行（= forbidden）的维度有值；所选值未在 `asset_dimension_applicability` 关联该 asset_class | services/product_service.py::validate_dimension_tags |
| `INVALID_DISPLAY_CONFIG` | 422 | 组合 `display_config`（持仓明细二级分组覆盖）非法：不是 dict；key 不是字典中 `dimension=asset_class` 的维度值（不校验 is_active）；value 未在该大类的 `asset_class_dimension_rule` 登记（无规则行的大类如 ASSET_CASH 任何配置均拒）。create 恒校验；update 仅在非 UNSET 哨兵时校验，null/{} 归一为清空、不触发校验 | services/portfolio_service.py::validate_display_config |
| `INVALID_ENTITLEMENT_DATE` | 422 | 份额变动事件 `entitlement_date`（权益登记日）不是交易日（`trading_calendar.is_open`）；创建与 PUT 改日期共用，是双日期校验链的第一道（早于除息日交易日、日期先后、晚于最新快照日） | services/share_change_event_service.py::_validate_event_dates |
| `INVALID_EX_DATE` | 422 | 份额变动事件 `ex_date`（除息日）不是交易日；创建与 PUT 改日期共用，紧随权益登记日交易日校验之后 | services/share_change_event_service.py::_validate_event_dates |
| `INVALID_MARKET` | 422 | PUT 产品时 `market` **值实际变化**且新值不在枚举（CN_EXCHANGE/CN_OTC/HK_MUTUAL）；守卫在系统虚拟产品保护（`SYSTEM_PRODUCT_PROTECTED`）之后。create 路径不校验 market 枚举（虚拟产品 `market=""` 由此可落库） | services/product_service.py::_validate_identity_change |
| `INVALID_NAV_LAG_DAYS` | 422 | 产品 `nav_lag_days` 非法（`validate_nav_lag_days`）：为 null（NOT NULL 列的「清除」语义一并拒）、< 0，或场内（`CN_EXCHANGE`）不为 0。create 恒校验（默认 0）；update 按 market + nav_lag_days 合并终态**无条件**校验，故 CN_OTC→CN_EXCHANGE 迁移残留 lag>0、或存量脏值在任何 PUT 上都会被拦 | services/product_service.py::validate_nav_lag_days |
| `INVALID_OLD_PASSWORD` | 400 | 改密时 `verify_password(old_password, hash)` 不通过；仅在「非 admin，或 admin 改自己密码」这条必须验旧密码的分支触发（admin 改他人密码不校验旧密码）。未传 old_password 是 400 `OLD_PASSWORD_REQUIRED`，非本码 | routers/auth.py::change_password |
| `INVALID_PARAM` | 422 | 申赎 PUT 参数非法（`update_subscription`，状态门之后）：除 `notes`（null = 清除备注）外任一字段显式传 null——防绕过量化与可用份额闸门落库脏数据；或字段与 `sub_type` 错位——申购传 `shares`、赎回传 `amount`。**#493 调仓侧同码**：confirmed 卖出 PUT `cash_confirm_date: null`（不传即保持原值）；**pending** 交易（买或卖）传 `cash_confirm_date`（买入扣款日固定 T、卖出到账日在确认时录入） | services/subscription_service.py::update_subscription; services/trade_service.py::_update_confirmed_sell_arrival_date; ::update_trade |
| `INVALID_PRODUCT_TYPE` | 422 | `product_type` 不在枚举（ETF/OEF/LOF/CASH/IN_TRANSIT）（`validate_product_type`）：create 恒校验；update 仅在 product_type **实际变化**时校验（前端编辑恒带该字段，显式传原值不进门禁） | services/product_service.py::validate_product_type |
| `INVALID_SHARES` | 422 | 份额输入为空或量化到 2 位后 <= 0：赎回创建（`shares` 为 None/≤0，以及量化后再判 ≤0）、赎回 PUT 改 `shares`；调仓卖出侧 `validate_sell_shares_with_addback`（`new_shares` 为 None，或量化后 ≤0），由卖出 create / update（shares 或 trade_date 变动时）/ confirm 三条路径共用 | services/trade_service.py::validate_sell_shares_with_addback; services/subscription_service.py::create_subscription |
| `INVALID_STATUS` | 422 | 三态生命周期状态门（[生命周期](#rule-lifecycle)）的通用拒绝码：confirm 与确认预览要求 `status == "pending"`（申赎/调仓/事件；调仓 preview 与 confirm 两处由 router 抛 `HTTPException` 422，事件 preview 由 service 抛同码 `BusinessError`，#424）；cancel 要求 pending；unconfirm 要求 confirmed；PUT 拒绝 cancelled。申赎侧经子类 `InvalidStatusError`；事件 PUT 只拒 confirmed，cancelled 事件不拦（与调仓/申赎不对称） | services/subscription_service.py::InvalidStatusError; services/trade_service.py::update_trade |
| `INVALID_TYPE` | 422 | 创建时枚举兜底：申赎 `sub_type` 既非 `subscribe` 也非 `redeem`；调仓 `trade_type` 既非 `buy` 也非 `sell`（schema 为裸 `str`，REST/CLI 都能传非法值，故在 if/elif 链的 else 分支拦截） | services/subscription_service.py::create_subscription; services/trade_service.py::resolve_cash_leg_plan |
| `INVESTOR_HAS_SHARES` | 422 | 删除投资人时，其**最新快照日**的 `investor_holding` 行 `shares > 0`（份额未清零不允许物理删除；只看最新一行，历史行有份额不阻断） | services/investor_service.py::delete_investor |
| `MANUAL_OVERRIDE_NOT_FOUND` | 404 | 删除现金手动重估记录时，按 (组合, 平台, `product_code="CASH"`, `value_date`) 查不到 `manual_market_value` 行 | services/position_service.py::delete_manual_cash_override |
| `MARKET_AMBIGUOUS` | 422 | `resolve_product_market`：只给 `product_code`、省略或传空 `market`，而该 code 在 `product` 表存在多个 market（LOF 一码多市场）时拒绝自动补全，`details` 带 `available_markets`。调仓创建、份额事件创建、不带 market 的产品详情共用 | services/product_service.py::resolve_product_market |
| `MARKET_CHANGE_REFERENCED` | 422 | 更新产品且 `market` **实际变化**（新值非 None 且 != 原值）时，旧 `(code, market)` 已被任一 `trade` / `share_change_event` / `portfolio_position` 引用（三类计数任一 > 0）；系统虚拟产品先被 `SYSTEM_PRODUCT_PROTECTED` 拦截 | services/product_service.py::_validate_identity_change |
| `MISSING_NAV` | 422 | 净值严格匹配、禁止向前回退（[快照规则](#rule-snapshot)）：① 快照侧按产品 `nav_lag_days` 定取价日（0 = 当日、N = 前第 N 个交易日），任一持仓缺该日 `price_record` 即拒绝生成（`db.add_all` 前抛，整体回滚）；预校验中**纯** price_data 失败同报此码，与其他检查项混合失败则降级 ValueError → `VALIDATION_FAILED`。② 调仓确认侧场外净值型基金（OEF/LOF × CN_OTC/HK_MUTUAL）缺 T 日（`trade_date`）净值即拒绝确认，`sync_nav=true` 时自动回填后重试一次、回填异常或仍缺也报此码。catch-up/recalculate 逐日循环捕获后写进 `results[].errors`（响应仍 200） | services/snapshot_service.py::_generate_portfolio_position; services/trade_service.py::confirm_single_trade |
| `MISSING_OR_INVALID_PRICE` | 422 | 创建调仓时价格闸门：`product.market == "CN_EXCHANGE"`（场内）且未传 `price`；或任意市场显式传入的 `price <= 0` | services/trade_service.py::create_trade |
| `MISSING_POSITION_SNAPSHOT` | 422 | 份额变动事件缺基数快照（确认与**确认预览**同码，两侧口径一致）：① `entitlement_date` 该组合**无任何** `portfolio_position` 行（两路径同款前置，预览在 `compute_share_change_event_preview` 内）；② 基金级事件（`platform_code is None`）自动拆分时该产品在 `entitlement_date` 的 `event.market` 下无 `shares > 0` 的持仓行（确认侧 `_confirm_fund_level_event` 的 `ValueError` 被包装为此码，预览侧在计算前显式拒绝——否则会返回 0/0.00 的「假预览」，看着能确认、点下去才炸；#461：口径以 `event.market` 为边界，另一市场有持仓不算命中） | services/share_change_event_service.py::_require_entitlement_snapshot; ::compute_share_change_event_preview; ::confirm_share_change_event |
| `NAV_NOT_AVAILABLE` | 422 | 申赎确认/预览的净值决策落到兜底：申请日无 `portfolio_value_snapshot`，**且**已存在 `confirm_date <= apply_date` 的 confirmed 申购（即资金已到账、不在 1.0000 首窗内，[申赎规则](#rule-subscription)）。首窗内不抛、按 1.0000 计价 | services/subscription_service.py::NavNotAvailableError |
| `NEGATIVE_CASH` | 422 | 快照持仓生成时，任一平台 CASH 行（`cash_amount IS NOT NULL`）计算出的 `cash_amount < 0` 即硬阻断（#203 由告警升级），抛出点在 `db.add_all` 之前、调用方整体回滚；存量脏数据另经 status 端点 `negative_cash_platforms` 暴露 | services/snapshot_service.py::_generate_portfolio_position |
| `NON_TRADING_DAY` | 422 | 目标日期不在 `trading_calendar.is_open`：调仓创建/PUT 的 `trade_date`（`validate_trade_date` 共用）、申赎创建/PUT 的 `apply_date`、跨平台现金转移的 `transfer_date`、现金手动重估的 `update_date`（缺省取 `date.today()`）。快照生成侧同型检查抛 ValueError 而非此码 | services/trade_service.py::validate_trade_date; services/position_service.py::update_cash_position |
| `NOT_FOUND` | 404 | 通用「资源不存在」码，全部站点均走 `NotFoundError`（无显式 `http_status` 覆盖，恒 404）：组合（`_get_portfolio_or_404` 及创建交易/事件/申赎时的组合校验）、投资人、产品 `(code, market)` 组合（`details` 带 `available_markets`）、维度值、组合最新一条市值快照（`get_latest_value_snapshot`）。与专用码分工：快照 catch-up/generate-next 与现金重估路径用 `PORTFOLIO_NOT_FOUND`，市场解析用 `PRODUCT_NOT_FOUND` | services/portfolio_service.py::_get_portfolio_or_404; services/investor_service.py::update_investor |
| `NO_SNAPSHOT_BASELINE` | 422 | 组合尚无任何快照（`get_latest_snapshot_date` 为 None）时调 `catch_up_snapshots` 或 `generate_next_snapshot`——两者都以最新快照日为增量基线，无基线须改用 recalculate 从最早交易日重建 | services/snapshot_service.py::catch_up_snapshots; ::generate_next_snapshot |
| `OLD_PASSWORD_REQUIRED` | 400 | `PUT /api/auth/password`：需验旧密码的分支（`current_user.role != "admin"` **或** `target_code == current_user.code`，即改自己的密码）下请求体未提供 `old_password`。router 直接抛 `HTTPException`，不经 `BusinessError` | routers/auth.py::change_password |
| `PENDING_TRANSACTIONS_EXIST` | 422 | ① 关闭组合时该组合存在 pending 申赎或 pending 调仓（两类计数任一 > 0）；② 修改产品 `product_type` 且值**实际变化**时，该产品存在 pending `trade` 或 pending `share_change_event`（`details` 带两类计数） | services/portfolio_service.py::close_portfolio; services/product_service.py::_validate_identity_change |
| `PLATFORM_NOT_ALLOWED` | 422 | 创建份额变动事件时 `event_type` 属基金级（`share_split` / `share_merge` / `bonus_share`）却指定了 `platform_code`（空串已在入口归一为 None，故只有真值触发） | services/share_change_event_service.py::create_share_change_event |
| `PLATFORM_NOT_COVERED` | 422 | 创建平台级事件（`cash_dividend` / `reinvest_dividend` / `forced_adjustment`）时，`entitlement_date` 该产品**同 `market`** 有 `shares > 0` 持仓的平台，未被「同 `(产品, market)`、同 `ex_date` 的非 cancelled 平台级事件 ∪ 本次 `platform_code`」全覆盖（#461：持仓侧与已录事件侧都按 market 收窄，LOF 另一市场的持仓/事件不参与）；传 `force_cover=true` 降级为 warning | services/share_change_event_service.py::create_share_change_event |
| `PLATFORM_NOT_FOUND` | 404 | 传入平台 code 在 `platform` 表查无记录（全部站点均 `NotFoundError`，恒 404）：调仓的 `platform_code` 与 `cash_platform_code`、申赎创建与 PUT 的 `platform_code`、平台级事件的 `platform_code`、现金转移的转出/转入平台、现金手动重估的 `platform_code` | services/trade_service.py::resolve_cash_leg_plan; services/subscription_service.py::create_subscription |
| `PLATFORM_REQUIRED` | 422 | 创建份额变动事件时 `event_type` 属平台级（cash_dividend / reinvest_dividend / forced_adjustment）而 `platform_code` 为空（空串先归一为 None，#343）；创建调仓交易未传 `platform_code`——基金腿平台决定持仓归属，缺省会让买入现金闸门退化为全组合聚合 | services/share_change_event_service.py::create_share_change_event; services/trade_service.py::create_trade |
| `PORTFOLIO_ALREADY_CLOSED` | 422 | `close_portfolio`：目标组合 status 已为 closed 时重复关闭被拒（组合不存在走 `_get_portfolio_or_404` 的 404） | services/portfolio_service.py::close_portfolio |
| `PORTFOLIO_NOT_ACTIVE` | 422 | 组合状态非 active 的写操作闸门：创建调仓、编辑调仓、创建跨平台现金转移均要求 `status == "active"`；创建申赎口径较宽——status 不在 (active, draft) 才拒，draft 放行以支持首购确认时激活（[组合管理](#rule-portfolio)） | services/trade_service.py::create_trade; services/subscription_service.py::create_subscription |
| `PORTFOLIO_NOT_CLOSED` | 422 | `reactivate_portfolio`：组合 `status != "closed"`（draft / active）时不可重开 | services/portfolio_service.py::reactivate_portfolio |
| `PORTFOLIO_NOT_FOUND` | 404 | 按 code 查不到 portfolio 行：快照 status / list / bulk-delete 端点与 recalculate-async 的组合前置校验（router 侧 `HTTPException` 404）；catch-up、generate-next、现金转移创建、现金手动重估的写入/列表/删除（service 侧 `NotFoundError`）。注意单日 generate 的组合不存在不落本码，走 ValueError → `VALIDATION_FAILED` | routers/snapshots.py::recalculate_async; services/snapshot_service.py::catch_up_snapshots |
| `POSITION_NOT_FOUND` | 422 | 份额变动事件指向不存在的持仓行：① 确认平台级 `forced_adjustment` 时，`entitlement_date` 当日快照无 (product_code, market, platform_code) 持仓行（提前快失败，否则要到快照生成才炸）；② 快照生成应用窗口内 confirmed 事件时，该三元组键在持仓字典中不存在（LOF market 误填为典型），或键存在但命中的是现金行（`cash_amount IS NOT NULL`） | services/snapshot_service.py::_generate_portfolio_position; services/share_change_event_service.py::resolve_entitlement_shares |
| `POSITION_TABLE_PROTECTED` | 422 | POST / PUT / DELETE `/api/positions` 三端点无条件拒绝（handler 首行即抛，不读请求体、不查记录）：`portfolio_position` 是系统生成的快照表，现金修正须走 cash-position（`manual_market_value` 覆盖层），删快照走 `DELETE /snapshots/{portfolio_code}/{snapshot_date}` | routers/positions.py::create_position; ::update_position |
| `PRICE_NAV_MISMATCH` | 422 | 确认或预览确认场外净值型基金（product_type ∈ OEF/LOF 且 market ∈ CN_OTC/HK_MUTUAL）时显式传入 price，经 `quantize_nav` 归一到 4 位（ROUND\_HALF\_UP，#428）后与 T 日（`trade_date`）净值不等——手动价只作一致性校验，不参与计算也不覆盖净值，不传价则直接取净值 | services/trade_service.py::calculate_confirm_preview |
| `PRODUCTS_PARAM_CONFLICT` | 422 | 列表过滤参数互斥（两处均显式 `http_status=422`）：调仓列表 `products`（逗号分隔 `code\|market` 多选）解析出非空项时又传了 product_code 或 market（判据为 `market is not None`）；份额变动事件列表 products 非空时又传了 product_code | services/trade_service.py::list_trades; routers/share_change_events.py::get_share_change_events |
| `PRODUCT_NOT_FOUND` | 404 | `resolve_product_market`：调用方省略 market，而按 product_code 查不到任何 Product 行（markets 集合为空）。省略 market 但命中多个市场走 `MARKET_AMBIGUOUS`；显式给了 `(code, market)` 却不存在时各入口抛 `NOT_FOUND`，不落本码 | services/product_service.py::resolve_product_market |
| `PRODUCT_REQUIRED` | 422 | 创建份额变动事件时 `product_code` 为空（falsy）——事件必须挂具体产品；基金级事件靠 `platform_code` 为空区分，不能省略产品 | services/share_change_event_service.py::create_share_change_event |
| `RECALCULATION_FAILED` | 500 | `POST /snapshots/recalculate` 的兜底 `except Exception`：ValueError 已被前一分支翻成 422 `VALIDATION_FAILED`，其余任何异常（逃出逐日 try 的 `BusinessError`、router 内 `db.commit()` 失败、DB/连接错误等）rollback 后统一 500。**逐日失败不走这里**——它们进 `results[].errors` 并以 200 返回；该端点无 `except BusinessError` 分支（generate/generate-next 有，领域异常原样上抛） | routers/snapshots.py::recalculate |
| `RECALC_JOB_CONFLICT` | 409 | `POST /snapshots/recalculate-async`：`submit_snapshot_recalc_job` 发现 sync_job 表已有 `job_type=snapshot_recalc` 且 `status ∈ (pending, running)` 的记录（同类型单 active 锁）抛 `ConflictError`，router 翻成 409 | routers/snapshots.py::recalculate_async |
| `SAME_PLATFORM` | 422 | `create_cash_transfer`：`from_platform == to_platform`，跨平台现金转移两端必须不同 | services/cash_transfer_service.py::create_cash_transfer |
| `SESSION_ABORTED` | —（非 HTTP 码：200 + `results[].errors` / `auto_confirmed` 条目） | auto_confirm 单条守护判定 DB session 不可恢复时写入的根因 code（#305/#419）：进守护前 `db.is_active` 为假；或守护内 savepoint 回滚自身失败、捕获 `PendingRollbackError`、`DBAPIError.connection_invalidated`、失败后 `is_active` 仍为假。重算逐日循环见到它即 break 并追加进 `results[].errors`（触发 router 整体 rollback），catch-up / 调度路径经 `auto_confirmed` 条目透传 | services/snapshot_service.py::_auto_confirm_guarded; ::recalculate_snapshots |
| `SHARES_CHANGE_ON_CASH_PRODUCT` | 422 | 事件标的为现金型/在途产品（按 product_code(+market) 查得 `product_type ∈ {CASH, IN_TRANSIT}`），且 `event_type` 属结构性份额类型（share_split / share_merge / bonus_share / reinvest_dividend，确认时必产生份额变动）或显式填了 `shares_change`；创建、PUT（按合并后值）、确认三处均校验。纯现金调整（`shares_change` 为空，如 #279 对 CASH 的现金修正）不触发 | services/share_change_event_service.py::_validate_product_allows_shares_change |
| `SNAPSHOT_DEPENDENCY` | 422 | 快照保护，须先删快照走级联回退（`details.from_date` = 需删除的起始日）。① **申赎/事件**：unconfirm 时确认日（申赎取 `confirm_date`、事件取 `ex_date`）及之后已有快照即拒；申赎侧 `check_snapshot=False`（快照删除级联自身调用）跳过。② **调仓（#493 组级）**：update/cancel/delete/unconfirm 时**组内任一腿**会计生效日（`confirm_date`，缺省 `trade_date`）及之后已有快照即拒，改日期时新值一并纳入保护。③ **调仓 confirm（#493）**：有效基金确认日 C 不晚于最新快照日即拒；买入需校正既有扣款腿时扣款日已被快照消费即拒。 | services/subscription_service.py::unconfirm_single_subscription; services/trade_service.py::validate_group_snapshot_free; ::_require_date_not_snapshot_consumed; ::resolve_cash_leg_plan |
| `SNAPSHOT_GENERATION_FAILED` | 500 | `POST /snapshots/generate` 与 `/generate-next` 的兜底 `except Exception`：`BusinessError` 已原样上抛交全局 handler、ValueError 已翻成 422 `VALIDATION_FAILED`，剩下的非预期异常（DB/连接错误、commit 失败等）rollback 后统一 500 | routers/snapshots.py::generate_snapshot; ::generate_next |
| `SNAPSHOT_NOT_CONTINUOUS` | 422 | 单日生成的连续性校验（`check_continuity=True`；重算逐日重建 bypass）：组合已有快照时，`target_date` 既不等于最新快照日（重建最新一日）也不等于最新快照日的下一交易日——早于最新日属单独重建中间快照、晚于属跳过交易日，两者都拒；零快照首次生成不受限 | services/snapshot_service.py::_validate_snapshot_continuity |
| `SNAPSHOT_REQUIRES_RECALCULATE` | 422 | 单日 generate 的「零快照失忆」守卫（#180）：组合无任何 `portfolio_value_snapshot`，且存在 `confirm_date < target_date` 的 confirmed 申赎或交易（`confirm_date` 为 NULL 的异常数据回退按 apply_date / trade_date 比较）——增量窗口无前序基线会退化为仅目标日，早期到账被静默漏掉，须改用 recalculate 从最早 `confirm_date` 逐日重建；目标日即最早到账日（`confirm_date == target_date`）不受影响 | services/snapshot_service.py::_validate_no_silent_history_gap |
| `SYNC_FAILED` | 500 | `POST /trading-calendar/sync`：Tushare 接口报错（`TushareAPIError`）或任何其他异常导致同步失败，两分支均 500（Tushare 未配置走 503 `DATA_SOURCE_NOT_CONFIGURED`，不落本码） | routers/trading_calendar.py::sync_trading_calendar |
| `TASK_NOT_FOUND` | 404 | `run_task` 的派发表守卫（#406）：`task_code` 不在 `_TASK_DISPATCH` 覆盖的四个任务码（`nav_sync` / `snapshot_generate` / `trading_calendar_sync` / `log_cleanup`）内，`details.available_tasks` 回传可选集；**不建执行记录**——没执行过的任务不该有执行历史。经 `POST /system/tasks/{code}/run` 时通常由 router 的前置校验（查 `scheduled_task`）先命中，本码覆盖「任务表有行但派发表未覆盖」的漂移 | services/task_runner.py::run_task |
| `SYSTEM_PRODUCT_PROTECTED` | 422 | `update_product` 的身份字段守卫：产品 code ∈ (CASH, IN_TRANSIT_BUY, IN_TRANSIT_SELL)，且本次更新**实际改变** `product_type` 或 `market` 的值（显式传原值不进门禁）；这两个字段以外的编辑不受本码限制 | services/product_service.py::_validate_identity_change |
| `TRANSFER_NOT_FOUND` | 404 | `confirm_cash_transfer`：按 (portfolio_code, transfer_group) 查不到 `product_code=CASH` 且 `status=pending` 的腿——组不存在或组内两腿已全部 confirmed（`NotFoundError` → 404） | services/cash_transfer_service.py::confirm_cash_transfer |
| `TRANSFER_NOT_READY` | 422 | `confirm_cash_transfer`：存在 pending CASH 腿，但其生效确认日（首条腿的 `confirm_date`，为空则取该腿 `trade_date` 的下一交易日）晚于今天——跨天转移尚未到账日，不允许提前确认（[现金转移的非对称状态](#rule-trade)） | services/cash_transfer_service.py::confirm_cash_transfer |
| `VALIDATION_FAILED` | 422（router 包装 ValueError）；逐日条目形态为 200 | 两形态同名：① generate / recalculate / generate-next 端点把下游 ValueError 翻成 422——组合不存在或非 active（`_validate_portfolio`）、目标日非交易日、依赖校验有 failed 项（纯 price_data 缺失走 `MISSING_NAV`、不落本码）、重算整区间静态预校验失败；② recalculate 逐日循环里 `validate_snapshot_dependencies` 出现 failed 项时写入 `results[].errors` 的 code（响应 200，随后 break、整体 rollback） | routers/snapshots.py::generate_snapshot; services/snapshot_service.py::recalculate_snapshots |

<a id="rule-precision"></a>
## 量化产生点清单

份额与金额统一 2 位小数、净值/市值/成本价 4 位小数、**三者均 ROUND\_HALF\_UP**、负数按绝对值对称（远离零进位，符合场外基金惯例），量化误差计入基金财产。**量化只发生在下列产生点**，读取/累加路径不量化；可用量闸门先量化再精确比较、无容差。每个精度各有唯一 helper，调用点不得写 `Decimal("0.01"/"0.0001")` 字面量——`quantize` 不传 `rounding=` 会走 Decimal 缺省的 HALF\_EVEN（#428 的教训：4 位口径曾静默漂移 7 处）。实现见 [quantize.py](../../backend/app/utils/quantize.py)，代表测试见 [test_quantize.py](../../backend/tests/unit/test_quantize.py)。

* **份额产生点**（`quantize_shares`）：申购确认 `amount/nav`、调仓买入 `amount/price`、卖出与赎回的用户输入、份额事件的变动计算。

* **金额产生点**（`quantize_amount`）：卖出与赎回确认 `shares×nav`、买入金额与手续费的用户输入、申赎金额、现金分红 `cash_change`、`forced_adjustment` 用户填写、`manual_market_value` 写入、现金转移金额、trade PUT 直改。

* **净值/估值产生点**（`quantize_nav`，#428）：`_generate_portfolio_value_snapshot` 构造快照行的 `total_value` / `unit_price` / `unit_price_change_pct` / `in_transit_total`；`_generate_investor_holding` 的 `cost_per_share`；以及**确认对账比较**——场外传入价与 T 日净值两侧归一到 4 位后精确比较（`PRICE_NAV_MISMATCH`，无容差）。

* 估值口径（`market_value` / `total_value` / `unit_price` / `cost_per_share`）保持 4 位、不进现金账本；DB 字段精度收紧留作后续迁移。

<a id="rule-trading-day"></a>
## 交易日

所有交易操作（申购、赎回、调仓、现金进出、事件日期）仅允许在交易日进行，以 `trading_calendar.is_open` 为准，不用自然日或周一至周五替代。实现入口为 [trading_utils.py](../../backend/app/services/trading_utils.py) 的 `get_next_trading_day` / `get_prev_trading_day`，代表测试见 [test_trading_day.py](../../backend/tests/integration/test_trading_day.py)。

## 易错陷阱

1. 现金市值修正走 `POST /positions/portfolio/{code}/cash-position` 写 `manual_market_value`（绝对替换），**不直接改 `portfolio_position`**；写入后需重新生成快照。
2. LOF 拆分为两条记录（场内/场外分别处理）。
3. 组合份额仅因申购赎回变化；分红再投资只影响成分基金份额。
4. 投资人不支持强制物理删除——份额需为 0 才能删。
5. 幂等性缓存（`idempotency_cache`）24 小时过期，批量调仓用 `Idempotency-Key`。
6. 分类信息只从 positions API 读侧派生（#128）：快照表无分类列，不要在写侧/快照链路重新引入 asset\_type 冗余；判断现金行用 `cash_amount IS NOT NULL`，不用产品类型字符串。产品维度标签改动走 product create/update（service 层矩阵校验），不直改 DB。
7. 传递依赖不写进 `backend/requirements.txt` 就等于没钉（#314，fastapi 的 starlette 曾长期浮动）；且 fastapi ≥0.141 改了 `app.routes` 结构，会让遍历路由的鉴权门禁（#256）静默空通过（#306）——升 fastapi/starlette 必须连带复核这道门禁是否还在真扫描。
