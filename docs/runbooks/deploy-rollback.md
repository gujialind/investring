# 部署回滚 Runbook：镜像回退 × 数据库迁移

> 适用场景：`server_deploy.sh` 统一失败处理后的现场处置、人工经 `workflow_dispatch`
> 回滚/重部署/迁移升级。核心问题：**镜像、配置与数据库兼容性必须共同判断；数据库迁移不会自己回退**。
> 自动化状态机（stage → preflight → activate → probe、锁内旧任务拒绝、LKG 与追加式记录、
> 迁移授权）见 §5；服务器目录约定见 `scripts/server_deploy.sh` 头部注释。

---

## 1. 故障机理（为什么不能只翻镜像）

- 旧镜像的 lifespan/entrypoint 曾在启动时隐式迁移；采用 #537 显式初始化入口的新代码只做启动前 check，DDL 由单独授权的 `app.bootstrap prepare` 执行（命令与退出码见[后端指南](../../backend/AGENTS.md#4-本地启动)）。
- `deploy.yml` 健康检查失败分支原本仅把 `.env` 中 `BACKEND_IMAGE_REF` /
  `FRONTEND_IMAGE_REF` 改回上一 tag 后 `docker compose up -d`。
- 若失败版本启动时**已经执行了新迁移**（迁移成功、但应用因其他原因不健康），数据库
  `alembic_version` 已指向新 revision；旧镜像的 `alembic/versions/` 目录中**不存在**该
  revision，旧后端启动时 `upgrade head` 直接抛错：

  ```
  FAILED: Can't locate revision identified by '<新revision>'
  ```

  旧后端无法启动 → **自动回滚自身失败**，服务停留在不可用状态。

该机理已于 2026-07-31 在本地演练中复现（见 §6 演练记录）。

## 2. 回滚决策流程

1. 先确认本次是否开始过 DDL、是否发生部分失败、探测是否可靠；未知状态或部分执行时停止自动回滚，不以 revision 未变推断数据库未改变。
2. 用旧镜像只读检查 schema/revision（§3）；无法识别 revision 时不能直接回退，但能够识别也只是必要条件。
3. 核对新旧数据语义与配置兼容性，并确认旧镜像对应的 Compose/nginx 文件可恢复；仅有配置摘要或当前 main 的文件不够。条件不成立时优先前滚修复（§4.1）。
4. 条件成立且操作已获授权，才恢复匹配的镜像与受管配置，保留秘密 `.env`、证书和数据库，并重新完成后端、前端、nginx 三路探活。人工降级另按 §4.2，禁止自动 downgrade。

**执行者**：经授权的服务器 SSH 操作人。当前自动化限制见 §5；日志中没有 `Can't locate revision` 不构成恢复安全证明。

## 3. 判定命令（服务器 /opt/investring 下执行）

带显式初始化入口的镜像先运行 `python -m app.bootstrap status` / `check`，不得为探测运行 prepare；ready 也不证明业务数据向后兼容。发布以 `releases/<id>/` 整包保留（`images.env` digest 化引用三个镜像），判定用**目标发布自己的镜像**探测：

```bash
cd /opt/investring
ID=<目标发布 id，见 state/accepted-releases.log>

docker compose --project-name investring --project-directory "releases/$ID" \
  --env-file "releases/$ID/images.env" -f "releases/$ID/docker-compose.yml" \
  run --rm --no-deps -T backend python -m app.bootstrap status
# state=ready               → 仅证明 schema 匹配该发布代码，仍须核对数据与配置兼容性
# state=migration_required  → 该发布需要迁移；只能走显式 migrate 授权（§5），禁止直接激活
# state=unknown_revision    → 迁移缺口/来源不明；停止，人工核对 alembic_version 与各发布迁移清单
# state=error 或探测失败     → 未知状态同样停止；未知不以「revision 未变」推断安全
```

历史遗留镜像（无 bootstrap 入口的旧 tag）可用 `alembic current` 识别 revision：报 `Can't locate revision` 表示迁移缺口，其他执行错误同样必须停止；识别成功后仍须完成 §2 的兼容性判断。

辅助信息（可选）：

