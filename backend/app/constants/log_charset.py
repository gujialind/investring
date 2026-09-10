"""日志/同步明细表的表级字符集纳管清单（issue #427；#433 起库级亦为 utf8mb4）。

**这个模块现在只承载「历史事实 + 纳管清单」**，不再是「库级之外的例外」：

- 建表用的 charset/collate 常量已移到 `app/constants/db_charset.py`（#433 把库级一并
  统一到 utf8mb4 后，表级声明与库级默认同值，常量不再有「日志专属」语义）；
- 迁移 `0014` 与 `tests/unit/test_migration_0014.py` 仍引用本模块的 `LOG_TABLE_CHARSET`
  / `LOG_TABLE_COLLATE` / `CHARSET_TABLES`——**0014 是已在生产执行过的历史迁移，
  不改其 import 与语义**（它记录的正是「库级还是 utf8mb3 时这五张表单独提 utf8mb4」
  这一步），改它只会让历史与仓库对不上。

**#427 的原始缺陷形态**（保留以便读懂 0014）：库级 utf8mb3 与连接侧 utf8mb4 不对称，
utf8mb3 列容不下 4 字节 UTF-8 字符（emoji、CJK 扩展 B 汉字），MySQL 严格模式下 errno
1366 `Incorrect string value` 让**整条**记录写不进去。分两档：

- `audit_log` / `system_error_log`：写入 best-effort（`record_system_error` 的 except
  吸收）→ **整条日志静默消失**，恰是日志基建最不该有的失效模式；
- `login_log` / `task_execution_log` / `nav_sync_detail`：写入直接 `commit()` →
  **外抛 500**，连带登录、任务执行、净值同步本身失败。

`nav_sync_detail` 与四张日志表同库同型，故一并纳管（#427 讨论中决定）。

**#433 之后的终局**：库级 charset 已随迁移 `0015` 统一为 utf8mb4，全库同构；「哪些表
需要 utf8mb4」这份清单不再需要长期维护——新建表天然继承正确的库级默认。本模块因此是
**只读的历史快照**，不要往 `CHARSET_TABLES` 里加表。
"""

# 表级字符集：4 字节 UTF-8 字符可写（emoji、CJK 扩展 B 等）
LOG_TABLE_CHARSET = "utf8mb4"

# 排序规则：utf8mb4 的通用排序规则（与库级旧值 utf8mb3_general_ci 的「general_ci」
# 家族一致）
LOG_TABLE_COLLATE = "utf8mb4_general_ci"

# 迁移 0014 的纳管范围（历史事实，勿增删）：漏一张即留一处 #427 的失效面
CHARSET_TABLES = (
    "audit_log",
    "system_error_log",
    "login_log",
    "task_execution_log",
    "nav_sync_detail",
)
