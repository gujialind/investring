# InvestRing CLI 使用说明书

## 1. 概述

`ir` 是 InvestRing 的轻量 HTTP 客户端命令行工具，通过 REST API 调用运行中的后端服务，所有输出为结构化 JSON，适合 AI agent 和脚本自动化调用。

**特点**：仅需 `typer` + `httpx` 两个依赖，可在任意设备上安装使用，不依赖后端代码库。

## 2. 安装与环境

### 2.1 安装

**方式一：一键脚本（推荐）**

```bash
# 从 GitHub 安装（默认）
./ir-cli/install.sh

# 从 Gitee 安装（国内设备）
./ir-cli/install.sh --repo gitee

# 指定分支 + 顺便配置服务端地址
./ir-cli/install.sh --ref dev --base-url https://ir.example.com

# curl 一键安装
curl -LsSf https://raw.githubusercontent.com/gujialind/investring/main/ir-cli/install.sh | bash -s -- --repo gitee --base-url https://ir.example.com
```

**方式二：pip / uv 手动安装**

```bash
# uv（推荐）
uv tool install "git+ssh://git@github.com/gujialind/investring.git#subdirectory=ir-cli"

# pip
pip install "git+ssh://git@github.com/gujialind/investring.git#subdirectory=ir-cli"
```

安装后 `ir` 命令注册到 PATH 中。升级 = 重跑安装脚本（`--force --reinstall`）。

### 2.2 运行前提

- 后端服务已启动且可访问（默认地址 `http://localhost:8000`）
- 已通过 `ir auth login` 登录获取 token（token 存储在 `~/.ir/token.json`，权限 0600）
- 服务端地址可通过以下方式配置（优先级从高到低）：
  - 环境变量 `IR_BASE_URL`
  - `~/.ir/config` 文件中的 `base_url=` 行
  - 默认 `http://localhost:8000`

```bash
# 配置服务端地址
ir config set base_url https://ir.example.com

# 或通过环境变量
export IR_BASE_URL=https://ir.example.com
```

### 2.3 获取帮助

```bash
ir --help                    # 查看所有命令组
ir portfolio --help          # 查看命令组下的子命令
ir portfolio create --help   # 查看具体命令的参数帮助
ir schema                    # 一次性输出全 CLI 机读 JSON 结构
ir schema --index            # 极简命令索引
ir schema trade              # 仅输出指定命令组
```

## 3. 输出格式

### 3.1 成功响应（exit code 0）

```json
{
  "ok": true,
  "data": { ... },
  "meta": { "total": 100, "page": 1, "page_size": 20 }
}
```

- `data`：主要数据，可以是对象或数组
- `meta`：分页元数据（仅列表命令返回），包含 `total`（总数）、`page`（当前页）、`page_size`（每页大小）
- `hints`：可选的顶层提示数组（如 create 返回 pending 时提示 confirm）

### 3.2 错误响应（exit code 1=业务错误 / 2=认证错误 / 3=连接错误）

```json
{
  "ok": false,
  "error": {
    "code": "NOT_FOUND",
    "message": "组合 PORT999 不存在",
    "hints": ["下一步补救命令"]
  }
}
```

### 3.3 错误码一览

`error.code` 有两个来源：**后端业务码**（响应体 `detail.error` 的结构化 code，绝大部分场景）与 **CLI 客户端码**（后端未带结构化 code 时由 CLI 生成）。后端码全量清单（触发条件 + HTTP 状态）见 `docs/reference/business-constraints.md`「错误码总表」，由后端 AST 守门测试保证与代码同步；一般 CLI 报错先查总表，只有总表查无此码时才是下表前半部分的 CLI 客户端码。

**CLI 客户端码**（来源 `ir_cli/`：HTTP 状态兜底映射、本地参数校验、网络层；后端带码时一律优先后端码，`FORBIDDEN`/`NOT_FOUND` 等与后端同名）：

| 错误码 | 触发 |
|--------|------|
| `AUTH_REQUIRED` | 401 兜底；或本地无 token 时不发请求直接拒绝（后端 401 为纯文本 detail，不带结构化码） |
| `FORBIDDEN` | 403 兜底（后端权限门 403 也用此码） |
| `NOT_FOUND` | 404 兜底（后端 `NotFoundError` 也用此码） |
| `CONFLICT` | 409 兜底（后端带码时用专用码，如 `RECALC_JOB_CONFLICT`；价格同步的 409 为纯文本 detail，落此兜底） |
| `VALIDATION_ERROR` | 422 兜底（FastAPI 请求体校验失败的 422 不带结构化码）；或 CLI 本地校验（日期格式、必填/更新字段缺失等） |
| `SERVER_ERROR` | ≥500 兜底 |
| `HTTP_ERROR` | 其余非 2xx 兜底 |
| `CONNECTION_ERROR` / `TIMEOUT_ERROR` / `NETWORK_ERROR` | 网络层失败：连接失败 / 超时 / 传输中断 |
| `INVALID_JSON` | 本地校验：`--json` 不是合法 JSON 对象 |

**常见后端码摘选**（完整清单与触发条件见总表）：

| 错误码 | 说明 |
|--------|------|
| `NOT_FOUND` | 资源不存在 |
| `ALREADY_EXISTS` | 资源已存在（唯一约束冲突） |
| `INVALID_STATUS` | 状态不允许当前操作 |
| `INVALID_PARAM` | 字段与记录类型/语义不匹配（如申购传份额、字段显式传 null） |
| `INVALID_AMOUNT` / `INVALID_SHARES` | 金额/份额不合法 |
| `INSUFFICIENT_CASH` | 买入金额超过可用现金 |
| `INSUFFICIENT_SHARES` | 卖出/赎回份额超过可用份额 |
| `NON_TRADING_DAY` | 提交日期不是交易日 |
| `MISSING_NAV` | 交易确认/快照生成时缺少净值数据 |
| `PRODUCT_NOT_FOUND` | 产品不存在（`details` 含查询的 code/market） |
| `MARKET_AMBIGUOUS` | 产品代码对应多个市场（如 LOF），需显式指定 market（`details.available_markets` 列出可选市场） |
| `NO_SNAPSHOT_BASELINE` | 组合无任何快照基线，无法追平/推进（先用 `snapshot generate` 建首日快照） |
| `CALENDAR_NOT_SYNCED` | 目标年份交易日历未同步（先执行 `system calendar-sync --year <年份>`） |
| `PENDING_TRANSACTIONS_EXIST` | 存在未处理的交易 |
| `PORTFOLIO_NOT_ACTIVE` | 组合未激活 |
| `INVESTOR_HAS_SHARES` | 投资人仍持有份额，不可删除 |
| `DATA_SOURCE_NOT_CONFIGURED` | 数据源未配置（`TUSHARE_TOKEN` 缺失），返回 503 |
| `SYNC_FAILED` | 数据源同步失败（接口报错或其他异常），返回 500 |
| `CASH_TRADE_FORBIDDEN` | 禁止直接创建 CASH 产品交易 |
| `CANNOT_CANCEL_EXCHANGE` | 场内交易不可取消 |
| `SNAPSHOT_DEPENDENCY` | 快照已依赖该记录，无法取消确认 |
| `SNAPSHOT_NOT_CONTINUOUS` | 快照日期不连续 |
| `INVALID_DIMENSION_TAGS` | 产品维度标签非法（不存在/已停用/违反维度规则/值级不适用，`details` 含 field/code/applicable_asset_classes 等） |
| `INVALID_CLASSIFICATION` | 维度字典值非法（code 前缀/dimension 不匹配、空适用关联、nonsense 关联等） |
| `INVALID_NAV_LAG_DAYS` | nav_lag_days 必须 >=0；场内（market=CN_EXCHANGE）必须为 0；显式传 null 拒绝；返回 422 |
| `INVALID_CONFIRM_DAYS` | confirm_days 必须 >=0；场内（market=CN_EXCHANGE）必须为 0；显式传 null 拒绝；返回 422 |
| `DIMENSION_VALUE_IN_USE` | 维度值关联仍被产品引用，不可移除（`details.products` 列引用产品） |
| `DIMENSION_RULE_CONFLICT` | 维度规则收紧与存量产品冲突（`details.products` 列冲突产品） |
| `CONFIRM_REQUIRED` | 需要显式确认（如 `--yes`） |