```bash
# 发布记录与现场：state/accepted-releases.log（追加式）、state/last-known-good、
# releases/<id>/.deploy-failed（失败现场标记）、state/migrate/*.json（DDL 前持久记录）
cat state/accepted-releases.log | tail -5
# 各发布镜像引用（digest 化，可直接 docker pull 复现制品）
cat "releases/$ID/images.env"
```

## 4. 迁移缺口时的两条路径

### 4.1 路径 A：前滚修复（默认推荐）

迁移已成功、应用因**代码/配置**原因不健康时，数据库并没有坏——回退数据库反而引入
新风险。保持 DB 不动，修复代码后构建新 tag 重新部署（或 `workflow_dispatch` 部署修复
commit）。

适用：绝大多数情况，尤其是含**有损 downgrade** 的迁移（见 §4.3）。

### 4.2 路径 B：downgrade 后回退镜像

仅在单独授权、确认必须回到旧版本且已验证恢复路径时执行；不得由失败处理脚本自动降级：

1. **先备份**：对云 RDS 打手动快照（控制台操作），确认完成后再继续。
2. **检查目标区间每个迁移的 `downgrade()`**：是否实现、是否有损（§4.3）。有损且不可
   接受 → 放弃本路径，改走前滚或从快照恢复。
3. 用**新镜像**（含全部迁移脚本的那个）执行降级到旧镜像的 head：

   ```bash
   cd /opt/investring
   # 旧镜像的 head revision（脚本文件名前缀即 revision，取最大者；或进容器 alembic heads）
   docker run --rm "$PREV_BACKEND" ls alembic/versions/
   # 用新镜像 downgrade 到旧 head
   docker run --rm --env-file .env <当前失败镜像> python -m alembic downgrade <旧head>
   # 校验：旧镜像现在应能识别 DB revision
   docker run --rm --env-file .env "$PREV_BACKEND" python -m alembic current
   ```

4. 重新确认 §2 的条件，恢复与旧镜像匹配的受管 Compose/nginx 配置；缺少对应文件时停止，不从当前 main 取配置替代。保留秘密 `.env`、证书和数据库，仅变更已授权的镜像选择。显式指定 Compose 文件及生产项目名，再启动、重载 nginx 并验证后端、前端、nginx 三路探活；任一路失败都不算恢复成功。

**禁止**：用 `alembic stamp <旧revision>` 只改版本号不动 schema——新迁移的表结构变更
仍留在库里，旧代码与 schema 不匹配，属于制造更隐蔽的故障。

### 4.3 现有迁移可逆性速查（downgrade 风险）

