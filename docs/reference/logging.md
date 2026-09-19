# InvestRing 日志规范

> 「健全日志系统」总纲 issue #403 的规范正文：跨端用法、事务边界与必要实现约束统一在此维护，模块指南只保留摘要和入口。改日志相关代码前先读对应章节；文档维护遵循 [AI 文档规范](documentation.md)。

| 子项 | 范围 | 落地位置 |
| --- | --- | --- |
| A #404 | 运行日志基建（stdout 单行 JSON + request_id 贯通） | `backend/app/logging_config.py`、`context.py`、`request_context.py` |
| B #405 | 审计与系统错误日志落地（写入点 + 迁移 0013） | `backend/app/services/audit_service.py` |
| C #406 | 调度可观测性（任务执行记录 + 自动运行落库） | `backend/app/services/task_runner.py::run_task` |
| D #407 | 前端日志基建（logger + 错误边界 + console 护栏） | `frontend/src/lib/logger.ts`、`frontend/src/app/{error,global-error}.tsx` |

---

<a id="logging-runtime"></a>
## 1. 后端运行日志

**形态**：只写 stdout、单行 JSON；**不落文件**——轮转交给 docker `json-file` driver
（生产与 dev 均为 `max-size=10m` / `max-file=3`）。配置入口
`logging_config.setup_logging()`，`main.py` 模块级调用、**幂等**（多入口重复配置不得
一行变两行）。

**固定字段**：`timestamp` / `level` / `logger` / `message`；`extra=` 传入的其他键原样进
JSON；异常折进 `exception` 字段（**不拼进 `message`**，否则换行会撕开单行 JSON）。

**级别口径**：`LOG_LEVEL` 优先，为空则由 `debug` 推导（True→DEBUG / False→INFO）。
选级别的唯一判据是「这条日志在生产默认级别下要不要出现」：

| 级别 | 用在 | 反例 |
| --- | --- | --- |
| DEBUG | 单次请求/单条记录的中间态、SQL 详略 | 任务失败（那是 ERROR） |
| INFO | 生命周期节点：启动、任务完成一句摘要、登录成功 | 循环体内逐条（会淹没 stdout） |
| WARNING | 可自愈的异常路径、以及一次**业务拒绝**（见下条） | 未预期异常（那要带堆栈记 ERROR） |
| ERROR | 需要人工介入：未预期异常、审计写失败、任务失败 | 用户输入非法 |

**业务拒绝（`BusinessError`）记 WARNING**——`main.py` 的全局 handler 是单点，带
`code` / `reason` / `method` / `path` / `status_code` 五个 `extra` 键。这条**刻意不加堆栈**
（拒绝是预期内结果，不是崩溃），量级由 code 维度控制：若某个 code 刷屏，说明前端校验
漏了而不是该降级日志。**业务代码不要自行再记一条**——handler 已覆盖所有 REST 入口，
重复记录会让同一拒绝出现两行、`request_id` 排查时误导。

未预期异常由同一个 `main.py` 的 `Exception` handler 记 ERROR（**带堆栈**），并同时落一条
`system_error_log`；`request_path` 在写入侧按列宽截断，完整 path 只在 stdout 行里。

**采集侧须容忍非 JSON 行**：`--workers 2`（生产）与 `--reload`（dev）下 uvicorn **父进程
不 import 应用**，每次启停有 2~4 行明文。解析用 `jq -R 'fromjson? // empty'`。

**关联一次请求**：`RequestContextMiddleware` 给每请求分配 `request_id`（沿用入站
`X-Request-ID`，否则 uuid4 hex），回写同名响应头，并在 JSON 里带 `request_id`；actor /
client_ip 由 `get_current_user` 在认证成功后写入。排查时先按 `request_id` 串同一请求的
多条日志。**只记 path 不记 query**（查询串可能带凭据），`/health` 跳过。

**程序化启动必须传 `log_config=None`**（#417，uvicorn 会无条件重装 handler 并设 propagate=False，覆盖 import 应用时的 JSON 配置）；命令行形态（生产 Dockerfile CMD）先配日志、后 import 应用，无需处理。不用 `--log-config` 收编父进程，避免应用内与命令行双源。

### 初始化与上下文边界