### 3.4 数据类型说明

- **日期参数**：所有日期参数使用 `YYYY-MM-DD` 格式字符串，如 `--apply-date 2025-01-06`
- **金额/份额**：使用浮点数输入，内部以 Decimal 运算；金额输出保留 2 位小数，份额统一 2 位小数（ROUND_HALF_UP，四舍五入）
- **ID 参数**：数值型主键，直接作为位置参数传入

### 3.5 AI Agent 友好特性

以下特性用于降低 AI agent 的学习成本与 token 消耗：

- **`ir schema [命令组]`**：一次性输出全 CLI 机读 JSON 结构，含命令树/参数/枚举取值/错误码补救指引/端到端业务配方（workflows）/输出协议，替代逐个 `--help` 探索；传命令组名（如 `ir schema trade`）仅输出单组。
- **`ir portfolio context <code>`**：操作前侦察聚合命令，一次返回组合详情/快照状态/实时可用现金/pending 申赎交易，替代 4-5 次分步查询。
- **`hints` 字段**：错误响应按错误码自动附加 `error.hints`（下一步补救命令，如 `SNAPSHOT_DEPENDENCY` → 先 `ir snapshot delete-bulk`）；关键写操作成功后输出顶层 `hints`（如 create 返回 pending 时提示 confirm、confirm 后提示生成快照）。映射表见 `ir_cli/hints.py`。
- **摘要字段默认输出**：`trade list` / `sub list` / `position list` / `log login|audit|error` 默认仅输出摘要字段（见 `ir_cli/utils.py::SUMMARY_FIELDS`），`--full` 输出全字段；优先级：显式 `--fields` > `--full` > 摘要预设。
- **`--quiet`**：`trade` / `sub` 的 create/confirm/cancel/unconfirm 仅输出 `{id, status, confirm_date}`。
- **`--json`**：所有 create/update 命令支持 `--json` 传完整 JSON 请求体，优先于逐项参数，适合复杂请求。
- **`--all`**：列表命令自动翻页获取全部记录。
- **plain help**：全部 `--help` 为无框线/无 ANSI 的纯文本输出，顶层 `ir --help` 含输出协议与退出码速览。

## 4. 命令详解

---

### 4.1 `ir auth` — 认证管理

#### `ir auth login`

登录获取 token（存储到 `~/.ir/token.json`）。

```bash
ir auth login --code <用户代码> --password <密码>
```

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `--code` | 是 | 用户代码（管理员或投资人） |
| `--password` | 是 | 登录密码 |

> 登录成功后 token 自动存储，后续命令自动携带。token 过期前 24 小时会在 stderr 打印警告。

#### `ir auth logout`

登出并清理本地 token。

```bash
ir auth logout
```

#### `ir auth change-password`

修改密码。

```bash
ir auth change-password --old-password <旧密码> --new-password <新密码>
```

> 修改密码后服务端会使旧 token 失效，需重新登录。

#### `ir auth status`

显示当前用户和 token 状态（本地操作，不请求服务端）。

```bash
ir auth status
```

> 未登录时返回 `AUTH_REQUIRED`（exit code 2）。

---

### 4.2 `ir investor` — 投资人管理

#### `ir investor list`

获取投资人列表（分页）。

```bash
ir investor list [--page N] [--page-size N] [--all]
```

| 参数 | 默认值 | 说明 |
|------|:------:|------|
| `--page` | 1 | 页码 |
| `--page-size` | 20 | 每页数量 |
| `--all` | false | 获取全部记录 |

#### `ir investor create`

创建投资人。

```bash
ir investor create --code <代码> --name <姓名> --password <密码> [--phone <手机>] [--email <邮箱>] [--role <角色>]
```

| 参数 | 必填 | 默认值 | 说明 |
|------|:----:|:------:|------|
| `--code` | 是 | — | 投资人代码（唯一标识） |
| `--name` | 是 | — | 投资人姓名 |
| `--password` | 是 | — | 登录密码 |
| `--phone` | 否 | — | 手机号 |
| `--email` | 否 | — | 邮箱 |
| `--role` | 否 | viewer | 角色：`admin` / `viewer` |

#### `ir investor get`

查看投资人详情。

```bash
ir investor get <CODE>
```

#### `ir investor update`

更新投资人信息。

```bash
ir investor update <CODE> [--name <姓名>] [--role <角色>] [--phone <手机>] [--email <邮箱>] [--password <新密码>]
```

所有选项参数均可选，仅更新传入的字段。

#### `ir investor delete`

删除投资人。

```bash
ir investor delete <CODE> [--yes]
```

| 参数 | 默认值 | 说明 |
|------|:------:|------|
| `--yes` | false | 跳过确认提示 |

> **约束**：投资人仍持有份额（`InvestorHolding.shares > 0`）时禁止删除。

---

### 4.3 `ir portfolio` — 组合管理

#### `ir portfolio list`

获取组合列表。

```bash
ir portfolio list [--status <状态>] [--page N] [--page-size N] [--all]
```

| 参数 | 默认值 | 说明 |
|------|:------:|------|
| `--status` | — | 按状态过滤：`draft` / `active` / `closed` |
| `--page` | 1 | 页码 |
| `--page-size` | 20 | 每页数量 |
| `--all` | false | 获取全部 |

#### `ir portfolio create`

创建组合（初始状态为 `draft`）。

```bash
ir portfolio create --code <代码> --name <名称> [--description <描述>] [--display-config <JSON>]
```

| 参数 | 默认值 | 说明 |
|------|:------:|------|
| `--display-config` | — | 持仓明细二级分组维度 JSON 对象，如 `'{"ASSET_STOCK": "style"}'`（issue #144） |

#### `ir portfolio get`

查看组合详情。

```bash
ir portfolio get <CODE>
```

#### `ir portfolio update`

更新组合信息。

```bash
ir portfolio update <CODE> [--name <名称>] [--description <描述>] [--display-config <JSON>]
```

| 参数 | 默认值 | 说明 |
|------|:------:|------|
| `--display-config` | — | 持仓明细二级分组维度 JSON 对象，如 `'{"ASSET_STOCK": "style"}'`（issue #144）；清空配置请用 `--json '{"display_config": null}'` |

#### `ir portfolio close`

关闭组合。

```bash
ir portfolio close <CODE> [--yes]
```

> **约束**：存在 pending 状态的申购/赎回或调仓交易时禁止关闭。

#### `ir portfolio reactivate`

重新激活已关闭的组合。

```bash
ir portfolio reactivate <CODE>
```

> **约束**：仅 `closed` 状态可激活。

#### `ir portfolio nav-history`

查看组合净值历史。

```bash
ir portfolio nav-history <CODE> [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]
```

返回单层数组，每行包含：`snapshot_date`、`unit_price`（单位净值）、`total_value`（总资产）、`total_shares`（总份额），按日期升序。

```json
{
  "ok": true,
  "data": [
    {"snapshot_date": "2025-01-06", "unit_price": 1.0, "total_value": 100000.0, "total_shares": 100000.0},
    {"snapshot_date": "2025-01-07", "unit_price": 1.01, "total_value": 101000.0, "total_shares": 100000.0}
  ]
}
```

#### `ir portfolio returns`

查看组合收益率。

```bash
ir portfolio returns <CODE>
```