| Revision | 内容 | downgrade | 风险 |
|---|---|---|---|
| 0001 | sync_job 表 + nav_sync_detail.job_id | 已实现 | 删表删列，丢同步历史 |
| 0002 | investor_holding 衍生字段 | 已实现 | 删列，衍生值可重算，低风险 |
| 0003 | trade.transfer_group NOT NULL | 已实现 | 仅放宽约束，无损 |
| 0004 | 份额精度 15,4 → 15,2 | 已实现 | **有损**：仅恢复列类型，被截断的精度不可恢复 |
| 0005 | cash_amount / 日期字段重命名 | 已实现 | 纯重命名，无损 |
| 0006 | product_code 扩展 String(20) + in_transit_total + IN_TRANSIT 种子产品（⚠️ 实测缺陷：`alter_column` 未传 `nullable`，MySQL `MODIFY` 整体替换列定义，6 张表 product_code 的 NOT NULL 被静默剥离——由 0018 前滚修复） | **未实现**（`raise NotImplementedError`） | **不可逆**：IN_TRANSIT 数据存在时无法安全回退（FK 约束 + 列收窄容不下长 code）。跨过 0006 的回滚只能走前滚（§4.1）或从 RDS 快照恢复 |
| 0007 | asset_classification.asset_name 新增 + 回填 | 已实现 | 删列，回填值丢失但可按 `ASSET_NAME_MAP` 重跑迁移恢复；人工改过的 asset_name 不可恢复，低风险 |
| 0008 | 资产分类五维度重构（维度字典 + product 五 FK 列，删旧扁平分类与 portfolio_position.asset_type） | **未实现**（`raise NotImplementedError`） | **不可逆**：旧扁平分类行与 `asset_type` 列已物理删除。跨过 0008 的回滚只能走前滚（§4.1）或从 RDS 快照恢复（与 0006 同类） |
| 0009 | 维度规则表 asset_class_dimension_rule + 适用关系表 asset_dimension_applicability + asset_classification.is_active | 已实现 | 删两表 + 删列（每步带存在性守卫）；**规则矩阵与适用关系行删除后不可恢复**，回退后需重跑迁移或手工重建规则 |
| 0010 | portfolio.display_config（#144 持仓分组覆盖） | 已实现 | 删列；组合级分组覆盖配置丢失，回退后前端恢复默认分组，低风险 |
| 0011 | portfolio.auto_snapshot_enabled（#156 自动快照开关） | 已实现 | 删列；opt-in 开关丢失，回退后所有组合恢复默认 False（自动快照停摆），低风险 |
| 0012 | product.nav_lag_days（逐产品估值滞后天数） | 已实现 | **有损**：删列后逐产品自设值（QDII/港互认惯例 1）丢失且无法从其他字段推导，回退再升级后需人工重设，否则快照取价口径变化 |
| 0013 | 四张日志表纳入 alembic 管理（audit_log / system_error_log / login_log / task_execution_log） | **刻意 no-op**（不删表） | 无损：采纳型迁移——生产库这四张表与其数据均早于本迁移存在（由 `create_all` 建出），`upgrade()` 实为 no-op，故逆操作也不删表（删表 = 销毁审计/登录/任务历史）。另 `task_execution_log` 被 `nav_sync_detail.task_log_id` 外键引用，MySQL 下 DROP 必失败（errno 3730） |
| 0014 | 日志/同步明细表字符集 utf8mb3 → utf8mb4（#427，4 字节字符致 errno 1366 记录写不进去：日志侧静默丢失、`nav_sync_detail` 侧外抛） | 已实现，**有条件跳过** | 无损：回退到 0014 之前不构成迁移缺口风险（旧镜像对 utf8mb4 表照常读写，连接侧本就是 utf8mb4）。downgrade 逐表探测 4 字节字符，**表内已存在则跳过该表并打 WARNING**（反向转 utf8mb3 必然失败），此时该表停留在 utf8mb4——功能上无害，只是字符集与库级不一致 |
| 0015 | 全库字符集统一 utf8mb4（#433）：库级 `ALTER DATABASE` + 其余全部存量表 `CONVERT`，含「先拆外键 → 转码 → 再建外键」 | 已实现，**有条件跳过** | 无损：连接侧本就是 utf8mb4，回退不构成迁移缺口（旧镜像读写 utf8mb4 表照常）。downgrade 与 0014 同判据逐列探测 4 字节字符，**表内已存在则跳过该表并打 WARNING**。⚠️ 执行期会在「拆外键 → 重建」之间短暂失去 FK 保护（DDL 各自隐式提交，包不进一个事务），故择低峰执行；中途失败不会留半成品——重跑即补齐（非空转时总是重放拆→转→建） |
| 0016 | `nav_sync_detail.job_id` 外键去重收敛（#434）：把同列对上的重复外键（生产库 `fk_nav_sync_detail_job_id` + `nav_sync_detail_ibfk_2` 并存）收敛到「恰好一条、名为 `fk_nav_sync_detail_job_id`」 | **刻意 no-op**（不把冗余外键加回来） | 无损：采纳型迁移，`upgrade()` 对全新库（`create_all` 已按模型显式名建出唯一一条）本就是 no-op。逆操作是把语义完全相同的冗余约束加回来，不恢复任何功能、只会把「按名 drop 只删掉一条、另一条继续强制外键语义」的陷阱重新埋回库里。⚠️ 生产库走「只多删冗余那条」分支（好的那条全程不碰），不产生 FK 空窗；只有「仅剩自动名 `*_ibfk_N`」的旧库才拆掉重建。两条外键规则不一致时 `upgrade()` 直接 `RuntimeError`（不静默挑一条），此时部署失败、DB 无变化 |
| 0017 | `price_record.unit_price` 改为 NOT NULL（#580），先删除无单价行 | 仅恢复列可空 | **有损**：已删除的 NULL 单价行不能由 downgrade 恢复，须核对迁移清点记录并保留备份 |
| 0018 | 恢复 6 张表 `product_code` NOT NULL（#537，修复 0006 的 MODIFY 剥离漂移）：两段式——先全量核查 NULL（任一违规即 `RuntimeError` 且零 DDL），再统一 `MODIFY ... NOT NULL` | 已实现（恢复可空） | 无损：仅放宽/收紧约束，不动数据。NULL 违规时诊断：逐表 `SELECT COUNT(*) FROM <表> WHERE product_code IS NULL`（涉及 portfolio_position / trade / price_record / manual_market_value / nav_sync_detail / share_change_event），人工核对来源并清理后重跑 |