- `main.py` 中 `setup_logging()` 必须早于建表与调度初始化的 import 期副作用，但晚于 engine 创建。SQL 详略只由 `echo=settings.debug` 控制；dictConfig 不钉 `sqlalchemy.engine` / `sqlalchemy.engine.Engine` 级别，**显式清掉 echo 自挂的明文 handler**，否则 SQL 双写、多行 DDL 撕行。`uvicorn.access` 关闭（中间件单点记录），uvicorn/error 冒泡 root。
- `extra=` 禁用 LogRecord 保留键 `message`，否则 KeyError。每条访问记录带 method/path/status_code/duration_ms；入站 request_id 先过正则白名单，防响应头注入。
- 上下文必须是**中间件派发前创建的可变对象 + 依赖改字段**，不能在同步依赖里 `var.set()`：FastAPI 将依赖与 endpoint 分别交 threadpool，anyio 每次 copy_context，依赖那份 set 不会传到 service。
- Starlette 的 Exception handler 在最外层 ServerErrorMiddleware；调用时中间件 finally 已解绑上下文，故 request_id/actor/client_ip 从 scope 的 `SCOPE_REQUEST_ID_KEY` / `SCOPE_ACTOR_KEY` / `SCOPE_CLIENT_IP_KEY` 取回（认证成功时暂存）。响应经原始 send，handler 自补 X-Request-ID；中间件没见 response.start，访问日志按 500 记。handler 后仍重抛，测试用 `TestClient(app, raise_server_exceptions=False)`。
- 异常落库经 `run_in_threadpool`，不阻塞 async handler；request_params 刻意不记。stdout 与传给 record_system_error 的 path 均保持完整，只有唯一写入侧按列宽截断（#422c）。

---

<a id="logging-audit"></a>
## 2. 审计日志（`audit_log`）与系统错误日志（`system_error_log`）

两者都只经 `audit_service.record_audit` / `record_system_error` 写入——**不要直接
`db.add(AuditLog(...))`**，否则拿不到 savepoint 隔离与失败响亮（写不进去时会记 stdout
ERROR 并落一条 `system_error_log`，绝不静默）。

**audit_log 的必埋范围**（高风险子集，非全量）：申赎、调仓、跨平台现金转移、份额变动
事件、快照 generate / recalculate / delete（含级联回退）、现金手动重估。产品 / 平台 /
投资人 / 组合 CRUD 与任务手动触发**不埋**（YAGNI，需要时另开 issue）。

**action / resource_type 取值**以 `app/constants/audit_actions.py` 为单一事实来源，埋点
禁止写字面量；列宽是硬约束（`action` 20 / `resource_type` 50 / `investor_code` 20），
由 `tests/unit/test_audit_service.py` 守门。

**actor 口径**：取请求上下文，**无请求上下文时落 `SYSTEM` 哨兵**——`investor_code` 是
NOT NULL 列，缺哨兵则后台路径（调度器、线程池）整条写不进去。

**载荷口径**：diff-only（无变化则两侧皆 None，原样重提交不留痕）、`ensure_ascii=False`、
数值统一转 Decimal 再比（避免 `Decimal("1234.5600") != 1234.56` 恒真的假变更）。
副作用一律不另开记录。

读侧：`GET /api/system/logs/audit`、`/error`，CLI `ir log audit` / `ir log error`。

### 事务与失败隔离