返回数据包含：`cumulative_return`（累计收益率 %）、`annualized_return`（年化收益率 %）、`initial_nav`、`current_nav`、`holding_days`。

> **前提**：需要至少 2 条快照记录。

#### `ir portfolio cash-flow`

查看组合资金流。

```bash
ir portfolio cash-flow <CODE>
```

返回数据包含：`total_inflow`（总申购金额）、`total_outflow`（总赎回金额）、`net_inflow`（净流入）。

#### `ir portfolio context`

操作前侦察聚合命令，一次返回组合详情/快照状态/实时可用现金/pending 申赎交易。

```bash
ir portfolio context <CODE>
```

---

### 4.4 `ir position` — 持仓管理

#### `ir position list`

查看持仓（默认取每只产品的最新快照）。

```bash
ir position list --portfolio-code <组合代码> [--snapshot-date YYYY-MM-DD] [--page N] [--page-size N]
```

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `--portfolio-code` | 是 | 组合代码 |
| `--snapshot-date` | 否 | 指定快照日期，不传则取最新 |

#### `ir position available-cash`

查看组合可用现金（实时计算，非快照数据）。

```bash
ir position available-cash --portfolio-code <组合代码>
```

#### `ir position available-shares`

查看产品可用份额（实时计算）。

```bash
ir position available-shares --portfolio-code <组合代码> --product-code <产品代码> [--market <市场>]
```

#### `ir position update-cash`

更新非净值类现金市值：写入 `manual_market_value`（绝对替换），**不直接写快照表 `portfolio_position`**；写入后需重新生成快照方能反映到持仓（响应含 `requires_snapshot_regen: true`）。

```bash
ir position update-cash <PORTFOLIO_CODE> --platform-code <平台代码> --cash-amount <金额> [--update-date YYYY-MM-DD]
```

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `--platform-code` | 是 | 所属平台代码 |
| `--cash-amount` | 是 | 现金金额（绝对值，覆盖当日该平台现金市值） |
| `--update-date` | 否 | 更新日期（默认今天，必须是交易日） |

> 同日该平台存在已确认 CASH 交易时，响应 `warnings` 非空（覆盖层优先级高于当日交易，会压制其效果，不阻断写入）。

#### `ir position list-cash-overrides`

查询现金手动覆盖记录（`manual_market_value` 的 CASH 行，issue #88）。

```bash
ir position list-cash-overrides --portfolio-code <组合代码> [--platform-code <平台>] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]
```

#### `ir position delete-cash`

删除现金手动覆盖记录（issue #88）。删除后该日该平台回退到自然计算值；若覆盖已被快照纳入（响应 `requires_snapshot_regen: true`），需重算快照才生效。无对应记录返回 `MANUAL_OVERRIDE_NOT_FOUND`。

```bash
ir position delete-cash --portfolio-code <组合代码> --platform-code <平台> --update-date YYYY-MM-DD
```

---

### 4.5 `ir sub` — 申购赎回管理

#### `ir sub list`

获取申购赎回列表。

```bash
ir sub list [--portfolio-code <组合>] [--investor-code <投资人>] [--status <状态>] [--type <类型>] \
  [--platform-code <平台>] [--apply-date-start YYYY-MM-DD] [--apply-date-end YYYY-MM-DD] \
  [--confirm-date-start YYYY-MM-DD] [--confirm-date-end YYYY-MM-DD] [--page N] [--page-size N] [--all]
```

| 参数 | 说明 |
|------|------|
| `--status` | 状态过滤（`pending`/`confirmed`/`cancelled`） |
| `--type` | 类型过滤（`subscribe`/`redeem`） |
| `--platform-code` | 交易平台过滤 |
| `--apply-date-start` / `--apply-date-end` | 申请日期区间（闭区间；`start > end` 返回 422） |
| `--confirm-date-start` / `--confirm-date-end` | 确认日期区间（闭区间；对 pending 记录命中**预计确认日**） |

#### `ir sub create`

创建申购或赎回。

```bash
# 申购（按金额）
ir sub create --portfolio-code <组合> --investor-code <投资人> --type subscribe --amount <金额> --apply-date YYYY-MM-DD [--notes <备注>]

# 赎回（按份额）
ir sub create --portfolio-code <组合> --investor-code <投资人> --type redeem --shares <份额> --apply-date YYYY-MM-DD [--notes <备注>]
```

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `--portfolio-code` | 是 | 组合代码 |
| `--investor-code` | 是 | 投资人代码 |
| `--type` | 是 | `subscribe`（申购）或 `redeem`（赎回） |
| `--amount` | 申购时必填 | 申购金额（必须 > 0） |
| `--shares` | 赎回时必填 | 赎回份额（必须 > 0；先量化到 2 位小数再校验，不可超过可用份额） |
| `--apply-date` | 是 | 申请日期（必须是交易日） |
| `--notes` | 否 | 备注 |

> **业务规则**：
> - 申请日期必须是交易日
> - 组合状态必须为 `active` 或 `draft`
> - 赎回份额先量化到 2 位小数（四舍五入），再与投资人可用份额精确比较，超出则拒绝

#### `ir sub get`

查看申购赎回详情。

```bash
ir sub get <ID>
```

#### `ir sub confirm`

确认申购赎回。

```bash
ir sub confirm <ID> [--confirm-date YYYY-MM-DD] [--unit-price <净值>]
```

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `--confirm-date` | 否 | 确认日期（默认自动计算下一交易日） |
| `--unit-price` | 视情况 | 确认净值 |

> **业务规则**：
> - 仅 `pending` 状态可确认
> - **首次申购**：净值固定为 `1.0000`，无需提供 `--unit-price`，同时自动将组合从 `draft` 激活为 `active`
> - **首窗申购**（issue #179）：申请日无快照且当日无任何已到账申购资金时同样按 `1.0000` 计价（覆盖首日多平台/分笔申购）；已有资金到账则需先生成申请日快照，否则报 `NAV_NOT_AVAILABLE`
> - **乱序补录闸门**（issue #179）：确认日早于组合首笔到账日（`started_at`）报 `CONFIRM_BEFORE_STARTED`，需先 unconfirm 首笔申购后依序重录
> - **非首次申购/赎回**：净值取申请日组合快照，快照未生成时报 `NAV_NOT_AVAILABLE`

#### `ir sub cancel`

取消申购赎回（仅 `pending` 状态可操作）。

```bash
ir sub cancel <ID>
```

#### `ir sub unconfirm`

取消确认，将 `confirmed` 状态回退为 `pending`。

```bash
ir sub unconfirm <ID>
```

> - 确认日及之后已有快照时报 `SNAPSHOT_DEPENDENCY`，需先删除对应快照
> - **负现金防护**（issue #203）：unconfirm 不再因平台现金已被消耗而拦截（原 #180 守卫已移除）；负现金改在消费点拦截——赎回确认时平台可用现金不足报 `INSUFFICIENT_CASH`，快照生成时现金为负报 `NEGATIVE_CASH`
> - **状态回退**（issue #180）：回退后组合 `started_at` 重算为现存最小确认日；无任何确认申购时组合回退 `draft`（`closed` 组合保持 `closed`）

#### `ir sub update`

编辑申赎（仅 `pending` 可改，`confirmed` 需先 `unconfirm`）。

```bash
ir sub update <ID> [--amount <金额>] [--shares <份额>] [--unit-price <净值>] \
  [--platform-code <平台>] [--apply-date YYYY-MM-DD] [--notes <备注>]
```

> - 改 `--apply-date` 时后端校验交易日 + 晚于最新快照日，并自动重算预计确认日（T+1，issue #202）
> - 字段按类型收口（与创建同口径）：申购仅可改 `--amount`、赎回仅可改 `--shares`，错位报 `INVALID_PARAM`（PR #204 评审）
> - 赎回改份额与创建同口径：先量化 2 位再与可用份额精确比较（加回本条自身 pending 旧份额）