> 新增迁移时同步维护本表；`downgrade()` 未实现或有损的迁移，路径 B 前必须先 RDS 快照。

## 5. 自动化配合（deploy.yml + server_deploy.sh）

CD 不构建（消费 CI `image-smoke` 产出并经 `release_bundle.py verify` 校验的发布包），服务器端由 `scripts/server_deploy.sh` 在 `flock` 内执行统一状态机：

- **stage → preflight → activate → probe**：整包落地 `releases/<id>/`（自包含：Compose/nginx 文件、`images.env` digest 引用、bundle.json、manifest）；`sha256sum -c` 复核 + 包身份与 release-id 互证 + 按 digest 拉镜像后才进入 DB 判定；`current` 符号链接原子激活；三路探活（backend `/health`、nginx 容器内 frontend 连通、nginx HTTPS 入口）。
- **自动部署停在写库之前**（exit 4）：preflight 用目标发布自己的镜像只读探测，要求 `state=ready` 且迁移内容指纹与发布包一致；待迁移、缺表、未知 revision、探测错误一律不激活、不写库，并提示走显式 migrate。普通无待迁移发布照常自动上线，无第二位 reviewer。
- **migrate 是显式授权动作**（workflow_dispatch `action=migrate`）：授权绑定准确发布包（release-id）与预期 DB 状态（`--expect-state` = status 的 64 位 fingerprint）；锁内重验指纹未变，`prepare` 在 MySQL `GET_LOCK` 内还会再验一次；**DDL 开始前**把状态持久化到 `state/migrate/`。失败（exit 6）不做自动回滚、不自动 downgrade，现场与 DDL 前记录保留，按 §4 人工决策。注意：fingerprint 绑定连接身份（database_identity 含账号），`expect-state` 必须取自将执行 prepare 的同一账号的 status——服务器端单账号（.env）路径天然一致（2026-09-25 演练实测）。
- **激活后失败统一处理**（exit 5）：先落 `failed` 记录并打 `.deploy-failed` 现场标记；仅当**数据库未被本次动作改变**且上一发布对当前 DB 探出 `ready` 时，才恢复整包并重新三路探活（`restored`）；恢复条件不成立或恢复也失败时保留现场（`restore-failed`），不盲翻。DB 已迁移后禁止自动回退镜像。
- **记录与旧任务拒绝**：`state/accepted-releases.log` 追加式（tsv：ts/kind/release/sha/run/attempt），手动回滚**不降级** auto 记录；`state/last-known-good` 仅在探活通过后推进。auto 部署在 workflow 侧验证主线祖先关系（`deploy_ancestry.py`，docs-only 提交推进 main 不误挡），并在锁内重读记录比对 `--expect-accepted` 快照——排队期间被更新任务插队的旧部署按 exit 3 拒绝。
- **秘密与证书永不进发布链**：`.env` 与 `certbot/www` 由服务器人工维护，发布目录只建符号链接；CD 不复制、不回滚、不重建它们。清理或打标记失败不撤回已健康的部署（只警告）。
- **探测诊断**：bootstrap status/check 的 Compose 与进程 stderr 保存在服务器 `state/bootstrap-<action>-<release-id>.*.log`，每次执行独立建文件（0600，仅部署用户可读写），按目标发布与子命令分段追加；探测失败消息引用路径，不把原文转发到 CI。经授权登录服务器查阅，分享前先脱敏；重试不覆盖旧日志，故障处理后由部署用户按需清理。

