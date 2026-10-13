"""场外与港互认历史净值日涨跌回填（#692）

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-13

`price_record.pct_change` 在场外（CN_OTC）与港互认（HK_MUTUAL）从未被写入（生产实测
244373 行 NULL，占全部行情行 74.8%），产品详情页「历史净值」的日涨跌列因此恒为 `--`。
写侧修复与本次回填必须**同批**交付：`_bulk_upsert_prices` 的 `ON DUPLICATE KEY UPDATE`
是无条件覆盖（`VALUES()` 而非 COALESCE），只修一侧的话，另一侧要么继续写 NULL、要么
把回填好的值抹回 NULL。

两条分支，口径与写侧 `market_data_service._derive_otc_pct_change` 逐字一致：

1. **tushare 场外**（生产 ≈24.2 万行）：基准取 `accumulated_nav`。上游 `fund_nav` 无
   涨跌幅字段，只能按累计净值相邻比值推导——总收益口径，现金分红除息日不跳变（分红同步
   计入累计分红），且与产品详情页同页的「区间收益率」（`_nav_interval_return`，同为
   accum 比值）同源。
2. **港互认 ∪ akshare 源场外**（行数少）：基准取 `unit_price`。这两个源的上游字段
   （`NAVCHGRT` / 「日增长率」）本身就是相邻公布净值的比值，本地 LAG 推导与之数学同构；
   日后同步以上游真值覆盖时只在末位舍入内一致，不会口径翻转。

`LAG(...) OVER (PARTITION BY product_code, market ORDER BY price_date)` 跑在基准列
**非空子集**上，故基准 = 最近一条非空行而非「上一交易日」——停牌/缺行时两者不同，这与
写侧 `_db_prev_accum_nav` 的取法一致（刻意不引交易日历）。

幂等设计：`WHERE p.pct_change IS NULL` 只补不覆盖，重复执行无副作用（0007/0012 模式）。
每产品首行 `prev_nav` 天然为 NULL 而跳过，基准非正（<= 0）同样跳过——首行无基准与平盘
是两件事，一律不伪造 `0.00%`。除零另由 `NULLIF` 兜住：MySQL 对含窗口函数的派生表不保证
「先过滤后求值」，只靠 `prev_nav > 0` 会在 SELECT 列表里先撞上 errno 1365（SQLite 除零
返回 NULL，本地测不出来）。

超出列标度的推导值跳过：`pct_change` 是 `Numeric(8,4)`，MySQL 严格模式下越界值会让整条
UPDATE 报 errno 1264 并拖垮部署（一个展示列不值得阻断发布）；被跳过的行保持 NULL，由
issue #692 的部署后抽查 SQL 暴露。

`source IS NULL` 的存量**场外（CN_OTC）**行两条分支都不命中，刻意保持 NULL：
`source <> 'tushare'` 在 SQL 三值逻辑下对 NULL 为假，而无从判断该行来自哪个源，也就
无从选基准列——按 unit_price 兜底会给 tushare 源的行套上除息日假暴跌。这类行由重同步
（写侧已修）补齐。HK_MUTUAL 不受此限：分支 2 的谓词不引用 `source`，NULL-source 的港
互认行照常按 unit_price 回填——该市场只有 akshare 一个上游，不存在选错基准列的问题。

舍入口径：MySQL 的 `ROUND(DECIMAL, 4)` 是 half-away-from-zero（实测 8.4），与写侧
`quantize_nav` 的 HALF_UP 同模式。两侧落值一致是**实测性质、不是 MySQL 的文档保证**：
`×100` 结果的声明标度只有 8 位小数，但服务端在随后的 `ROUND` 里保留了高于该标度的内部
精度，故与写侧「按 Decimal 上下文 28 位有效数字算完再收口」逐位相同——本地 docker
`mysql:8.4` 上 20 万组随机 4 位净值对 0 例分叉，4 位输入能构造出的最贴边界穿越对
（`4.9287 → 4.9465`，真值距 4 位半数边界 1.014e-9）两侧同为 `0.3611`。改中间量标度或
`div_precision_increment` 后须重新实测，不得拿这句话当逐位对账的凭据。SQLite 的 `ROUND`
走二进制 float，末位可能与 HALF_UP 差一个单位——只影响本地/CI 的展示列，故迁移测试避开
半数边界值。

自包含：不 import app 代码（#128 教训，同 0007 头注释惯例）。
"""
import logging

