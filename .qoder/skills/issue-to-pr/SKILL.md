---
name: issue-to-pr
description: 从 GitHub issue 评估出发，经计划、执行、测试到提交 PR 并合入的完整开发流程。当用户给出一个或多个 issue 号要求"做掉/实现/修复"，或说"走 issue-to-pr 流程"、"从 issue X 开始做到合入"时使用。流程含两个人工关卡（计划批准、合并确认），并按任务难度为子 Agent 分层指派模型。
---

# issue-to-pr：从 issue 到 PR 合入的流水线

主 Agent 职责：**评估、规划、调度子 Agent、验收、把关**。编码与重活派给子 Agent。
全程遵守仓库 AGENTS.md §8：GitHub Flow 单分支模型、Conventional Commits、PR 模板、CI 全绿才合入、文档随代码同一次提交、AI 不直接 push main。

## 阶段总览

| # | 阶段 | 执行者 | 关卡 |
|---|------|--------|------|
| 1 | 评估 issue | 主 Agent（+Explore 扇出） | — |
| 2 | 制定计划 | 主 Agent（+Plan 子 Agent） | **关卡 1：用户批准计划** |
| 3 | 执行改动 | 编码子 Agent | — |
| 4 | 测试验证 | 测试子 Agent + 主 Agent 验收 | — |
| 5 | 提交 PR + review | 主 Agent（+open-code-review） | CI OK |
| 6 | 合并 | 主 Agent | **关卡 2：用户确认合并** |

---

## 阶段 1：评估 issue

1. `gh issue view <N>`（含 `--comments`）读取 issue 正文与讨论。
2. 主 Agent 做定向核查：Grep/Read 验证 issue 描述与代码现状是否一致，找出**盲区**（issue 没覆盖的依赖、副作用、约定冲突）。
3. 探索量大时派 Explore 子 Agent 扇出（`subagent_type=Explore`，明确搜索广度），主 Agent 只收结论。
4. 发现的盲区/澄清点：经用户确认后以 issue 评论补录（可派子 Agent 用 `gh issue comment` 发，属事务性操作）。
5. 产出：一段评估结论——改动范围、影响的文件/模块、是否需拆多个 PR、是否涉及 DB 迁移或业务规则（若是，确认 issue 已按 §8.2 模板写全）。

## 阶段 2：制定计划（关卡 1）

1. 复杂任务派 Plan 子 Agent（`subagent_type=Plan`）产出实施方案；主 Agent 审核其与仓库约定的一致性（分支模型、迁移可逆性、E2E 优雅 skip 约定、AGENTS.md 可发现性过滤）。
2. 进入 Plan 模式（`EnterPlanMode`），把计划写入 plan 文件，经 `ExitPlanMode` 交用户批准。
3. **未获批准不得进入阶段 3。** 计划须明确：PR 拆分与依赖顺序、每个 PR 的验收断言、测试策略。

## 阶段 3：执行改动

- 从最新 `origin/main` 拉分支：`feature/<issue号>-<简述>`（多 issue 拆 PR 时各拉各的）。
- **按耦合度分配子 Agent**：
  - 互相独立的 PR → 并行派编码子 Agent，各用 `isolation: "worktree"` 隔离，避免工作区冲突。
  - 有依赖的 PR（如 B 引用 A 的新文件）→ 串行，等上游合入 main 后从最新 main 重新拉分支再继续。
- 给子 Agent 的 prompt 必须自包含：文件路径、改动点、约束（不得引入未要求依赖、文档同提交更新、不 push main）。
- 子 Agent 返回后，主 Agent 读 diff 验收：改动是否在计划范围内、是否遵守 §8.5 铁律。

## 阶段 4：测试验证

1. 派测试子 Agent（后台运行）执行对应门禁，只回传消化后的摘要而非原始日志：
   - 后端：pytest（见 `backend/AGENTS.md` 的测试命令与 TEST_DB_URL 约定）；涉及迁移时补 MySQL 方言/迁移链验证。
   - 前端：lint + tsc + build；涉及页面行为时跑 Playwright E2E。
   - ir-cli 契约：改动波及 API 时重跑 `gen_response_fields.py` 一致性校验。
2. 主 Agent 验收摘要：全绿才可进入阶段 5；失败则把失败信息交回编码子 Agent 修复，循环直至绿。
3. 本地无法验证的改动，明确向用户说明，不得谎报已验证。

## 阶段 5：提交 PR + review

1. 主 Agent 创建 PR（`gh pr create`），正文按模板四段：改动内容 / 关联 issue（`fixes #N`）/ 测试验证 / 部署影响（DB 迁移、新依赖、回滚要点）。
2. 提交信息 Conventional Commits + issue 号（如 `fix(snapshot): ... (#96)`）。
3. 轮询 CI 直至 required check `CI OK` 绿；失败则回阶段 3/4 修复。
4. CI 绿后用 `open-code-review` skill（`ocr` CLI）对 PR diff 做一轮 AI 审查，主 Agent 判断并落实有效意见。

## 阶段 6：合并（关卡 2）

1. 向用户报告：PR 链接、CI 状态、review 结论、部署影响要点。
2. **用户确认后**才 `gh pr merge --squash`（分支由仓库 delete_branch_on_merge 自动删除，勿手动带 `--delete-branch`）。
3. 多 PR 按依赖顺序逐个合并：合一个 → 等 main 更新 → 下一个 PR rebase 最新 origin/main 再合。
4. 合入后冒烟：health check + `ir portfolio list` 类关键路径抽查（视改动范围）。

---

## 分层模型策略

按任务难度给子 Agent 指派不同能力/成本档的模型。可用档位：`inherit` / `auto` / `lite` / `efficient` / `performance` / `ultimate`（成本大致递增）。

| 任务类型 | 典型子 Agent | 指定档位 | 理由 |
|----------|--------------|----------|------|
| 代码定位/探索扇出 | Explore | `efficient` | 检索型任务，无需强推理 |
| 事务性操作（发 issue 评论、查 CI 状态） | general-purpose | `lite` | 步骤固定，低风险，最省成本 |
| 测试执行 + 日志摘要 | general-purpose | `efficient` | 跑命令 + 归纳，中等难度 |
| 编码实现 | 编码子 Agent（worktree 隔离） | `performance` | 需理解业务不变量，质量优先 |
| 方案/计划设计 | Plan | `performance` / `ultimate` | 架构权衡，错误代价高 |
| PR 代码审查 | `open-code-review` skill（ocr CLI） | —（外部工具） | 专用审查工具，不占子 Agent 模型预算 |
| DB 迁移 / 涉快照核心链路改动 | 编码子 Agent | `ultimate` | 不可逆操作（迁移 0006/0008 前科），最高档兜底 |

**指派方式**（优先级从高到低）：

1. 调用时显式传参：Agent 工具的 `model` 参数（覆盖一切）；
2. 自定义 agent 定义 frontmatter 的 `model:` 字段；
3. settings.json `agents.overrides` / 环境变量 `QODER_SUBAGENT_MODEL`（改动后 `/agents reload`）。

本 skill 一律用**方式 1**（调用时传参），保持灵活；档位冲突或用户另有指定时以用户为准。

## 边界与例外

- issue 描述不清、验收断言缺失 → 先补齐 issue（评论或编辑），不进入阶段 2。
- 排查/审查中顺带发现的问题：只提新 issue，不在本流程里夹带修复（§8.5 铁律 2）。
- 用户可随时叫停任一阶段；关卡未过不得跳步。