自动化不证明的部分：`ready` 与指纹一致只覆盖 schema/迁移内容，不证明业务数据向后兼容；跨不可逆迁移（§4.3）的版本回退仍然只能人工前滚或走 RDS 快照。

## 6. 演练记录

### 2026-07-31 本地 alembic 层演练（已完成）

- **环境**：本地（WSL2，无 docker），alembic 1.13.1 + SQLite 临时库；两套迁移目录模拟
  新旧镜像（旧=仅 0001，新=0001+0002），流程与生产 `env.py`/lifespan 行为一致。
- **步骤与结果**：
  1. 新目录 `upgrade head` → DB 至 `0002 (head)`（模拟失败版本已执行新迁移）。
  2. 切换旧目录 `upgrade head` → **复现故障**：`FAILED: Can't locate revision identified by '0002'`，退出码非 0。确认「旧镜像启动即崩」推断成立。
  3. 判定逻辑：DB revision `0002` 不在旧目录 revision 集合中 → 正确报告迁移缺口。
  4. 路径 B：用新目录 `alembic downgrade 0001` 成功，`current` = `0001`。
  5. 再以旧目录 `upgrade head` → 正常返回 `0001 (head)`，**旧镜像可恢复启动**。
- **结论**：故障机理、判定命令、downgrade 恢复路径三者均验证通过。

### 2026-09-25 隔离容器全链路演练（已完成）

- **环境**：WSL2 + Docker Desktop 28.1.1 / Compose v2.35.1；镜像自 `bc51edd` 构建（带
  `GIT_REVISION` OCI 标签）；隔离 MySQL 8.4；全部资源唯一后缀并自动清理，未接触生产。
- **最终镜像冒烟**（`scripts/smoke_images.py`，**17/17 通过，rc=0**）：镜像 OCI revision
  与目标提交一致 → 空库 `status(empty) → prepare → check(ready)`（heads=[0018]）→ E2E 种子
  → **仅 DML 权限的运行账号启动后端**且 `/health` 200（运行期零 DDL 的强证明）→ 前端
  Next rewrite 代理登录签发 token → 真实 `nginx.conf` + 自签 SAN 证书：80→301、HTTPS
  `/health`（backend upstream）、`/login`（frontend upstream）、`/api/` 登录全部通过。
- **存量库升级演练**（单镜像 `downgrade -1` 模拟存量旧 head，rc=0）：
  1. 空库 prepare → `ready`（revisions=[0018]）；
  2. `alembic downgrade -1` → 0017；`status` 如实报 `migration_required` 且
     `missing_not_null` 列出 6 处 product_code（与 0006 漂移形态一致）；
  3. **过期授权拒绝**：用 downgrade 前的旧指纹 prepare → "Database state changed"
    （exit 2），零 DDL；
  4. 用**将执行 prepare 的同一账号**的 status 指纹重新授权 → upgrade → `ready`；
  5. `check` 通过；运行账号（仅 DML）只读 status 复核 `ready`。
- **演练发现并修复的真实问题**：
  1. **#537 存量 schema 漂移（真实存在）**：0006 的 `alter_column` 未传 `nullable`，
     MySQL `MODIFY` 整体替换列定义，6 张表 `product_code` 的 NOT NULL 被剥离——2026-08
     起每个经「create_all→upgrade」初始化的库（含生产）均受影响；0018 前滚修复（§4.3）。
     漂移库实测：`migration_required` → NULL 行拒绝（零 DDL）→ 授权 prepare → `ready`。
  2. 授权指纹绑定**连接身份**（database_identity 含账号）：`--expect-state` 必须取自
     将执行 prepare 的同一账号的 status；服务器端单账号（.env）路径天然一致。
  3. 冒烟 harness 两处修复：生产镜像不带测试代码 → seed 只读挂载 `backend/tests`；
     Docker Desktop/WSL2 端口映射注册晚于 `run -d` 返回 → 端口查询改轮询等待，容器
     退出按检查失败处理并带日志尾部。
- **未覆盖项（如实记录）**：以真实旧镜像（tag-old）做双镜像「Can't locate revision」
  容器演练未执行——该机理已由 2026-07-31 alembic 层双目录演练复现（§上），unknown
  revision 的拒绝路径另有单元与服务器脚本测试覆盖；判断容器层增量低，需要时可补。