- 埋点在 service，REST/CLI 共用。`record_audit` 不 commit，审计随业务事务提交或回滚。写入使用 **Core INSERT + 连接级 savepoint**（`db.connection().begin_nested()`），不用 ORM add/flush：ORM flush 失败会把根因挂到 `SessionTransaction._rollback_exception`、令 session DEACTIVE，连接级回滚无法修复 ORM 状态（#419）。Core execute 失败不留该状态，连接级足够。
- 不把审计换成 session 级 savepoint；需要 ORM 状态复位的是失败后继续循环的 auto_confirm。`sync_transfer_group` / `_confirm_fund_level_event` 失败即 rollback/raise、不承诺继续，同样保留连接级。auto_confirm 的显式 session savepoint 与禁止 with 的原因见[后端事务说明](../../backend/AGENTS.md#backend-auto-confirm)。
- savepoint 前先 flush 调用方业务改动到外层，否则 ROLLBACK TO SAVEPOINT 会一并撤销它们而调用方收到成功；**flush 和建 savepoint 都在 try 内**，`sp = None` 哨兵（#422a），否则重算收尾埋点可能把承诺的 `200 + results[].errors` 变成 500。
- 审计失败不外抛：回滚 savepoint、stdout ERROR，再用独立 SessionLocal 写 `error_type=AuditWriteFailure` 的 system_error_log；后者自身 best-effort，失败只记 stdout。守护覆盖 flush、建 savepoint、Core INSERT、回滚四段（#422）；回滚失败另记带 exc_info 的 ERROR，文案须按结果分支，不能宣称业务事务不受影响。
- request_path 在唯一写入路径按 `SYSTEM_ERROR_PATH_MAX`（绑定模型列宽 200）截断，完整值保留在 stdout，避免 MySQL 严格模式超宽丢整条记录。自由文本的 4 字节字符由全库 utf8mb4 解决（#427/#433），不采用写入侧转义清单；迁移兼容约束见[后端数据模型说明](../../backend/AGENTS.md#backend-models)。

### 载荷与去重边界

- `snapshot_recalc_job` 跨线程只传 request_id、不传 actor（共用线程池不复制 contextvars），后台审计恒为 SYSTEM，stdout 仍能关联请求。
- JSON 用 `default=str` 保存 Decimal/date，create 无 old_value、delete 无 new_value；副作用仅申赎确认的 cash_transfer_group 与交易创建的 transfer_group 折进 new_value，组合激活及确认配对腿镜像不另入载荷。
- 快照 generate 数值载荷用保标度字符串（#421）：ORM 构造点保留量化后的 Decimal（估值 4 位、份额 2 位），不转 float；同函数 API 返回 dict 的 float **刻意保留**，那是 JSON 数字契约。投资人成本价构造点同样保留 Decimal。
- 级联委派给既有 unconfirm 实现时只由被委派方留痕；事件级联就地改字段才记 cascade_unconfirm。不为 generate 前的空删除记 delete；申赎/交易/事件 update 仅 diff 非空才记审计，原样提交不产生两侧皆 NULL 的空行。

---

<a id="logging-tasks"></a>
## 3. 任务执行记录（`task_execution_log`）

**唯一编排点**是 `task_runner.run_task(db, task_code, trigger_type)`：手动触发
（`POST /api/system/tasks/{code}/run`）与调度触发（`scheduler_service`）共用它，
两条路径不会漂移。读侧 `GET /api/system/tasks/executions`、CLI `ir task logs <code>`。

**触发来源**（`trigger_type`，前端执行历史「触发方式」列取它）：

| 取值 | 来源 |
| --- | --- |
| `manual` | 手动触发（`routers/tasks.py`） |
| `scheduled` | 调度器自动运行（`scheduler_service`） |

**跳过不是执行**：GET_LOCK 被别的进程持有、当日非交易日这两类跳过**不建记录**——
否则每个非交易日为每条 job 各积一行无信息量的「未执行」噪音。

**字段口径**：

| 字段 | 口径 |
| --- | --- |
| `duration_ms` | `finished_at − started_at`（毫秒） |
| `records_total` / `success` / `failed` | 按 task_code 归一，见下 |
| `error_message` | 失败摘要（截断 1000 字符）；`snapshot_generate` 的 #305 告警文案也走它 |
| `error_stack` | 异常路径的 traceback |

`records_*` 逐任务：`nav_sync` = 产品数 / 差额 / 失败产品数；`snapshot_generate` =
处理组合数 / 差额 / `auto_confirm_failed` 条数；`trading_calendar_sync`、`log_cleanup`
只填 total 与 success。

> **`records_failed` 为 NULL 表示「未度量」，不是「零失败」**。读侧（含前端与 CLI）不得
> 把 NULL 折算成 0——本表的初衷就是消灭「恒 null 的摆设列」，那要求空值能表达「不知道」、
> 有值必须是真的。

### 执行与事务边界

- run_task 先建 running 行并 commit，再经 `_TASK_DISPATCH` 执行，按 `_derive_log_fields` 落终态并 commit。复用调用方 session、不 close；异常路径不 rollback（running 已落库），任务未提交业务由自身编排收口：run_nav_sync 逐产品 checkpoint、`_generate_snapshots_for_date` 逐日 rollback/commit。
- trigger_type 使用 `TRIGGER_MANUAL` / `TRIGGER_SCHEDULED` 常量，不写字面量；跳过判定统一在 `scheduler_service._should_run_today`，两条 job 共用。
- 归一的输入分别为 products_count/failed_products、portfolios_processed/auto_confirm_failed、synced_count、删除行数求和。#305 的 warnings / auto_confirm_failed 归并及 1000 字符摘要不改变。
- 任务异常写 failed、str(e) 摘要与 traceback 后**原样上抛**，HTTP 映射留给 router/global handler；未知 task_code 抛 NotFoundError，不建执行行。
- `run_nav_sync(db, log_id)` 的 log_id 必填，逐产品 NavSyncDetail 要挂父行；`run_snapshot_generate(db)` 没有逐日明细表，不添加无消费者的 log_id。

---

<a id="logging-frontend"></a>
## 4. 前端日志

**统一入口 `@/lib/logger`**，业务代码**禁止直调 `console.*`**：

```ts
import { createLogger } from "@/lib/logger";

const log = createLogger("tradePairs");   // 模块 tag 进统一前缀

log.debug("结对数不匹配", { buy, sell }); // 生产构建下自动 no-op
log.error("提交失败", err);                // Error 的 message + stack 都保留
```

**输出前缀**统一为 `[InvestRing][<tag>][<LEVEL>]`，四级逐级对应
`console.debug/info/warn/error`。**`debug` 在生产构建下不产生输出**（判定用
`process.env.NODE_ENV === "production"`，Next 构建期静态替换，不需要构建插件）。

**meta 处理**：字符串原样拼接、对象走 `JSON.stringify`、**`Error` 单独取 message + stack**
（`JSON.stringify(new Error("x"))` 得到 `{}`，直接序列化等于丢堆栈）；不可序列化值
（循环引用等）退化为 `String()`，绝不因日志本身抛错。

**级别口径**与后端一致（第 1 节的表）：用户可见的操作失败记 `error`，可恢复的降级记
`warn`，排查用的中间态记 `debug`。

**渲染期异常**由 App Router 约定文件兜底：`src/app/error.tsx`（根）、
`src/app/m/error.tsx`（移动端）、`src/app/global-error.tsx`（根 layout 自身抛错时，
自带 `<html>`/`<body>` 与 `globals.css`）。三者共用
`src/components/shared/ErrorFallback.tsx`，异常经 logger 留痕并带 Next 的 `error.digest`
（它在浏览器与 Next 服务端日志里同时出现，是两条记录之间唯一的关联键）。
**刻意不自造 `<ErrorBoundary>` 组件**——Next 的约定文件即边界，再包一层不增加覆盖。

**客户端错误上报明确不做**（总纲 #403 决策 3）：避免改动 openapi 契约与引入上报端点的
滥用/限流问题。需要时另开 issue。

**护栏**：`frontend/eslint.config.mjs` 在 `files: ["src/**"]` 上禁裸 `console.*`
（`no-console: error`）；作用域限定是因为 `frontend/scripts/visual-shot.mjs` 有 8 处
脚本文本的 `console.*`。唯一豁免是 `src/lib/logger.ts` 与其单测（前者是唯一封装层、
后者需对 console 做 spy）——豁免在 `docs/design/visual-spec.md` §1.5 登记。

---

## 5. 运维：日志落在哪、怎么查

| 环境 | 去向 | 轮转 |
| --- | --- | --- |
| 生产（`docker-compose.yml`） | backend / frontend / nginx 三服务的 docker stdout | `json-file` 10m × 3 |
| dev（`docker-compose.dev.yml`） | 三服务（含 mysql）的 docker stdout | `json-file` 10m × 3（与生产同值） |
| 本地 override（`docker-compose.override.yml`） | backend 容器 stdout | `json-file` 10m × 3 |

`nginx/nginx.conf` 的 `error_log … warn` 与 `access_log` 走镜像 stdout 符号链接，同样由
docker driver 轮转（未挂载 `/var/log/nginx`）。

```bash
# 只看某个组合相关的 JSON 行（容忍 uvicorn 父进程的明文行）
docker compose logs --since 1h backend | jq -R 'fromjson? // empty' | jq 'select(.request_id=="<id>")'

# 任务执行历史（REST）
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/system/tasks/executions?task_code=nav_sync" | jq
```

**应用侧不写日志文件**：`logs/` 与 `*.log` 已在 `.gitignore` 中忽略，仓库内不该出现日志残留；
需要落盘时由 docker driver 决定，不要在应用里再开一套文件 handler。