from alembic import op
import sqlalchemy as sa


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

TABLE = "price_record"
# pct_change 的列标度 Numeric(8,4) 可表示范围（app/models/price_record.py）
PCT_RANGE = (-9999.9999, 9999.9999)

OTC_TUSHARE = {
    "label": "CN_OTC/tushare",
    "column": "accumulated_nav",
    "where": "market = 'CN_OTC' AND source = 'tushare' AND accumulated_nav IS NOT NULL",
}
UPSTREAM_PCT = {
    "label": "HK_MUTUAL ∪ CN_OTC/非 tushare",
    "column": "unit_price",
    # unit_price 是 NOT NULL（#580），无需再过滤空值
    "where": "market = 'HK_MUTUAL' OR (market = 'CN_OTC' AND source <> 'tushare')",
}
BRANCHES = (OTC_TUSHARE, UPSTREAM_PCT)


def _derived_rows(branch: dict) -> str:
    """`(id, pct)` 派生表：窗口跑在基准列非空子集上，再滤掉无基准与越界值。

    除法用 `NULLIF(prev_nav, 0)` 而不是只靠外层 `prev_nav > 0` 过滤：MySQL 对含窗口
    函数的派生表不保证「先过滤后求值」，基准为 0 的行会先被算进 SELECT 列表而抛
    errno 1365（Division by 0）整条 UPDATE 失败——SQLite 的除零返回 NULL，本地全绿也
    发现不了。基准列虽然业务上不该为 0，但 24 万行存量里出现脏值的代价是部署失败，
    不值得赌。
    """
    column = branch["column"]
    return (
        "SELECT id, pct FROM ("
        f"SELECT id, ROUND(({column} / NULLIF(prev_nav, 0) - 1) * 100, 4) AS pct FROM ("
        f"SELECT id, {column}, "
        f"LAG({column}) OVER ("
        f"PARTITION BY product_code, market ORDER BY price_date) AS prev_nav "
        f"FROM {TABLE} WHERE {branch['where']}"
        ") AS windowed WHERE prev_nav IS NOT NULL AND prev_nav > 0"
        f") AS derived WHERE pct BETWEEN {PCT_RANGE[0]} AND {PCT_RANGE[1]}"
    )


def _backfill_sql(dialect: str, branch: dict) -> str:
    """按方言拼回填语句（0014/0015 的 `dialect.name` 分叉先例）。

    两侧都是「派生表 + 只补 NULL」，语义一致，只有 UPDATE 的语法不同。窗口函数让派生表
    必然物化，MySQL 不会触发 errno 1093（同表子查询）。
    """
    derived = _derived_rows(branch)
    if dialect == "mysql":
        return (
            f"UPDATE {TABLE} AS p JOIN ({derived}) AS d ON p.id = d.id "
            "SET p.pct_change = d.pct WHERE p.pct_change IS NULL"
        )
    return (
        f"UPDATE {TABLE} AS p SET pct_change = d.pct "
        f"FROM ({derived}) AS d WHERE p.id = d.id AND p.pct_change IS NULL"
    )


def upgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name
    for branch in BRANCHES:
        result = bind.execute(sa.text(_backfill_sql(dialect, branch)))
        logger.info(
            "0020 回填 %s 的日涨跌 %d 行（基准列 %s）",
            branch["label"], max(result.rowcount, 0), branch["column"],
        )


def downgrade():
    """刻意 no-op（0013/0016 先例）：回填不反向清除。

    回填值可由同表的 `accumulated_nav` / `unit_price` 重新推导，没有任何数据被销毁；
    反向置 NULL 则会连带删掉上线后新同步写入的合法值——库里无从区分「迁移补的」与
    「同步写的」。`pct_change` 是纯展示列，无账务下游，回滚功能本身不需要动数据。
    """
    logger.info("0020 downgrade no-op：pct_change 回填不反向清除，理由见函数注释")
