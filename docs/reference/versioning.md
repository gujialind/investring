# 版本号规范与发布流程（issue #375）

> 项目版本**单一事实来源 = 仓库根 `VERSION` 文件**。本文定义 InvestRing monorepo 的 Semver 规范与发布操作；方案推演与设计决策见 issue #375。

***

## 1. 版本方案

* 格式 `MAJOR.MINOR.PATCH`，git **附注标签** `vX.Y.Z`；镜像语义标签与 git 标签同形（`:vX.Y.Z`）。
* **0.x 语义**：`MAJOR=0` 为初始阶段，无对外兼容承诺，不兼容调整可走 MINOR（Semver 惯例）；升 `1.0.0` 的时机由 owner 显式决定。
* **不使用 pre-release 版本**（alpha/beta/rc）：main 即生产、单人使用，无预发布通道需求。

### 版本同步矩阵（一律由 `scripts/release.py` 维护，禁止手改）

| 位置 | 消费方 | 同步方式 |
| --- | --- | --- |
| `VERSION`（根） | 唯一事实来源 | release 脚本写入 |
| `backend/app/main.py` `FastAPI(version=…)` | `openapi.json` 的 `info.version`、API 文档 | 运行时 `_resolve_version()` 读根 VERSION（`APP_VERSION` 环境变量优先；镜像内 `/app/VERSION` 由 Dockerfile COPY） |
| `backend/openapi.json` | CI 契约门禁 `check_openapi.py`（全量比对含 `info.version`） | release 时用钉版 `.venv-openapi` 调用 `export_openapi.py --offline` 隔离重导出，**必须与版本变更同 commit** |
| `backend/pyproject.toml` | 无直接消费（不参与构建） | release 脚本替换 |
| `frontend/package.json` + `package-lock.json`（两处） | 构建期注入 `NEXT_PUBLIC_APP_VERSION`（`next.config.js`）→ 设置页「系统信息」展示；lock 不同步会击穿 `npm ci` | release 脚本替换 |
| `ir-cli/pyproject.toml` | `ir --version`（`importlib.metadata`） | release 脚本替换 |

## 2. Bump 规则

按上个 `v` tag 以来的 conventional commits 判定（`release.py --suggest` 自动建议）：

| 级别 | 触发条件 |
| --- | --- |
| **MAJOR** | 不兼容变更：删除既有 API 端点或破坏性改变响应结构（ir-cli/前端无法兼容）、不可逆 DB 迁移（`SKIP_DOWNGRADE` 豁免类）、配置/数据格式破坏性调整；commit 带 `BREAKING CHANGE` 或 `!` 标记 |
| **MINOR** | 向后兼容的功能新增（`feat`：新端点/页面/CLI 命令） |
| **PATCH** | 向后兼容的修复与杂项（`fix`/`chore`/`docs`/`refactor`/`test`/`perf`/依赖升级） |

* 常规**可逆** DB 迁移（CI 强制 downgrade 往返验证）属 MINOR/PATCH，不构成 MAJOR。
* openapi 契约演进是常态，仅「破坏已发布契约兼容性」才 MAJOR——本仓前后端/CLI 同仓同部署，兼容压力低。

## 3. 日期与版本的关系

版本号本身**不含日期**——Semver 表达的是变更幅度而非时间。日期体现在三处：

1. CHANGELOG 条目标题：`## vX.Y.Z - YYYY-MM-DD`；
2. git 附注标签时间戳（`git tag -n` / `git for-each-ref refs/tags`）；
3. `deploy/YYYYMMDD-SHORTSHA` 部署标签。

`vX.Y.Z` 标识发布 PR 的精确合并 commit；`deploy/*` 标识实际部署的 commit。只有部署了该发布 commit，两者才对应同一 SHA；Git 版本标签存在本身不证明镜像语义标签存在或已上线。