---

### 4.6 `ir trade` — 调仓交易管理

#### `ir trade list`

获取调仓交易列表。

```bash
ir trade list [--portfolio-code <组合>] [--status <状态>] [--type <类型>] \
  [--product-code <产品>] [--market <市场>] [--platform-code <平台>] \
  [--trade-date-start YYYY-MM-DD] [--trade-date-end YYYY-MM-DD] \
  [--confirm-date-start YYYY-MM-DD] [--confirm-date-end YYYY-MM-DD] [--page N] [--page-size N] [--all]
```

| 参数 | 说明 |
|------|------|
| `--status` | 状态过滤（`pending`/`confirmed`/`cancelled`） |
| `--type` | 类型过滤（`buy`/`sell`） |
| `--product-code` | 产品过滤（单独使用时跨市场全匹配，LOF 一码多市场均命中） |
| `--market` | 市场过滤（与 `--product-code` 组合为精确过滤） |
| `--platform-code` | 交易平台过滤 |
| `--trade-date-start` / `--trade-date-end` | 交易日期区间（闭区间；`start > end` 返回 422） |
| `--confirm-date-start` / `--confirm-date-end` | 确认日期区间（闭区间；对 pending 记录命中**预计确认日**） |

#### `ir trade create`

创建买入/卖出交易。

```bash
# 买入
ir trade create --portfolio-code <组合> --product-code <产品> [--market <市场>] \
  --type buy --actual-amount <实际金额> --fee <手续费> --price <价格> \
  --trade-date YYYY-MM-DD --platform-code <平台> [--cash-platform-code <现金平台>] [--shares <份额>] [--notes <备注>]

# 卖出
ir trade create --portfolio-code <组合> --product-code <产品> [--market <市场>] \
  --type sell --shares <份额> --trade-date YYYY-MM-DD [--actual-amount <实际金额>] \
  [--fee <手续费>] --platform-code <平台> [--cash-platform-code <现金平台>] [--notes <备注>]
```

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `--portfolio-code` | 是 | 组合代码（必须为 `active` 状态） |
| `--product-code` | 是 | 产品代码 |
| `--market` | 否 | 市场类型：`CN_OTC` / `CN_EXCHANGE` / `HK_MUTUAL`；省略时自动解析，一码多市场（如 LOF）返回 `MARKET_AMBIGUOUS`，需显式指定 |
| `--type` | 是 | `buy`（买入）或 `sell`（卖出） |
| `--actual-amount` | 买入时必填、卖出可选 | 实际金额（方向敏感）：买入=含费现金支出（必须 > 0，不超过扣款平台可用现金）；卖出=到手净额（#190 起为纯派生量，有价格时按 `shares×price−fee` 推导，显式传入仅作对账校验，对不上报 `AMOUNT_MISMATCH`） |
| `--amount` | 否 | 与 `--actual-amount` 同义（`--actual-amount` 优先）；卖出时仅作对账校验 |
| `--fee` | 否（默认0） | 手续费 |
| `--price` | 场内必填 | 交易价格；场内（CN_EXCHANGE）必填；任意市场显式传价均须为正数（`MISSING_OR_INVALID_PRICE`）；卖出传价将按 `shares×price` 推导金额（场内对账超差报 `AMOUNT_MISMATCH`，场外仅推导展示不强对账，确认时仅与 T 日净值做一致性校验，不覆盖净值） |
| `--shares` | 卖出时必填 | 卖出份额（必须 > 0；先量化到 2 位小数再校验，不超过可用份额） |
| `--platform-code` | 是 | 平台代码（必须已存在，缺失报 `PLATFORM_REQUIRED`、不存在报 `PLATFORM_NOT_FOUND`） |
| `--cash-platform-code` | 否 | 现金腿平台（issue #91）：买=扣款平台、卖=到账平台，缺省同基金腿平台；买入可用现金按扣款平台校验，两腿同 transfer_group 原子翻转 |
| `--trade-date` | 是 | 交易日期（必须是交易日） |
| `--notes` | 否 | 备注 |

#### `ir trade get`

查看交易详情。

```bash
ir trade get <ID>
```

#### `ir trade preview`

确认前预览：返回真实确认将写入的净值/份额/金额，不落库（与 `confirm` 共用同一计算实现，预览 == 真实确认）。

```bash
ir trade preview <ID> [--confirm-date YYYY-MM-DD] [--price <价格>]
```

> **说明**：
> - 仅 `pending` 状态可预览，否则返回 `INVALID_STATUS`
> - 输出 `trade`（当前交易）+ `preview`（将写入的 price/shares/amount/actual_amount/fee/confirm_date/nav_date/is_otc_nav_fund）+ `paired_cash_amount`（配对 CASH 腿将同步的金额）
> - 场外基金 T 日净值缺失返回 `MISSING_NAV`；传入 `--price` 与净值不一致返回 `PRICE_NAV_MISMATCH`
> - 预览为时点快照，核对无误后执行 `ir trade confirm <ID>`

#### `ir trade confirm`

确认交易（自动获取净值；确认间隔取产品落库的 `confirm_days`）。

```bash
ir trade confirm <ID> [--confirm-date YYYY-MM-DD] [--price <价格>] [--sync-nav]
```

> **业务规则**：
> - 仅 `pending` 状态可确认
> - 确认日期根据产品落库的 `confirm_days` 自动计算（普通场外基金 T+1，场外 QDII 通常 T+2），不在确认时按 `is_qdii` 二次推断
> - 场外净值型产品（OEF/LOF + CN_OTC/HK_MUTUAL）**一律**从 `PriceRecord` 获取交易日（T 日）净值重算 shares/amount，缺失报 `MISSING_NAV`——QDII / 互认基金也不例外
> - issue #228：快照估值侧的滞后取价（`nav_lag_days`）与确认侧正交，确认恒取 T 日净值
> - `--price`：场外基金仅用于与 T 日净值一致性校验（不一致报 `PRICE_NAV_MISMATCH`，不覆盖净值）；场内基金作为覆盖成交价
> - `--sync-nav`（issue #90）：命中 `MISSING_NAV` 时自动回填该标的历史净值后重试一次（会访问外部数据源），同步后仍缺失则照常报 `MISSING_NAV`

#### `ir trade cancel`

取消交易。

```bash
ir trade cancel <ID>
```

> **约束**：仅 `pending` 状态且非场内交易（`CN_EXCHANGE`）可取消。

#### `ir trade unconfirm`

取消确认，将 `confirmed` 状态回退为 `pending`。

```bash
ir trade unconfirm <ID>
```

#### `ir trade update`

编辑交易（仅 `pending` 状态可改，`confirmed` 需先 `unconfirm`；`cancelled` 不可改）。

```bash
ir trade update <ID> [--shares <份额>] [--amount <金额>] [--price <价格>] \
  [--fee <手续费>] [--actual-amount <实际金额>] [--trade-date YYYY-MM-DD] [--notes <备注>]
```

| 参数 | 说明 |
|------|------|
| `--amount` / `--actual-amount` | 实际金额，两参同义（`--actual-amount` 优先）：buy=含费现金支出（联动 `actual_amount=X`、`amount=X−fee`、CASH 腿=X）；sell 有价格时与创建同口径（#190）：按 shares×price 重推导、显式金额仅作对账（场内超差报 `AMOUNT_MISMATCH`），无价格占位单仍输入为准（联动 `actual_amount=X`、`amount=X+fee`、CASH 腿=X） |
| `--shares` | 卖出份额（先量化到 2 位小数再校验，不超过可用份额） |
| `--price` / `--fee` / `--notes` | 直改字段；仅改这些字段不触发可用量校验 |
| `--trade-date` | 新交易日期（必须是交易日且晚于最新快照日，非交易日直接报错不静默滚交易日；联动重算 confirm_date 并同步 CASH 腿） |

