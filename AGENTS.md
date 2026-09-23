# InvestRing 开发指南 (AGENTS.md)

> AI 编程助手的项目入口：项目地图、任务路由、跨域不变量摘要与动作边界。完整业务规则只在专题文档维护；源码说明当前实现，文档说明业务意图，冲突时先指出差异并询问，不默默选边。

## 1. 项目概览

**InvestRing** 是供个人和家庭使用的净值化投资组合记账系统，支持多投资人、公募基金（场内 ETF、场外 OEF/LOF、港互认）与现金。股票仅为分类维度（`asset_class_code="ASSET_STOCK"`），暂不支持个股持仓。所有资产人民币计价，无汇率换算。

| 目录 | 内容 |
| --- | --- |
| `backend/` | FastAPI + SQLAlchemy；应用、迁移与测试 |
| `frontend/` | Next.js App Router，桌面/移动双端 |
| `ir-cli/` | 独立轻量 HTTP CLI（typer + httpx） |
| `nginx/`、`scripts/`、`docker-compose*.yml` | 部署与运维 |

技术栈版本以各栈依赖文件为准；后端入口 [app/main.py](backend/app/main.py)，前端入口 `npm run dev`，CLI 入口与运行要求见 [ir-cli 指南](ir-cli/AGENTS.md)。

模块操作细节分别在 [backend/AGENTS.md](backend/AGENTS.md)、[frontend/AGENTS.md](frontend/AGENTS.md)、[ir-cli/AGENTS.md](ir-cli/AGENTS.md)。**编写或迁移文档先读 [AI 文档规范](docs/reference/documentation.md)**；其他工具只引用这套规范，不复制正文。

## 2. 按任务加载

先定位章节/符号，再读取必要正文；多类型任务取并集，不默认加载全部专题或完整 CLI 手册。

| 任务 | 默认加载 | 验证入口 |
| --- | --- | --- |
| 后端业务 | [后端指南](backend/AGENTS.md)、[对应领域规则](docs/reference/business-constraints.md)、目标服务及相关测试 | 后端指南“跑测试”的影响面表 |
| 前端交互 | [前端指南](frontend/AGENTS.md)、目标页面与 API 契约；涉及业务时读对应规则 | 前端指南的质量门禁与影响面 E2E |
| 前端视觉 | 前端交互的入口 + [visual-spec](docs/design/visual-spec.md) | 前端指南的双端目检 |
| CLI | [CLI 指南](ir-cli/AGENTS.md)、[手册对应命令组](ir-cli/CLI_MANUAL.md)（先 `ir schema --index`） | CLI 单测与响应字段契约检查 |
| 发布与运维 | [versioning](docs/reference/versioning.md)、目标 workflow、相关 runbook | 版本/契约验证及 runbook 的成功与恢复判据 |
| 日志与任务记录 | 模块指南 + [logging](docs/reference/logging.md) | 对应服务/任务日志测试 |
| 代码审查 | [code-review](docs/reference/code-review.md)、本次变更涉及的规则 | L2 语义审查，不重复 CI 的 L1 检查 |
| 文档变更 | [documentation](docs/reference/documentation.md)、持有正文的专题及其引用方 | `python scripts/check_context_docs.py` 与相关契约/测试 |

### 跨域不变量摘要

下列只作导航，完整定义、原因和例外以链接正文为准。

