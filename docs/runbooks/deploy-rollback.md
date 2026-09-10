# 部署回滚 Runbook：镜像回退 × 数据库迁移

> 适用场景：`deploy.yml` 健康检查失败触发自动回滚，或人工经 `workflow_dispatch` 指定旧
> tag 回滚。核心问题：**镜像可以随意回退，数据库迁移不会自己回退**。

---

## 1. 故障机理（为什么不能只翻镜像）

- `backend/app/main.py` 的 lifespan 在**每次启动**时自动执行 `alembic upgrade head`。
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

```
部署失败，准备回滚到旧 tag
        │
        ▼
【判定】DB 当前 revision 是否在旧镜像的迁移目录中？（§3）
        │
   ┌────┴─────┐
   在          不在（迁移缺口）
   │           │
   ▼           ▼
直接回退镜像   ┌─ 路径 A（默认推荐）：前滚修复（§4.1）
（安全）      └─ 路径 B：先 downgrade 再回退镜像（§4.2）
```

**执行者**：服务器 SSH 操作人（个人项目即维护者本人）。deploy.yml 的自动回滚分支已内置
迁移缺口检测（§5）：检测到缺口时**不再盲翻镜像**，直接失败并提示按本 runbook 人工处置。

## 3. 判定命令（服务器 /opt/investring 下执行）

用**旧镜像**跑一次 `alembic current`：能识别 DB 当前 revision 即安全，报
`Can't locate revision` 即存在迁移缺口。

```bash
cd /opt/investring
PREV_BACKEND=<旧后端镜像引用，如 registry.../investring-backend:abc1234>

docker run --rm --env-file .env "$PREV_BACKEND" python -m alembic current
# 输出形如 "0005 (head)"        → 无缺口，直接回退镜像安全
# 输出 "Can't locate revision"  → 迁移缺口，走 §4 决策
```

辅助信息（可选）：

```bash
# DB 实际 revision（用任意能连库的镜像均可）
docker run --rm --env-file .env <当前失败镜像> python -m alembic current
# 新旧镜像各自的迁移脚本清单
docker run --rm "$PREV_BACKEND" ls alembic/versions/
docker run --rm <当前失败镜像> ls alembic/versions/
```

## 4. 迁移缺口时的两条路径

### 4.1 路径 A：前滚修复（默认推荐）

迁移已成功、应用因**代码/配置**原因不健康时，数据库并没有坏——回退数据库反而引入
新风险。保持 DB 不动，修复代码后构建新 tag 重新部署（或 `workflow_dispatch` 部署修复
commit）。

适用：绝大多数情况，尤其是含**有损 downgrade** 的迁移（见 §4.3）。

### 4.2 路径 B：downgrade 后回退镜像

确认必须回到旧版本（如新迁移本身就是故障源）时：

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

4. 回退镜像并验证：

   ```bash
   sed -i "s|^BACKEND_IMAGE_REF=.*|BACKEND_IMAGE_REF=$PREV_BACKEND|" .env
   sed -i "s|^FRONTEND_IMAGE_REF=.*|FRONTEND_IMAGE_REF=<旧前端镜像引用>|" .env
   docker compose up -d
   # 刷新 nginx upstream 解析（容器重建后换新 IP，不重载入口会持续 502，issue #104）
   docker compose exec -T nginx nginx -s reload || docker compose restart nginx
   curl -sf http://127.0.0.1:8000/health && echo OK
   ```

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
| 0006 | product_code 扩展 String(20) + in_transit_total + IN_TRANSIT 种子产品 | **未实现**（`raise NotImplementedError`） | **不可逆**：IN_TRANSIT 数据存在时无法安全回退（FK 约束 + 列收窄容不下长 code）。跨过 0006 的回滚只能走前滚（§4.1）或从 RDS 快照恢复 |
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

> 新增迁移时同步维护本表；`downgrade()` 未实现或有损的迁移，路径 B 前必须先 RDS 快照。

## 5. 自动化配合（deploy.yml）

健康检查失败分支在翻回镜像前，先用旧镜像跑 `alembic current` 做迁移缺口检测：

- 无缺口 → 照旧自动回退镜像（原行为）。
- 检出 `Can't locate revision` → **跳过自动回滚**、workflow 失败，日志中指引本 runbook。

经 `workflow_dispatch` 指定旧 tag 回滚同样受影响（旧镜像启动时同样 `upgrade head`）：
含迁移的版本请先按 §3 判定，必要时先走 §4.2 再触发部署。

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

### 待办：非生产环境容器级完整演练（一次性）

本地环境无 docker，容器级演练待在带 docker 的非生产机器执行一次并回填记录：

1. 用 `docker-compose.dev.yml` 起一套隔离栈（独立 MySQL 库）。
2. 构建两个后端镜像：tag-old（当前 main）、tag-new（在其上加一条演练迁移，编号取当时 head + 1，如加一个可空列，`downgrade()` 删列）。
3. 部署 tag-new → 确认 `alembic current` 为演练 revision、应用健康。
4. 模拟失败回滚：按 §3 用 tag-old 跑 `alembic current`，**预期**报 `Can't locate revision`。
5. 按 §4.2 用 tag-new `downgrade` 到当前 main 的 head，再切 tag-old `compose up`，**预期**旧后端健康。
6. 将实际输出回填至本节，删除本待办。