> **业务规则**（issue #182，与创建同口径校验）：
> - 改金额/份额/日期时实时校验可用量：buy 按扣款平台可用现金、sell 按可用份额（均加回自身 pending 旧值），不足返回 `INSUFFICIENT_CASH` / `INSUFFICIENT_SHARES`
> - 编辑成与另一笔交易撞自然键（同组合/产品/市场/平台/方向/交易日且金额或份额相同）返回 `DUPLICATE_TRADE`，无 `allow_duplicate` 逃生口
> - CASH 腿（配对现金腿）仅允许改 `--notes`，其余字段返回 `CASH_TRADE_FORBIDDEN`
> - 校验失败零部分写入，交易保持原值

---

### 4.7 `ir share-event` — 份额变动事件管理

管理分红、再投资、拆合股等份额变动事件。

#### `ir share-event list`

获取事件列表。

```bash
ir share-event list [--portfolio-code <组合>] [--page N] [--page-size N] [--all]
```

#### `ir share-event create`

创建份额变动事件。

```bash
ir share-event create \
  --portfolio-code <组合> --product-code <产品> --market <市场> \
  --event-type <事件类型> \
  --ex-date YYYY-MM-DD --entitlement-date YYYY-MM-DD \
  [--platform-code <平台>] \
  [--entitlement-shares N] [--shares-before N] [--shares-change N] [--shares-after N] \
  [--ratio N] [--div-cash N] [--reinvest-nav N] [--cash-change N] \
  [--event-source <来源>] [--notes <备注>] [--force-cover]
```

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `--portfolio-code` | 是 | 组合代码 |
| `--product-code` | 是 | 产品代码 |
| `--market` | 是 | 市场类型 |
| `--event-type` | 是 | 事件类型（见下表） |
| `--ex-date` | 是 | 除息日/应用日（**必须是交易日**，且 > 权益登记日） |
| `--entitlement-date` | 是 | 权益登记日（基数日，**必须是交易日**） |
| `--platform-code` | 视类型 | 平台级事件（cash_dividend/reinvest_dividend/forced_adjustment）必填 |
| `--entitlement-shares` | 否 | 权益登记日份额 |
| `--shares-before` | 否 | 变动前份额 |
| `--shares-change` | 否 | 变动份额 |
| `--shares-after` | 否 | 变动后份额 |
| `--ratio` | 否 | 比例（拆合股用） |
| `--div-cash` | 否 | 现金分红金额 |
| `--reinvest-nav` | 否 | 再投资净值 |
| `--cash-change` | 否 | 现金变动 |
| `--event-source` | 否 | 事件来源 |
| `--notes` | 否 | 备注 |
| `--force-cover` | 否 | 平台覆盖不全时降为 warning（默认阻断） |

**事件类型：**

| 类型 | 说明 |
|------|------|
| `cash_dividend` | 现金分红 |
| `reinvest_dividend` | 红利再投资 |
| `share_split` | 份额拆分 |
| `share_merge` | 份额合并 |
| `bonus_share` | 送股 |
| `forced_adjustment` | 强制调整 |

#### `ir share-event get`

查看事件详情。

```bash
ir share-event get <ID>
```

#### `ir share-event update`

更新事件信息。

```bash
ir share-event update <ID> [--ex-date YYYY-MM-DD] [--entitlement-shares N] [--ratio N] \
  [--div-cash N] [--reinvest-nav N] [--cash-change N] [--notes <备注>]
```

#### `ir share-event delete`

删除事件。

```bash
ir share-event delete <ID> [--yes]
```

#### `ir share-event confirm`

确认事件（校验权益登记日持仓快照存在）。

```bash
ir share-event confirm <ID>
```

> **约束**：仅 `pending` 状态可确认，且权益登记日必须有持仓快照。

#### `ir share-event cancel`

取消事件（仅 `pending` 状态可操作）。

```bash
ir share-event cancel <ID>
```

#### `ir share-event unconfirm`

取消确认事件（`confirmed` → `pending`）。

```bash
ir share-event unconfirm <ID>
```

> **约束**：仅 `confirmed` 状态可取消确认；`ex_date` 及之后已有快照则返回 `SNAPSHOT_DEPENDENCY`（需先删除对应快照）。基金级父记录会级联删除其拆分子记录后置 `pending`；子记录单独 unconfirm 会被拒绝（`CANNOT_UNCONFIRM_CHILD`）。

---

### 4.8 `ir market` — 市场数据

#### `ir market price`

查询产品价格数据。

```bash
ir market price <PRODUCT_CODE> <MARKET> [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--limit N]
```

| 参数 | 默认值 | 说明 |
|------|:------:|------|
| `--start-date` | — | 起始日期 |
| `--end-date` | — | 截止日期 |
| `--limit` | 50 | 最大返回条数（按日期降序） |

#### `ir market nav-coverage`

校验区间内净值同步覆盖情况（以交易日历为基准，列出缺失日期）。

```bash
ir market nav-coverage <PRODUCT_CODE> <MARKET> --start-date YYYY-MM-DD [--end-date YYYY-MM-DD]
```

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `--start-date` | 是 | 开始日期 |
| `--end-date` | 否 | 结束日期，默认今天 |

返回示例：

```json
{
  "ok": true,
  "data": {
    "product_code": "000300.OF",
    "market": "CN_OTC",
    "start_date": "2025-01-06",
    "end_date": "2025-01-10",
    "total_trading_days": 5,
    "synced_days": 4,
    "coverage": 0.8,
    "missing_dates": ["2025-01-08"]
  }
}
```

> `coverage` = `synced_days / total_trading_days`；区间内无交易日时为 `null`。

#### `ir market sync`

从 Tushare 同步产品价格数据。

```bash
ir market sync <PRODUCT_CODE> <MARKET> [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD]
```

#### `ir market sync-history`

同步产品近 90 天历史数据（自动计算日期范围）。

```bash
ir market sync-history <PRODUCT_CODE> <MARKET>
```

---

### 4.9 `ir product` — 产品管理

#### `ir product list`

获取产品列表。

```bash
ir product list [--product-type <类型>] [--include-virtual] [--page N] [--page-size N] [--all]
```

| 参数 | 说明 |
|------|------|
| `--product-type` | 按类型过滤：`ETF` / `OEF` / `LOF` / `CASH` / `IN_TRANSIT` |
| `--include-virtual` | 包含虚拟产品（CASH/IN_TRANSIT*）；#327 起后端默认排除虚拟产品，本旗标显式包含 |

#### `ir product create`

创建产品（`confirm_days` 显式传入优先；不传由后端按市场 + QDII 属性自动推导）。

```bash
ir product create --code <代码> --market <市场> --name <名称> --product-type <类型> \
  [--asset-class-code <资产类别>] [--confirm-days N] [--nav-lag-days N] [--is-qdii] [--data-source <数据源>] [--sync]
```

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `--code` | 是 | 产品代码（如 `000051.OF`） |
| `--market` | 是 | 市场：`CN_EXCHANGE` / `CN_OTC` / `HK_MUTUAL` |
| `--name` | 是 | 产品名称 |
| `--product-type` | 是 | 类型：`ETF` / `OEF` / `LOF` / `CASH` / `IN_TRANSIT` |
| `--asset-class-code` | 否 | 资产类别代码 |
| `--confirm-days` | 否 | 确认天数（#231/#236/#241）：显式传入优先（>=0；场内 `CN_EXCHANGE` 必须 0，否则 422 `INVALID_CONFIRM_DAYS`）；不传按下表推导 |
| `--nav-lag-days` | 否 | 快照估值取价滞后交易日数（issue #228）：默认 `0` 取当日净值，`N` 取前第 N 个交易日净值；场外 QDII / 香港互认基金填 `1` |
| `--is-qdii` | 否 | 是否为 QDII 产品（默认 false）；**纯展示标签**，仅在创建时参与 `confirm_days` 默认值推导，不影响快照取价 |
| `--data-source` | 否 | 数据源（如 `tushare`） |
| `--sync` | 否 | 创建后立即回填历史净值（issue #90）；同步结果在响应 `sync_result`，失败不阻断创建 |

