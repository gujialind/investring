## 改动内容

<!-- 本次改动做了什么，涉及哪些模块。 -->

## 关联 issue

<!-- 格式：fixes #N（合并后自动关闭对应 issue）。 -->

- fixes #

## 自审清单

<!-- 标准见 docs/reference/code-review.md。此处只列 CI（L1 机械门禁）结构上查不到的项——
     lint / tsc / build / 测试 / 契约漂移已由 CI 强制，勿在 PR 里复述。 -->

- [ ] 我读过自己的**完整 diff 原文**（不是只看 AI 生成的改动摘要）
- [ ] **失败路径**：新增/修改的每个失败分支都有可感知出口（抛领域异常 / 记日志 / 告警），无静默吞掉、静默降级、静默截断、静默覆盖入参
- [ ] **同类排查**：改动的同构位置（同模板的其它组件、同服务的其它分支、同一页面的另一端）已 grep/AST 确认，无漏网
- [ ] **数值口径**：涉及金额/份额/净值时，量化只在产生点、走统一入口、舍入模式与根 `AGENTS.md` §2.11 一致，无 `float()` 掉标度
- [ ] **契约同步**：AGENTS / runbook / visual-spec 已随代码更新（无守门的产物，不适用请注明；错误码清单由 CI 守门，勿在此复述）
- [ ] **测试覆盖未蒸发**：新增的 `skip` 属真条件性并注明条件，未为变绿而放宽或删除断言

## 测试验证

<!-- 勾选已执行的验证项，并附关键结果。合入 main 前 CI 必须全绿。 -->

- [ ] 本地 pytest（backend，影响面圈定见 `backend/AGENTS.md` §2）
- [ ] MySQL 迁移链检查（CI backend-test-mysql）
- [ ] ir-cli 契约检查（CI cli-contract-check）
- [ ] 前端 lint / build（`scripts/verify-frontend.sh`，CI frontend-check）
- [ ] 前端 E2E（CI frontend-e2e）
- [ ] **改动表格/图表列结构时**：已跑 `scripts/visual-verify.sh` 目检并附截图（CI 拦不住列宽挤压与 CJK 竖排，见 #355；不适用请注明）

## 部署影响

<!-- 有无 DB 迁移 / 新依赖 / 配置变更；如有，写明回滚要点。 -->

## 合入后冒烟

<!-- 合入并 CD 部署完成后执行，勿在合入前勾选（见 docs/reference/code-review.md §4.6）。 -->

- [ ] health check + `ir portfolio list` + 关键数据抽查

## 审查记录

<!-- 审查者填写。结论格式见 docs/reference/code-review.md §6。 -->

- 结论：
- 🔴 Blocker / 🟡 Suggestion / 💭 Nit：
- follow-up issue（标题带「PR #X 评审 follow-up」）：
- 做得好的地方：
