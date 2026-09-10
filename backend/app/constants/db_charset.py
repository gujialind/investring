"""MySQL 字符集常量（库级 + 表级，issue #433）。

**背景（#427 → #433 的演进）**：生产 RDS 与 CI 建库曾用 `utf8mb3` /
`utf8mb3_general_ci`，而**连接侧是 utf8mb4**（`app/config.py` 连接串 charset=utf8mb4）。
这个不对称的代价是 utf8mb3 列容不下 4 字节 UTF-8 字符（emoji、CJK 扩展 B 汉字等）：
MySQL 严格模式下 errno 1366 `Incorrect string value` 会让**整条**记录写不进去。
#427 先给五张自由文本表（日志/同步明细）单独声明 utf8mb4 作为例外；#433 把库级
charset 一并统一到 utf8mb4，例外随之取消、全库同构。

**为什么最终改库级而不是长久保留表级例外**：表级例外要求「哪些表要 utf8mb4」这份
清单永远正确，而任何**新建**的表（新模型、新迁移）都会静默继承库级 utf8mb3、重新
引入同一个缺陷面——遗漏不可见。库级统一后，`create_all` 与迁移建出的表天然正确。

**为什么常量仍然显式声明而不是删掉**：库级设置只决定**建表时的默认值**，
`ALTER DATABASE ... CHARACTER SET` 不会改动任何已存在的表（MySQL 语义如此）。显式
表级声明让建表 DDL 自带 `CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci`，不依赖「库级
默认恰好被设对」这一外部前提——即使某天有人在控制台把库级改回去，模型建出的表仍正确。

**单一事实来源**：`app/database.py` 的 `Base.metadata`（全库建表默认）、需要显式
声明的模型 `__table_args__`、迁移 `0015`，以及 `tests/unit/test_db_charset.py`
三方共用，禁止在任一处写字面量。
"""

# 库级字符集与排序规则（生产 RDS / CI 建库 / 本地 dev 三处对齐）
DB_CHARSET = "utf8mb4"

# 排序规则：刻意与库级旧值 `utf8mb3_general_ci` 同家族（`general_ci`），而非 MySQL 8
# 的服务端默认 `utf8mb4_0900_ai_ci`——显式写出可让 MySQL 与 MariaDB、不同版本落到
# 同一排序规则，建表 DDL 与迁移 ALTER 也才对得上。
DB_COLLATE = "utf8mb4_general_ci"

# 旧库级值：迁移 0015 的 downgrade 与 0014 的反向转码共用（历史事实，不要用于建表）
LEGACY_DB_CHARSET = "utf8mb3"
LEGACY_DB_COLLATE = "utf8mb3_general_ci"