**未传 `--confirm-days` 时的自动推导规则（创建后需要其它值用 `ir product update --confirm-days`）：**

| 市场 | QDII | confirm_days |
|------|:----:|:------------:|
| `CN_EXCHANGE` | — | 0 |
| `CN_OTC` | 否 | 1 |
| `CN_OTC` | 是 | 2 |
| 其他（含 `HK_MUTUAL` / 空市场） | — | 1 |

#### `ir product get`

查看产品详情（复合主键：代码 + 市场）。

```bash
ir product get <CODE> [MARKET]
```

> `MARKET` 可省略：一码一市场时自动解析；一码多市场（如 LOF 同时存在 `CN_EXCHANGE`/`CN_OTC`）返回 `MARKET_AMBIGUOUS`，需显式指定。对应 REST 新增端点 `GET /api/products/{code}`（不带 market）。

#### `ir product update`

更新产品信息。

```bash
ir product update <CODE> <MARKET> [--name <名称>] [--product-type <类型>] [--is-qdii/--no-qdii] \
  [--confirm-days N] [--nav-lag-days N] [--asset-class-code <类别>] [--data-source <数据源>]
```

> **issue #228 语义**：
> - `--is-qdii` 为**纯展示标签**，更新它**不再**联动重算 `confirm_days`
> - `--confirm-days` / `--nav-lag-days` 纯显式更新：不传不改，传什么就是什么（唯一例外：`market` 变更时未传 `--confirm-days` 按新市场重推导，见下方 #232）。取值经服务端统一校验（422，错误码 `INVALID_CONFIRM_DAYS` / `INVALID_NAV_LAG_DAYS`）：必须 `>=0`；场内（`CN_EXCHANGE`）必须为 `0`；显式传 null 拒绝
> - `--nav-lag-days 1` 用于香港互认基金等 T-1 披露净值的产品（快照按前一个交易日净值估值）；迁移 `0012` 仅回填场外 QDII，互认基金需手工设置
>
> **issue #232 语义**：
> - `--product-type` 可纠错产品类型（合法值 `ETF`/`OEF`/`LOF`/`CASH`/`IN_TRANSIT`，非法值 422）；存在 pending 交易/事件时拒绝修改
> - `market` 变更须用 `--json '{"market": "CN_OTC"}'`：**仅限无交易/事件/持仓引用的产品**（有引用返回 `MARKET_CHANGE_REFERENCED` 及各表计数）；已有行情随行迁移并提示重新 `sync-history`；未显式传 `--confirm-days` 时按新市场重推导默认值
> - 请求体含未声明字段一律 422（此前会被静默忽略、返回 ok 却不生效）

#### `ir product delete`

删除产品。

```bash
ir product delete <CODE> <MARKET> [--yes]
```

---

### 4.10 `ir platform` — 平台管理

#### `ir platform list`

获取平台列表。

```bash
ir platform list [--page N] [--page-size N] [--all]
```

#### `ir platform create`

创建平台。

```bash
ir platform create --code <代码> --name <名称> [--platform-type <类型>]
```

#### `ir platform get`

查看平台详情。

```bash
ir platform get <CODE>
```

#### `ir platform update`

更新平台信息。

```bash
ir platform update <CODE> [--name <名称>] [--platform-type <类型>]
```

#### `ir platform delete`

删除平台。

```bash
ir platform delete <CODE> [--yes]
```

---

### 4.11 `ir snapshot` — 快照管理

#### `ir snapshot generate`

生成单日快照。

```bash
ir snapshot generate --portfolio-code <组合> --target-date YYYY-MM-DD
```

> - 净值严格匹配（issue #96/#178，#228 起由产品 `nav_lag_days` 驱动）：取价日 = `nav_lag_days=0` 时的 target_date 当日净值，`nav_lag_days=N` 时的前第 N 个交易日净值（场外 QDII / 香港互认基金为 1，即 T-1）；禁止向前回退，任一持仓缺失即失败并返回 `MISSING_NAV`（错误信息按取价规则分组列出缺失产品与所需日期，形如 `[T=2025-06-10]` / `[T-1=2025-06-09]`），先用 `ir market sync-history <product_code> <market>` 回填净值后重试
> - `is_qdii` 不参与取价判断（纯展示标签）；交易确认侧仍恒取 T 日净值，不受 `nav_lag_days` 影响
> - 生成失败不产生任何快照数据（目标日仍缺失，可修复数据后安全重试）
> - **零快照防呆**（issue #180）：组合尚无任何快照且目标日之前已有确认交易时报 `SNAPSHOT_REQUIRES_RECALCULATE`（单日生成会漏掉早期到账记录），改用 `ir snapshot recalculate` 从最早确认日起逐日重建

#### `ir snapshot recalculate`

区间重算快照。大区间重算耗时长，建议加 `--async` 提交后台任务，避免客户端超时后无法判定终态（issue #89）。

```bash
ir snapshot recalculate --start-date YYYY-MM-DD --end-date YYYY-MM-DD [--portfolio-code <组合>] [--force]

# 异步模式（issue #89）
ir snapshot recalculate --start-date YYYY-MM-DD --end-date YYYY-MM-DD [--portfolio-code <组合>] --async
ir snapshot recalculate ... --wait [--poll-interval 5]   # 提交后轮询至终态再返回
```

| 参数 | 说明 |
|------|------|
| `--portfolio-code` | 指定组合（不传则重算所有活跃组合） |
| `--start-date` | 起始日期（必填） |
| `--end-date` | 截止日期（必填） |
| `--force` | 跳过校验强制重算 |
| `--async` | 提交后台任务立即返回 job_id，用 `ir sync-job status <id>` 轮询终态（success=已提交 / failed=已整体回滚） |
| `--wait` | 隐含 `--async`，提交后轮询至终态再返回；每次轮询都是短请求，不受长事务超时影响 |

> 异步模式事务语义与同步一致：后台按 errors 统一 commit/rollback，对外仍是「要么完整成功，要么无变化」；已有重算任务在跑时返回 409 `RECALC_JOB_CONFLICT`。

#### `ir snapshot catch-up`

快照追平：从最新快照日的次一交易日逐日生成到目标日期。

```bash
ir snapshot catch-up --portfolio-code <组合> --to-date YYYY-MM-DD
```

返回 `generated_count`（生成数）、`generated_dates`（升序日期列表）、`latest_snapshot_date`；逐日 checkpoint 语义，失败时已成功的日子已落库，`failed_date`/`error` 标记中断点。

> 组合无任何快照时返回 `NO_SNAPSHOT_BASELINE`，需先用 `ir snapshot generate` 建首日基线。

#### `ir snapshot generate-next`

生成下一交易日快照（只推进一天，日期由服务端自动推算）。

```bash
ir snapshot generate-next --portfolio-code <组合>
```

返回 `generated_date`（本次生成日）及 `total_value` / `total_shares` / `unit_price`。

#### `ir snapshot validate`

校验指定日期的快照依赖数据是否齐全。

```bash
ir snapshot validate --portfolio-code <组合> --target-date YYYY-MM-DD
```

返回 `is_valid`（是否有效）和 `checks`（各项检查详情）。

#### `ir snapshot status`

查看组合快照状态。

```bash
ir snapshot status <PORTFOLIO_CODE>
```

返回 `nav_snapshot_count`（快照总数）、`latest_date`（最新日期）、`earliest_date`（最早日期）。

#### `ir snapshot delete`

删除指定日期的快照（包括持仓快照、净值快照、投资人持仓三类数据）。