* **`deploy/*` 只在真正部署时推进**（#456）：纯文档改动合入 main 不触发 CD，也就不打 `deploy/*` 标签，故该标签**可能落后于 main tip**。这不是漏打——它标示的是「当前在跑的镜像对应的 commit」，不是 main tip；追溯上线版本时以它为准，不要拿 main tip 反推。
* **发布 PR 仍触发 CI**：`scripts/release.py` 的发布 commit 必改 `VERSION`（非 `.md`），不会落进 `ci.yml` 的 `paths-ignore` 模式。成功的 main push CI 可触发当前 CD；是否完成部署、镜像是否携带 `:vX.Y.Z`，仍须单独核实。

## 4. 发布流程（两阶段：发布 PR → 打 tag）

main 的 ruleset 要求一切改动经 PR 且 CI OK（**直接推送会被拒绝，无 admin 豁免**），故发布分两阶段。阶段二必须显式选择发布 PR 或完整合并 SHA，不以当前 main tip 或最近提交标题猜测发布目标（#540）。

```bash
python3 scripts/release.py doctor              # 0. 只读版本投影检查
python3 scripts/release.py --suggest           # 1. 看建议的 bump 类型（只读）
python3 scripts/release.py patch --dry-run     # 2. 预览全部改动 + CHANGELOG 草稿（不落盘）
python3 scripts/release.py patch               # 3. 阶段一：release/vX.Y.Z 分支提交 + 推送 + 创建 PR
                                               #    （推送前交互确认；非交互加 --yes；--fixes N 关联 issue）
#    发布 PR 等 CI OK 后按常规合并（merge-commit），再等合并 SHA 的 main push CI 成功
python3 scripts/release.py tag v0.1.1 --pr 123 # 4. 阶段二：以发布 PR #123 的合并 SHA 打标签并推送
# 或使用 --sha <40位完整合并SHA>，与 --pr 互斥且必须选择一个
```

* **前提**（阶段一）：main 分支、工作区干净、与 origin/main 同步；钉版契约环境 `.venv-openapi/` 存在（缺失时脚本给出重建命令：`python3 -m venv .venv-openapi && .venv-openapi/bin/pip install -r backend/requirements.txt`）。
* **阶段一原子完成**：同步 5 处版本文件（含 package-lock 两处；读写保留原行尾，CRLF 文件不翻转）→ 经 `export_openapi.py --offline` 在独立临时 SQLite 子进程重导出 `openapi.json`（不继承业务环境或 `APP_VERSION`）→ `check_openapi.py` + `gen_response_fields.py --check` 验证 → CHANGELOG 顶部插入新条目 → 在 `release/vX.Y.Z` 分支单 commit `chore(release): vX.Y.Z` → 推送分支并创建发布 PR（有 `gh` CLI 时自动建）。
* **PR 身份**：阶段一输出创建的 PR 编号与 URL，提示合并后使用 `tag --pr`；创建时尚无合并 SHA，不记录未来 SHA。阶段二通过 `gh` 查询 origin 对应的 github.com 仓库，要求 PR 已合并、base 为 main、head 为同仓库 `release/vX.Y.Z`、标题为 `chore(release): vX.Y.Z`。`--sha` 也必须唯一对应这样的 PR 的合并提交，不能拿后续普通提交替代。
* **阶段二校验后打 tag**：仅 fetch main（不 fetch tags、不 force），验证目标 SHA 从 origin/main 可达，目标提交的 VERSION 及版本同步矩阵中的静态投影一致；要求该 SHA 最新的可信 CI run 成功完成（同仓库、main、push，workflow 为 `.github/workflows/ci.yml` / `CI`，不是 PR 或手动触发的绿灯）。main 已前进不改变标签目标。缺数据、查询失败或任一校验不通过均停止；无需切换或改写工作区版本文件。
* **幂等与确认**：本地／远程同名标签都按解引用后的 commit SHA 比较，兼容附注标签。任一指向其他 SHA 即拒绝，不覆盖；远程已指向目标时不再创建或推送标签，本地已有正确标签但远程缺失时只推送。新建／推送仍须交互确认，非交互使用 `--yes`；该参数不代替远程写操作授权。
* **只读检查**：`doctor` 复用 `render_file_edits()` 的投影规则，并读取 OpenAPI `info.version`；不写文件、不 fetch、不加载应用或调用契约生成器。普通 HEAD 沿用当前 VERSION、未落在 v 标签提交上是合法情况。它只检查版本投影，不替代全量 OpenAPI 契约门禁、PR／CI 证明或部署验证；原 bump、`--suggest`、`--dry-run` 语义不变。
* **发布时序**：精确 SHA 消除「main 前进导致打错 Git 标签」的问题；制品层时序由发布包 + alias 解决（#537/#540）——`vX.Y.Z` git 标签的存在不再蕴含镜像语义标签存在，该 SHA 的 main push CI 产出发布包后即可随时 alias `:vX.Y.Z`，无需重建镜像（见 §5）。重跑 main push CI 会产出新的发布包与新 run（同 SHA 不同构建的记录互不覆盖）；重跑及一切生产操作仍属另需授权的操作。
* **节奏**：手动触发；功能里程碑收口发 MINOR，一批修复后可批量发 PATCH，不要求每次合入都发版。
* **失败恢复**：阶段一 commit 前失败 → `git restore --staged . && git restore .`（仍在 release 分支，清理后切回 main 删分支）；commit 后未推送 → `git switch main && git branch -D release/vX.Y.Z`；以上清理须先确认无其他工作并取得破坏性操作授权。PR 已合并未打 tag，或本地 tag 已创建而推送失败 → 用同一 `--pr`／`--sha` 重试阶段二；标签冲突须人工核实，脚本不删除或覆盖。
* 首次发布用 `--initial v0.1.0`（基线摘要条目，此前历史不逐条回溯）。

