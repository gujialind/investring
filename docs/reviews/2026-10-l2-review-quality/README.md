# L2 评审质量复盘存档 — 2026-10 批次

> 批次存档，不承载业务规则正文（载体定位见[文档职责表](../../reference/documentation.md#doc-ownership)）。仍有效的条款已回写[审查规范](../../reference/code-review.md)，本目录只保留度量口径、明细数据与判据，供下一批复盘对照。

- 关联 issue：#722（提升 AI 代码评审质量：发布前过滤、度量闭环、风险分层）
- 复盘日期：2026-10-19；基线标注日期：2026-10-16
- 关联 PR（本轮纳入，均已合入 main）：720、721、723、726、727、728、735、737
- 基线纳入 PR：706–712、716、717

## 索引

| 文件 | 内容 |
| --- | --- |
| [report.md](report.md) | 本轮主报告：条数核对、质量分类、独立核验覆盖率、与基线对比、模式聚类 P1–P8、规范条款草案 R1–R6、局限与待确认 |
| [findings.jsonl](findings.jsonl) | 本轮 58 条发现明细（每行一条 JSON：`finding_id`/`severity`/`verdict`/`verification`/`code_anchor`/`notes`） |
| [pilot.md](pilot.md) | 发布前过滤试点复盘：哪些 PR 真走了过滤、原稿 vs 发布稿逐条差异、误报拦截与耗时口径 |
| [missed.md](missed.md) | 漏报复盘（recall 侧）：各 PR 评审 head → 合入 sha、合入后 72h 内触及同文件的修复、M1–M4 归因 |
| [baseline-report.md](baseline-report.md) | 首轮基线报告（9 PR / 38 条，chain-only 核验） |
| [baseline-findings.jsonl](baseline-findings.jsonl) | 基线 38 条明细 |

## 口径

- **纳入对象**：首轮 L2 评审的带级别发现（`[Blocker]`/`[Suggestion]`/`[Nit]`）+ 复审/第二轮**新增**的带级别发现。同一发现多轮引用只算一条，以最早出现的评论为准；行号/措辞更新不算新发现；无级别条目与纯状态评论不纳入，逐项排除理由见 report.md 第 1 节差异列。
- **分母**：本轮 N = 58，分类分母 = A+B1+B2+C = 58（D 单列、不计入分母，本轮 D = 0）；基线 38 条 / 分母 37（D = 1 单列，与本轮同口径排除）。
- **指标**：错误率 = C/分母；采纳率 = A/分母；真问题率 = (A+B1+B2)/分母。分类定义 A/B1/B2/C/D 见 report.md 第 2 节，与基线同源。
- **核验强度**：本轮 57/58 为 `code_verified`（在评审当时的 head sha 用 `git show` 读原文）、`728-12` 为 `metadata_verified`（其前提是 PR 正文声明的行数，正文不在 commit tree 内，只能凭归档时保留的 `pr_meta.json` 快照核验）；基线为 chain-only。二者对同一现象的观测口径不同，report.md 第 4.3 节已按分层计算排除「PR 类型构成」这一候选解释。
- **计数复算**：全部计数由脚本对 findings.jsonl 复算，非手工加总。

## 归档边界

- 原会话的输入材料（PR 评论链 `data/`、评审原稿 `drafts/`、只读克隆 `repo/`）与一次性取数脚本**未归档**；正文中出现的 `out/…`、`data/…`、`drafts/…`、`/tmp/rt/…` 路径按原会话目录解读，本目录内文件已平铺（去掉 `out/` 前缀）。
- 除 `728-12`（前提是 PR 正文数字，标 `metadata_verified`、凭归档保留的 `pr_meta.json` 快照核验）外，其余 `code_anchor` 与 commit sha 仍可在本仓库用 `git show <sha>:<path>` 复现；只读 git 无法复现的运行期数字（pytest 计数、E2E/visual 实跑、px 探针）一律按「评论链自述」处理，见 report.md §3.3。

## 条款回写情况

report.md §6.1 给出 R1–R6 的「条款原文 → 拟改文本 → 依据 → 代价」。本批已回写 4 条，均落在[审查规范](../../reference/code-review.md)既有编号内，未新增并列清单：

| 草案 | 落点 | 内容 |
| --- | --- | --- |
| R1 | [§6.1](../../reference/code-review.md#review-output) | 压缩发布只允许合并版式、不得删除原有六项（级别、位置、触发条件、证据、影响、建议）；未成立主张单列「已撤回主张」区（主张/撤回理由/证据）；整轮不发布须留痕 |
| R2 | [§6.1](../../reference/code-review.md#review-output) | 位置核验作为发布前置闸，并区分「锚点错但前提成立」与「前提不成立」两种处置 |
| R4 | [§6.2](../../reference/code-review.md#review-recheck) | 以「上次通过评审 head → 合入 head 树差异非空」为新增范围判据，须逐处核对双方意图；`git show -c` 仅作辅助，combined diff 为空不能推断无编辑 |
| R6 | [§4 引言](../../reference/code-review.md#review-risks) | L2 算力优先给 §4.1/§4.4，压缩发布不得削减其 `触发与证据` |

未回写 2 条：R3（跨 PR 文档收口义务，report.md 建议落 §6.3）与 R5（处置链的回归与留痕自证）本轮不做，草案与依据仍留在 report.md §6.1 供后续拍板。report.md §6.2–§6.4 的流程建议（关口位置、执行者、必核级别与抽检比例）、度量自动化脚本设计与试点固化结论本轮均未落成条款或工具，仅存档。