```bash
ir snapshot delete <PORTFOLIO_CODE> <SNAPSHOT_DATE> [--yes]
```

#### `ir snapshot delete-bulk`

批量删除从起始日期（含当日）起的所有快照，从最新快照日倒序逐日删除并级联回退。

```bash
ir snapshot delete-bulk <PORTFOLIO_CODE> <FROM_DATE> --yes
ir snapshot delete-bulk <PORTFOLIO_CODE> <FROM_DATE> --dry-run   # 仅预览，不执行删除
```

| 参数 | 默认值 | 说明 |
|------|:------:|------|
| `--yes` | false | 必传。不带 `--yes` 时拒绝执行（`CONFIRM_REQUIRED`） |
| `--dry-run` | false | 仅预览将删除的快照日期列表，不执行删除（无需 `--yes`） |

> **破坏性操作**：逐日 commit，不可中途回滚；建议先用 `--dry-run` 预览影响面。对应的 REST 端点 `DELETE /api/snapshots/{portfolio_code}/bulk/{from_date}` 同样支持 `dry_run=true` Query 参数，且真删除时要求显式传 `confirm=true`，否则返回 422 `CONFIRM_REQUIRED`（兼作影响面预览）。

---

### 4.12 `ir system` — 系统管理

#### `ir system trading-day` — 交易日查询

嵌套子命令组，推算/判断交易日（对应 REST 端点 `GET /api/trading-calendar/next|prev|is-open`）。

```bash
# 起始日期之后第 N 个交易日（默认 N=1）
ir system trading-day next --from-date YYYY-MM-DD [--days N]

# 起始日期之前第 N 个交易日（默认 N=1）
ir system trading-day prev --from-date YYYY-MM-DD [--days N]

# 判断指定日期是否为交易日
ir system trading-day is-open <DATE>
```

| 参数 | 说明 |
|------|------|
| `--from-date` | 起始日期（next/prev 必填） |
| `--days` | 前/后第 N 个交易日，范围 1–365，默认 1 |

> 目标年份日历未同步时返回 `CALENDAR_NOT_SYNCED`，先执行 `ir system calendar-sync --year <年份>`。

#### `ir system calendar`

查询交易日历。

```bash
ir system calendar [--year N] [--start-date YYYY-MM-DD] [--end-date YYYY-MM-DD] [--is-open true/false]
```

| 参数 | 说明 |
|------|------|
| `--year` | 按年份查询 |
| `--start-date` | 起始日期 |
| `--end-date` | 截止日期 |
| `--is-open` | 按是否交易日过滤 |

#### `ir system calendar-sync`

从 Tushare 同步交易日历。

```bash
ir system calendar-sync --year <年份>
```

#### `ir system datasources`

查看数据源配置（API key 脱敏显示）。

```bash
ir system datasources
```

#### `ir system datasource-update`

更新数据源配置。

```bash
ir system datasource-update tushare --api-key <新的Tushare Token>
```

> 更新后自动刷新配置缓存。

---

### 4.13 `ir log` — 日志管理

#### `ir log login`

查询登录日志。

```bash
ir log login [--page N] [--page-size N]
```

#### `ir log audit`

查询审计日志。

```bash
ir log audit [--page N] [--page-size N]
```

#### `ir log error`

查询系统错误日志。

```bash
ir log error [--page N] [--page-size N]
```

---

### 4.14 `ir task` — 定时任务管理

#### `ir task list`

获取定时任务列表。

```bash
ir task list [--page N] [--page-size N]
```

#### `ir task describe`

查看单个任务详情（含最近一次执行记录，用于失败诊断）。

```bash
ir task describe <CODE>
```

返回任务基本信息（cron 表达式、启用状态、上次/下次运行时间）+ `last_execution`（最近一次执行日志，含耗时、成功/失败记录数、错误信息）。对应 REST 新增端点 `GET /api/system/tasks/{code}`。

#### `ir task run`

手动执行任务。

```bash
ir task run <CODE>
```

**支持的任务代码：**

| 任务代码 | 说明 |
|----------|------|
| `nav_sync` | 净值同步 |
| `snapshot_generate` | 组合快照生成（仅处理开启自动快照的组合） |
| `trading_calendar_sync` | 交易日历同步 |
| `log_cleanup` | 过期日志清理 |

#### `ir task enable`

启用任务。

```bash
ir task enable <CODE>
```

#### `ir task disable`

禁用任务。

```bash
ir task disable <CODE>
```

#### `ir task logs`

查看任务执行日志。

```bash
ir task logs <CODE> [--page N] [--page-size N]
```

---

### 4.15 `ir cash-transfer` — 现金转移管理

管理平台间现金转移，复用 `trade` 表，一次转移生成两条 CASH 腿（sell + buy）。

#### `ir cash-transfer create`

创建平台间现金转移。

```bash
ir cash-transfer create --portfolio-code <组合> --from <转出平台> --to <转入平台> \
  --amount <金额> --date YYYY-MM-DD [--cross-day] [--notes <备注>] [--json <完整JSON>]
```

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `--portfolio-code` | 是 | 组合代码 |
| `--from` | 是 | 转出平台代码 |
| `--to` | 是 | 转入平台代码 |
| `--amount` | 是 | 转移金额 |
| `--date` | 是 | 转出日期（YYYY-MM-DD，必须是交易日） |
| `--cross-day` | 否 | 是否跨天到账（默认 false，当天完成） |
| `--notes` | 否 | 备注 |
| `--json` | 否 | 完整 JSON 请求体，优先于逐项参数 |

> - **当天完成**（`cross_day=False`）：两腿立即 confirmed
> - **跨天到账**（`cross_day=True`）：转出方当日 confirmed，转入方 pending 待次日确认

#### `ir cash-transfer list`

查询现金转移记录。

```bash
ir cash-transfer list --portfolio-code <组合> [--page N] [--page-size N] [--all]
```

#### `ir cash-transfer confirm`

确认跨天转移的转入腿。

```bash
ir cash-transfer confirm --portfolio-code <组合> --group <transfer_group>
```

---

### 4.16 `ir sync-job` — 同步任务管理

查询异步同步任务状态（如快照重算后台任务）。

#### `ir sync-job status`

查询同步任务状态。

```bash
ir sync-job status <JOB_ID>
```

#### `ir sync-job details`

查询同步任务逐产品明细。

```bash
ir sync-job details <JOB_ID>
```

---

### 4.17 `ir notification` — 通知管理

#### `ir notification list`

获取通知列表。

```bash
ir notification list [--status <状态>] [--page N] [--page-size N] [--all] [--fields <字段>]
```

| 参数 | 说明 |
|------|------|
| `--status` | 状态筛选：`pending` / `read` |

#### `ir notification read`

标记通知为已读。

```bash
ir notification read <ID>
```

#### `ir notification read-all`

标记全部通知为已读。

```bash
ir notification read-all
```

---

### 4.18 `ir asset-classification` — 资产分类维度字典管理

五维正交维度字典（asset_class/region/style/size/segment）的查看与维护（issue #135）。无 delete 命令，后悔药用 `update --inactive` 软失效（存量引用不阻断）。

#### `ir asset-classification list`

获取维度值字典（含停用值，`is_active` 字段标识；维度级规则矩阵见 `get` 单条的 `dimension_rules`）。

```bash
ir asset-classification list [--dimension <维度>] [--fields <字段>] [--full]
```

#### `ir asset-classification get`

查看维度值详情；asset_class 维度值附 `dimension_rules`（`{dimension: rule}`，rule ∈ required/optional，未出现的维度 = forbidden）。

```bash
ir asset-classification get <CODE>
```

#### `ir asset-classification create`

