"""模型层的声明式 Base 与全库建表字符集钩子（issue #433）。

`Base` 是全部模型的声明式基类（`app/database.py` 原样再导出，保持既有 import 路径）。
本模块把它放在模型包内的原因是**钩子必须装在建类之前**：见下。

**为什么需要这个钩子**：全库统一 utf8mb4 靠两层落地——迁移 `0015` 转存量表，
`create_all` 建新表。后者要求**每张表**的建表 DDL 自带 `CHARSET=utf8mb4 COLLATE
=utf8mb4_general_ci`，否则新库会继承库级设置（建库语句在迁移之前执行，库级还是
utf8mb3 时表就是 utf8mb3，缺陷原样复发）。25 个模型各写一遍 `__table_args__` 的话，
漏一处就是一处静默的 utf8mb3 表、而遗漏不可见；钩子让「所有模型表都声明字符集」成为
结构性保证（`tests/unit/test_db_charset.py` 逐模型断言）。

**为什么不是 `MetaData(mysql_charset=…)`**：SQLAlchemy 2.0 的 `MetaData.__init__`
**不接受**任何方言参数（签名只有 `schema` / `quote_schema` / `naming_convention` /
`info`），`mysql_charset` 是 `Table` 级 dialect kwarg——写在 MetaData 上直接
`TypeError`。

**为什么不用 `MetaData` 的 `before_create` 事件**：该事件只在 `create()` /
`create_all()` 那条真实路径上触发，`CreateTable(...).compile()` **不触发**（实测）。
DDL 与「编译期即可检查」这件事因此脱钩——测试里编译一遍看到的 DDL 与实际建表可能不同，
是最难发现的一类偏差。

**为什么不是「`Base` 建好后再赋 `Base.__init_subclass__`」**：`__init_subclass__` 在
**父类**上查找，且 `declarative_base()` 返回的 `Base` 是已有类，事后给它赋值
**不会被已存在的父类链采纳**（实测：表格 DDL 不带 CHARSET）。必须在 `Base` 的
**类体**里声明，故本模块用中间基类 `_CharsetBase` 承载钩子，`Base` 继承它。

**保留显式值**：子类自己写了 `mysql_charset` 则以子类为准（`setdefault` 语义），钩子
只补默认、不覆盖。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import declarative_base

from app.constants.db_charset import DB_CHARSET, DB_COLLATE


class _CharsetBase:
    """承载 `__init_subclass__` 钩子的中间基类（不映射任何表）。"""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

        declared = cls.__dict__.get("__table_args__")
        constraints: tuple = ()
        if isinstance(declared, dict):
            options = dict(declared)
        elif isinstance(declared, tuple) and declared and isinstance(declared[-1], dict):
            # 声明式 `__table_args__` 的「(约束, …, {方言参数})」形式：末尾元素是 dict 即为
            # 方言参数，其余是约束/索引，原样保留。
            constraints, options = declared[:-1], dict(declared[-1])
        elif isinstance(declared, tuple):
            constraints, options = declared, {}
        else:
            options = {}

        options.setdefault("mysql_charset", DB_CHARSET)
        options.setdefault("mysql_collate", DB_COLLATE)

        cls.__table_args__ = (*constraints, options) if constraints else options


Base = declarative_base(cls=_CharsetBase)
