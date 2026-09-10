"""四张日志表的字符集常量（issue #427）。

**为什么是 utf8mb4 而不是库级 utf8mb3**：生产 RDS 与 CI 建库都用 utf8mb3
（`utf8mb3_general_ci`，刻意对齐生产 RDS 的库级约定），而**连接侧字符集是 utf8mb4**
（`app/config.py` 连接串 charset=utf8mb4）。这个不对称是刻意的、不能靠改库级 charset
解决；但 utf8mb3 列容不下 4 字节 UTF-8 字符（emoji、CJK 扩展 B 汉字等），MySQL 严格
模式下 errno 1366 `Incorrect string value` 会让**整条**日志记录写不进去——错误记录
被 best-effort except 吸收后静默消失，恰是日志基建最不该有的失效模式。典型来源是
用户输入（路径参数、请求体、投资人 code、User-Agent）被异常文案回显。故四张日志表
**在库级 utf8mb3 之内单独声明 utf8mb4**：库级约定不变，写入侧不再有字符集适配清单
（「哪些列要转义」这种遗漏面不可见的做法被刻意排除）。

单一事实来源：四个模型（`app/models/{audit_log,system_error_log,login_log,
task_execution_log}.py`）、迁移 `0014_log_tables_utf8mb4.py`、守门测试
（`tests/unit/test_migration_0014.py`）三方共用，禁止在任一处写字面量。

**不改库级 charset**：`ci.yml` 三处 `CREATE DATABASE ... utf8mb3` 与
`docker-compose.dev.yml` 的 server 字符集均保持原样，只更新注释说明本例外。

**迁移 0014 是终态而非唯一防线**：模型 `__table_args__` 带 `mysql_charset` 使
`create_all` 建出的新库不依赖库级默认；否则任何新建库（新环境、CI 重建）又会生成
utf8mb3 表、缺陷原样复发。
"""

# 表级字符集：4 字节 UTF-8 字符可写（emoji、CJK 扩展 B 等）
LOG_TABLE_CHARSET = "utf8mb4"

# 排序规则：utf8mb4 的通用排序规则（与库级 utf8mb3_general_ci 的「general_ci」家族一致）。
# 显式写出而非依赖服务端 utf8mb4 默认值（MySQL 8 是 utf8mb4_0900_ai_ci，MariaDB 又不同），
# 保证建表 DDL 与迁移 0014 的 ALTER 落到同一排序规则。
LOG_TABLE_COLLATE = "utf8mb4_general_ci"

# 纳管范围：迁移 0014 与守门测试共用，漏一张即留一处静默丢失面
LOG_TABLES = ("audit_log", "system_error_log", "login_log", "task_execution_log")