新建维度值。code 须全大写且前缀匹配维度（ASSET_/REGION_/STYLE_/SIZE_/SEG_）；非 asset_class 维度必须 `--applicable` 指定 ≥1 适用大类（目标大类规则须允许该维度）；asset_class 维度可重复 `--rule` 配维度规则（缺省 = 现金型全禁止，配好后产品立即可用）。

```bash
ir asset-classification create --code <代码> --dimension <维度> --name <名称> \
  [--sort-order N] [--description <描述>] \
  [--applicable ASSET_STOCK,ASSET_BOND] \
  [--rule region=required --rule segment=optional]
```

#### `ir asset-classification update`

更新维度值（code/dimension 不可改）。`--applicable` 与 `--rule` 为**全量替换**语义：移除被产品引用的关联报 `DIMENSION_VALUE_IN_USE`（details 列产品）；关联不可减到 0；规则收紧（→required 需存量该维度全非空，删规则行 = forbidden 需全空）冲突报 `DIMENSION_RULE_CONFLICT`。编辑 asset_class 的 `--sort-order` 即变更前端饼图/分区色板序位（改色）。

```bash
ir asset-classification update <CODE> [--name <名称>] [--sort-order N] \
  [--description <描述>] [--active/--inactive] \
  [--applicable ASSET_STOCK,ASSET_BOND] [--rule region=required ...]
```

---

### 4.19 `ir config` — 本地配置管理

纯本地操作，读写 `~/.ir/config` 文件。

#### `ir config set`

写入配置项。

```bash
ir config set base_url https://ir.example.com
```

| 配置键 | 说明 |
|--------|------|
| `base_url` | 服务端地址（优先级低于 `IR_BASE_URL` 环境变量） |

#### `ir config show`

显示当前生效配置（含环境变量覆盖后的实际值）。

```bash
ir config show
```

---

### 4.20 `ir schema` — CLI 机读结构

输出全 CLI 机读 JSON 结构，供 AI agent 一次性了解全部指令。

```bash
# 全量输出
ir schema

# 极简命令索引（<1KB）
ir schema --index

# 仅输出指定命令组
ir schema trade
```

| 参数 | 说明 |
|------|------|
| `group` | 命令组名（可选，如 `trade`） |
| `--index` | 仅输出极简命令索引，与命令组参数互斥 |

> 输出包含：命令树/参数/枚举取值/错误码补救指引/端到端业务配方（workflows）/输出协议/响应字段契约。响应字段契约由 `ir-cli/scripts/gen_response_fields.py` 从 `backend/openapi.json` 生成，CI 做一致性校验。

---

## 5. 典型业务流程

### 5.1 从零开始创建组合并完成首笔交易

```bash
# 0. 配置服务端地址（首次使用）
ir config set base_url http://localhost:8000

# 1. 登录（管理员账户需通过 REST API 或初始化脚本创建）
ir auth login --code ADMIN --password "pass123"

# 2. 创建投资人
ir investor create --code INV001 --name "张三" --password "pass123"

# 3. 创建组合
ir portfolio create --code PORT001 --name "个人养老金"

# 4. 首次申购（净值固定 1.0000，组合自动激活为 active）
ir sub create --portfolio-code PORT001 --investor-code INV001 \
  --type subscribe --amount 100000 --apply-date 2025-01-06
# 记录返回的 ID，假设为 1

# 5. 确认申购
ir sub confirm 1

# 6. 查看可用现金
ir position available-cash --portfolio-code PORT001

# 7. 创建买入交易
ir trade create --portfolio-code PORT001 --product-code 000051.OF --market CN_OTC \
  --type buy --actual-amount 50000 --fee 0 --price 1.5 --trade-date 2025-01-07

# 8. 确认交易
ir trade confirm 1

# 9. 生成快照
ir snapshot generate --portfolio-code PORT001 --target-date 2025-01-07

# 10. 查看组合净值和收益率
ir portfolio nav-history PORT001
ir portfolio returns PORT001
```

### 5.2 分红处理

```bash
# 1. 创建现金分红事件
ir share-event create --portfolio-code PORT001 --product-code 000051.OF --market CN_OTC \
  --event-type cash_dividend \
  --ex-date 2025-06-15 --entitlement-date 2025-06-13 \
  --div-cash 500 --entitlement-shares 33333.33

# 2. 生成权益登记日的快照（如果还没有）
ir snapshot generate --portfolio-code PORT001 --target-date 2025-06-13

# 3. 确认分红事件
ir share-event confirm 1
```

### 5.3 日常运维

```bash
# 同步交易日历
ir system calendar-sync --year 2025

# 手动执行净值同步任务
ir task run nav_sync

# 生成今日快照
ir snapshot generate --portfolio-code PORT001 --target-date $(date +%Y-%m-%d)

# 查看数据源配置
ir system datasources
```

## 6. AI Agent 集成指南

### 6.1 调用方式

AI agent 可通过 shell 命令直接调用，解析 JSON 输出获取结果：

```python
import subprocess, json

result = subprocess.run(
    ["ir", "portfolio", "list", "--status", "active"],
    capture_output=True, text=True
)
data = json.loads(result.stdout)
if data["ok"]:
    portfolios = data["data"]
```

> 诊断信息输出至 stderr，脚本/Agent 解析请只读取 stdout。

### 6.2 错误处理

通过 exit code 和 JSON 中的 `ok` 字段判断是否成功：

```bash
output=$(ir portfolio get PORT999 2>/dev/null)
exit_code=$?
if [ $exit_code -ne 0 ]; then
    error_code=$(echo "$output" | jq -r '.error.code')
    # 处理错误
fi
```

**退出码：** 0=成功 / 1=业务错误(可换参重试) / 2=认证错误(需 `ir auth login`) / 3=连接/超时

### 6.3 链式操作

利用 `jq` 提取数据进行链式操作：

```bash
# 获取所有活跃组合的代码
ir portfolio list --status active --all 2>/dev/null | jq -r '.data[].code'

# 遍历每个活跃组合生成快照
for code in $(ir portfolio list --status active --all 2>/dev/null | jq -r '.data[].code'); do
    ir snapshot generate --portfolio-code "$code" --target-date 2025-01-06
done
```

### 6.4 使用 `ir schema` 快速了解全部指令

```bash
# 先拿极简索引
ir schema --index

# 按需加载某个命令组的完整结构
ir schema trade

# 一次性获取全部（输出较大）
ir schema
```

## 7. 注意事项

1. **交易日历依赖**：申购、赎回、交易、份额变动事件的日期必须是交易日。请先确保已通过 `ir system calendar-sync` 同步了当年的交易日历。

2. **净值数据依赖**：交易确认和快照生成依赖 `PriceRecord` 中的净值数据。请先通过 `ir market sync` 或 `ir market sync-history` 同步产品净值。

3. **快照顺序依赖**：快照按日逐日生成，某日快照依赖前一天的快照数据。区间重算（`ir snapshot recalculate`）可修复断裂的快照链。

4. **首次申购特殊规则**：组合的首次申购确认时净值固定为 `1.0000`，同时自动将组合状态从 `draft` 切换为 `active`。

5. **净值延迟披露产品**（issue #228）：场外 QDII、香港互认基金的净值晚一个交易日披露，需将产品 `nav_lag_days` 设为 `1`，快照即按前一交易日净值估值（`ir product update <CODE> <MARKET> --nav-lag-days 1`）。确认间隔由落库的 `confirm_days` 决定（场外 QDII 通常 T+2），确认时仍取交易日当天净值，须确保该日净值已同步；`is_qdii` 仅为展示标签。

6. **数据精度**：内部使用 Python `Decimal` 进行运算，输出时金额保留 2 位小数，份额统一 2 位小数，净值保留 4 位小数。

7. **认证与 token**：登录后 token 存储在 `~/.ir/token.json`（权限 0600），过期前 24 小时会在 stderr 打印警告。token 过期后需重新 `ir auth login`。修改密码后旧 token 会失效。