- [双层账本](docs/reference/business-constraints.md#rule-ledger)：投资人组合份额与内部产品持仓分账，只有申赎改变组合份额。
- [组合生命周期](docs/reference/business-constraints.md#rule-portfolio)与[投资人份额](docs/reference/business-constraints.md#rule-investor)：状态和可用量不能靠列表/快照字段猜测。
- [产品与市场](docs/reference/business-constraints.md#rule-product)：确认间隔与快照取价滞后是独立机制。
- [现金账本](docs/reference/business-constraints.md#rule-cash)：按平台分账，CASH 腿显式落账；在途计市值、不计可用现金。
- [快照](docs/reference/business-constraints.md#rule-snapshot)：严格交易日连续、严格取价；整体回滚与允许的单笔自动确认失败必须区分。
- [生命周期](docs/reference/business-constraints.md#rule-lifecycle)：确认、回退、取消与配对腿的操作边界由业务矩阵决定。
- [申赎](docs/reference/business-constraints.md#rule-subscription)、[调仓](docs/reference/business-constraints.md#rule-trade)、[份额事件](docs/reference/business-constraints.md#rule-event)：各自定价和生效日期不能互相套用。
- [数值口径](docs/reference/business-constraints.md#rule-precision)与[交易日](docs/reference/business-constraints.md#rule-trading-day)：使用统一量化入口及交易日历，不自造舍入/工作日判断。

### 高频实现与测试入口

| 主题 | 实现 | 代表性测试 |
| --- | --- | --- |
| 金额/份额/净值量化 | [quantize.py](backend/app/utils/quantize.py) 的三个 helper | [test_quantize.py](backend/tests/unit/test_quantize.py) |
| 交易日 | [trading_utils.py](backend/app/services/trading_utils.py) | [test_trading_day.py](backend/tests/integration/test_trading_day.py) |
| 快照事务 | [snapshot_service.py](backend/app/services/snapshot_service.py) 的 recalculate_snapshots | [test_snapshot_service.py](backend/tests/unit/test_snapshot_service.py) |

## 3. 开发流程约定

> 单人 + AI 协作。以下约束动作边界；代码是当前实现事实来源，不用推测替代读取。

### 3.1 分支模型（GitHub Flow，单长期分支，issue #211）

- `main` 是唯一长期分支与生产交付线，受 ruleset 保护：禁删除/强推、require PR、required check `CI OK`。
- 一切改动从最新 `origin/main` 拉短命分支，经 PR 合入 main；不从本地旧 ref 重建。命名用 `feature/<issue号>-<简述>`、`hotfix/<issue号>-<简述>`；按下方 Issue 约定免开 issue 的小修可用 `hotfix/<简述>`。AI 可用 `trae/`、`codex/` 前缀。
- 合入 main 触发 CI → CD 自动部署，**纯文档例外**（#456）：ci.yml 的 push 触发器忽略 `**/*.md` 与 `docs/**`，纯文档合入不重建镜像、不重部署。`deploy/YYYYMMDD-SHA` 标签因此不随每次文档合入推进，见版本规范。
- **PR 触发器禁止添加 paths-ignore**：docs-only PR 仍须产出 CI OK，否则 required check 永久等待（#377）。路径模式只按后缀/目录精确匹配，不能扩大到漏掉代码；合入前验证不减，完整执行策略见 ci.yml 与[审查规范的合入条件](docs/reference/code-review.md#review-merge)。
- 手动部署（deploy.yml workflow_dispatch）只接受已有镜像 tag，用于回滚/重部署。

### 3.2 Issue 约定

- 新功能、大改、涉及业务规则或 DB 迁移必须先提 issue；影响面大或需留痕的 bug 同样如此。需留痕的决策、延期缺陷或跨 PR 工作使用 issue；局部 bug 可直接修复、验证并在 PR 说明，不强制另开 issue（#608）。
- 按 `.github/ISSUE_TEMPLATE/` 的 bug_report / feature_request / chore 模板提交。
- 标题使用 `[bug]` / `[feat]` / `[chore]`（含文档和运维），与 Conventional Commits 对齐。

### 3.3 PR 约定

- 使用 [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md)。同目标、可共同验收的小改可合并，不要求一 issue 一 PR，不按行数或每日数量凑批。
- 审查标准、分级处置与规模护栏见 [code-review](docs/reference/code-review.md)。CI 已强制的 lint、类型、构建、测试和契约漂移属 L1，审查不重复。
- **改 PR base 不触发 CI**：默认 opened/synchronize/reopened 不包含 edited；retarget 后须 close/reopen 或推新 commit 触发，不能等待空 checks 自行补齐（#377）。

### 3.4 commit 信息

Conventional Commits：`fix:` / `feat:` / `docs:` / `refactor:` / `chore:`，简述目的并尽量带 issue 号，如 `fix(snapshot): 快照净值严格匹配 (#96)`。

<a id="ai-agent-rules"></a>
### 3.5 AI AGENT铁律

1. **改完必须验证，以证据交付**：按第 2 节任务入口及模块指南圈定影响面，本地相关检查与测试必须通过，拿不准宁宽勿窄；前端还须实际操作验证，按模块指南完成必要的双端目检。CI 兜底不替代本地验证。交付说明改动、验证结果和未覆盖项；验证不了不提交，不把未运行写成通过。
2. **开发负责闭环，排查/审查不越权修复**：已授权开发范围内的实现、自检和修复直接推进，本次改动引入的问题必须修复并回归验证；专项排查/审查及任务外发现先核实并报告证据、影响与建议，不顺手改代码。可选建议不自动派生任务，确需跟进时按 Issue 约定经授权登记。任务外问题若阻塞验收，先确认是否扩围；审查分级处置及机械修例外遵循[审查规范](docs/reference/code-review.md#review-disposition)，不得以 follow-up 绕过阻塞项。
3. **只做最小必要变更，保护已有工作**：不夹带无关重构，不引入未明确要求的依赖或表结构变更；获准的 DB 变更必须同步提供 Alembic 迁移。修改前检查工作区状态，不覆盖用户或其他 agent 的改动，不擅自清理其他 worktree 的进程或数据。
4. **文档按需同步，操作授权不推定**：按 [AI 文档规范](docs/reference/documentation.md#doc-sync)在同一次提交中同步受影响文档、引用与生成契约，无需同步时说明理由；决策留痕按 Issue 约定执行。提交、推送、合入、发布、其他远程写操作及生产或破坏性操作均须明确授权；授权只覆盖指定动作与范围，不自动延续到后续任务。
5. **边界内自主，关键歧义才询问**：先查代码、测试和项目约定；目标明确、局部可逆且不改变业务规则或公开契约的实现细节自行决定，交付时说明关键取舍。需求歧义、文档与实现冲突、数据安全或授权不清时必须询问，未澄清前不做相关决定；仅暂停受影响部分，可独立完成的工作继续推进。
6. **把建议说清楚**：先用白话说明会错什么、已有证据、是否需要现在处理，再推荐最小方案；可选项允许“不做”。术语须解释，不把未核实的担忧或可自行解决的技术细节交给维护者裁决。不理解不代表同意增加工作，详见[表达要求](docs/reference/code-review.md#review-output)。
7. **避免过度增加自动检查**：先核实已有检查和更简单的修法，说明新增检查能避免的具体错误及以后要维护什么；不只因“更稳健”就加层、建后续任务或扩大范围。保留必要的正确性、安全检查及验证检查有效的测试，详见[按需保护](docs/reference/code-review.md#review-guard-cost)。