## 5. 镜像、发布包与部署标签

* **CD 不再构建镜像**（#537）：main push CI 的 `image-smoke` job 构建一次（`:sha7`、`:latest`），按 digest 拉回对最终镜像做运行冒烟，并产出机器可校验的**发布包**（release-bundle artifact：完整 SHA、构建身份、VERSION、前后端及 nginx 镜像 digest、Compose/nginx 实际文件与摘要、迁移指纹）。`deploy.yml` 验证触发 run 的来源（仓库/workflow/event/head SHA/run ID/attempt）后消费该包；服务器端 `scripts/server_deploy.sh` 复核 manifest 后才 stage → preflight → activate → probe。
* **语义镜像标签与构建时机解耦**（#540）：`:vX.Y.Z` 不再由构建附带，而是在 git 标签打完后 alias 到已验证发布包的 digest：

  ```bash
  python3 scripts/release.py alias v0.1.1 --bundle <release-bundle目录> [--dry-run] [--yes]
  ```

  前置链：发布包过 `release_bundle.py verify` 同一判据 → 远程 `v0.1.1` git 标签已指向包内 SHA（先完成 §4 阶段二）→ 该 SHA 存在可信成功的 main push CI run → 包的 `run_id` 属于这些成功 run。执行判定以注册表实况为准：目标标签已指向同一 digest 时幂等成功；指向不同 digest 时拒绝——**不通过重建/重部署「补」语义标签**。
* `deploy/YYYYMMDD-SHORTSHA` git 标签机制不变（auto 部署成功后打，回滚目标单一视图；与语义版本正交，推进时机见 §3）。
* **手动 `workflow_dispatch`** 不再接受任意镜像 tag：操作类型显式三选一 `redeploy` / `rollback` / `migrate`，目标是服务器已保留的发布 id（`<sha7>-<run>.<attempt>`，见服务器 `state/accepted-releases.log`），格式严格校验；`migrate` 还须提供服务器 `bootstrap status` 输出的 64 位 DB 状态指纹作为显式迁移授权（锁内重验，失败处置见[部署回滚 runbook](../runbooks/deploy-rollback.md)）。
