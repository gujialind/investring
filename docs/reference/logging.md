# InvestRing 日志规范

> 「健全日志系统」总纲 issue #403 的规范正文。四个子项各自的实现细节在模块指南里
> （`backend/AGENTS.md` §1.3/§1.5、`frontend/AGENTS.md`），本文只写**跨端约定与用法**，
> 不重复实现说明。改日志相关代码前先读本文。

| 子项 | 范围 | 落地位置 |
| --- | --- | --- |
| A #404 | 运行日志基建（stdout 单行 JSON + request_id 贯通） | `backend/app/logging_config.py`、`context.py`、`request_context.py` |
| B #405 | 审计与系统错误日志落地（写入点 + 迁移 0013） | `backend/app/services/audit_service.py` |
| C #406 | 调度可观测性（任务执行记录 + 自动运行落库） | `backend/app/services/task_runner.py::run_task` |
| D #407 | 前端日志基建（logger + 错误边界 + console 护栏） | `frontend/src/lib/logger.ts`、`frontend/src/app/{error,global-error}.tsx` |

---

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

**程序化启动必须传 `log_config=None`**（uvicorn 会无条件重装自己的明文日志配置）；
命令行形态（生产 Dockerfile CMD）顺序相反、无需处理。

---

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

---

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

---

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
