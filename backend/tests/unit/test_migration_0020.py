# ============================================================================
# 单元测试：issue #692 迁移 0020 场外/港互认日涨跌回填
# ============================================================================
# 断言重点：
# ① 修订链挂在 0019 之后；分支谓词、基准列与**模型声明**绑死，且两条分支恰好
#    无重叠地覆盖写侧处理的市场集合（重叠 ⇒ 同一行按两种基准各算一次，谁赢取决于
#    BRANCHES 顺序，静默口径漂移）；
# ② 两条分支的推导值正确：tushare 场外走 accumulated_nav（总收益口径），港互认 ∪
#    akshare 场外走 unit_price（与上游 NAVCHGRT /「日增长率」数学同构）；
# ③ 三类「不写值」的守卫：每产品首行无基准、基准 <= 0、推导值超出 Numeric(8,4) 标度
#    （MySQL 严格模式下越界会让整条 UPDATE 报 errno 1264 并拖垮部署）；
# ④ 只补 NULL：已有值不被覆盖，重跑无副作用（幂等）；downgrade 是 no-op 且不抛；
# ⑤ 基准列为 NULL 的行自身跳过，但**不作为**基准断链——LAG 跑在非空子集上，下一条
#    非空行的基准是最近一条非空行（与写侧 `_db_prev_accum_nav` 同取法）。
#
# ②③⑤ 在 SQLite 上跑（本地/CI backend-test），MySQL 真身另有一组 dialect 用例把
# SQL 的 `ROUND(DECIMAL, 4)`（half-away-from-zero）与写侧 `quantize_nav`（HALF_UP）
# 逐位对齐——SQLite 的 ROUND 走二进制 float，末位可能差一个单位，故那组只在 MySQL 断言。
#
# 0020 是纯 DML（无 DDL），两种方言都可在事务内回滚，故直接用 `test_engine.connect()`
# + teardown rollback（与 test_migration_0019.py 同法），不需要 0016 那套收尾重建。
# ============================================================================

import importlib.util
import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.models.price_record import PriceRecord
from app.models.product import Product
from app.utils.quantize import quantize_nav

MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic" / "versions" / "0020_backfill_price_record_pct_change.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_0020_under_test", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migration = _load_migration()

PRICE = PriceRecord.__table__
PRODUCT = Product.__table__

# 本文件自有的产品，避免与 tests/seed_base.py 的种子行混淆断言。
OTC_TUSHARE = "021550.OF"      # 场外，tushare：accumulated_nav 基准
OTC_TUSHARE_ZERO = "021552.OF"  # 场外，tushare：基准 <= 0 守卫
OTC_AKSHARE = "021551.OF"      # 场外，非 tushare：unit_price 基准
HK = "968000"                  # 港互认：unit_price 基准
OTC_TUSHARE_EDGE = "021553.OF"  # 场外，tushare：仅供 MySQL 半数边界用例造行
OTC_NO_SOURCE = "021554.OF"    # 场外，source 为 NULL：两条分支都不该认领
HK_NO_SOURCE = "968001"        # 港互认，source 为 NULL：分支 2 不引用 source，仍应回填
EXCHANGE = "510301.SH"         # 场内：两条分支都不该碰

PRODUCTS = (
    (OTC_TUSHARE, "CN_OTC", "tushare"),
    (OTC_TUSHARE_ZERO, "CN_OTC", "tushare"),
    (OTC_TUSHARE_EDGE, "CN_OTC", "tushare"),
    (OTC_NO_SOURCE, "CN_OTC", None),
    (OTC_AKSHARE, "CN_OTC", "akshare"),
    (HK, "HK_MUTUAL", "akshare"),
    (HK_NO_SOURCE, "HK_MUTUAL", None),
    (EXCHANGE, "CN_EXCHANGE", "tushare"),
)

# (code, market, price_date, unit_price, accumulated_nav, source, preset_pct_change)
ROWS = (
    # --- tushare 场外：累计净值基准 ---
    (OTC_TUSHARE, "CN_OTC", date(2026, 9, 28), 1.1600, 1.0700, "tushare", None),
    (OTC_TUSHARE, "CN_OTC", date(2026, 9, 29), 1.1700, 1.0750, "tushare", None),
    (OTC_TUSHARE, "CN_OTC", date(2026, 9, 30), 1.1728, 1.0796, "tushare", None),
    # accum 为 NULL：自身跳过，且不参与 LAG 链
    (OTC_TUSHARE, "CN_OTC", date(2026, 10, 1), 1.1800, None, "tushare", None),
    # 基准跨过上面那条 NULL 行，仍取 1.0796
    (OTC_TUSHARE, "CN_OTC", date(2026, 10, 2), 1.1900, 1.0900, "tushare", None),
    # 已有值：只补 NULL，不得覆盖
    (OTC_TUSHARE, "CN_OTC", date(2026, 10, 3), 1.2000, 1.1000, "tushare", 1.5),
    # --- tushare 场外：基准 <= 0 守卫 ---
    (OTC_TUSHARE_ZERO, "CN_OTC", date(2026, 9, 29), 1.0000, 0.0000, "tushare", None),
    (OTC_TUSHARE_ZERO, "CN_OTC", date(2026, 9, 30), 1.0000, 1.0000, "tushare", None),
    (OTC_TUSHARE_ZERO, "CN_OTC", date(2026, 10, 1), 1.0200, 1.0200, "tushare", None),
    # --- source 为 NULL 的场外存量行：无从判断该用哪个基准，一律不认领 ---
    (OTC_NO_SOURCE, "CN_OTC", date(2026, 9, 29), 3.0000, 3.0000, None, None),
    (OTC_NO_SOURCE, "CN_OTC", date(2026, 9, 30), 3.0300, 3.0300, None, None),
    # --- akshare 场外：单位净值基准 ---
    (OTC_AKSHARE, "CN_OTC", date(2026, 9, 29), 2.0000, None, "akshare", None),
    (OTC_AKSHARE, "CN_OTC", date(2026, 9, 30), 2.0100, None, "akshare", None),
    # 越界推导值（≈ 4477 万 %）：跳过并留 NULL，而不是让整条 UPDATE 失败
    (OTC_AKSHARE, "CN_OTC", date(2026, 10, 1), 900000.0000, None, "akshare", None),
    # --- 港互认：单位净值基准，含下跌日 ---
    (HK, "HK_MUTUAL", date(2026, 9, 29), 10.5700, None, "akshare", None),
    (HK, "HK_MUTUAL", date(2026, 9, 30), 10.5600, None, "akshare", None),
    # --- source 为 NULL 的港互认行：分支 2 谓词不引用 source，仍按 unit_price 回填 ---
    (HK_NO_SOURCE, "HK_MUTUAL", date(2026, 9, 29), 10.0000, None, None, None),
    (HK_NO_SOURCE, "HK_MUTUAL", date(2026, 9, 30), 10.0200, None, None, None),
    # --- 场内：分支谓词都不命中，一行都不能动 ---
    (EXCHANGE, "CN_EXCHANGE", date(2026, 9, 29), 4.0000, None, "tushare", None),
    (EXCHANGE, "CN_EXCHANGE", date(2026, 9, 30), 4.0400, None, "tushare", None),
)

# 手算期望值（4 位，避开半数边界；SQLite 的 ROUND 走二进制 float，容差取一个末位单位）
EXPECTED = {
    (OTC_TUSHARE, date(2026, 9, 28)): None,        # 首行无基准
    (OTC_TUSHARE, date(2026, 9, 29)): 0.4673,      # 1.0750/1.0700-1
    (OTC_TUSHARE, date(2026, 9, 30)): 0.4279,      # 1.0796/1.0750-1
    (OTC_TUSHARE, date(2026, 10, 1)): None,        # accum NULL
    (OTC_TUSHARE, date(2026, 10, 2)): 0.9633,      # 1.0900/1.0796-1（跨过 NULL 行）
    (OTC_TUSHARE, date(2026, 10, 3)): 1.5,         # 原值不动
    (OTC_TUSHARE_ZERO, date(2026, 9, 29)): None,   # 首行无基准
    (OTC_TUSHARE_ZERO, date(2026, 9, 30)): None,   # 基准 0，不伪造值
    (OTC_TUSHARE_ZERO, date(2026, 10, 1)): 2.0,    # 1.0200/1.0000-1
    (OTC_AKSHARE, date(2026, 9, 29)): None,        # 首行无基准
    (OTC_AKSHARE, date(2026, 9, 30)): 0.5,         # 2.0100/2.0000-1
    (OTC_AKSHARE, date(2026, 10, 1)): None,        # 越界跳过
    (OTC_NO_SOURCE, date(2026, 9, 29)): None,      # source NULL：不认领
    (OTC_NO_SOURCE, date(2026, 9, 30)): None,      # source NULL：不认领
    (HK, date(2026, 9, 29)): None,                 # 首行无基准
    (HK, date(2026, 9, 30)): -0.0946,              # 10.5600/10.5700-1
    (HK_NO_SOURCE, date(2026, 9, 29)): None,       # 首行无基准（source NULL 不影响认领）
    (HK_NO_SOURCE, date(2026, 9, 30)): 0.2,        # 10.0200/10.0000-1
    (EXCHANGE, date(2026, 9, 29)): None,
    (EXCHANGE, date(2026, 9, 30)): None,
}

OWN_CODES = tuple(code for code, _, _ in PRODUCTS)


def _run(fn, conn):
    """在给定连接上执行程序化迁移（alembic.op 代理需 Operations.context 安装）。"""
    ctx = MigrationContext.configure(conn)
    with Operations.context(ctx):
        fn()


@pytest.fixture
def migration_conn(test_engine, _seed_base_data):
    """自有连接 + 造行；teardown 一律回滚（0020 纯 DML，两种方言都吃事务）。"""
    with test_engine.connect() as conn:
        for code, market, source in PRODUCTS:
            conn.execute(
                PRODUCT.insert().values(
                    code=code, market=market, name=f"0020-{code}",
                    product_type="FUND", data_source=source,
                )
            )
        for code, market, day, unit, accum, source, preset in ROWS:
            conn.execute(
                PRICE.insert().values(
                    product_code=code, market=market, price_date=day,
                    unit_price=unit, accumulated_nav=accum, source=source,
                    pct_change=preset,
                )
            )
        try:
            yield conn
        finally:
            conn.rollback()


def _pcts(conn):
    """本文件自有行的 pct_change，按 (code, price_date) 索引。"""
    rows = conn.execute(
        sa.select(PRICE.c.product_code, PRICE.c.price_date, PRICE.c.pct_change).where(
            PRICE.c.product_code.in_(OWN_CODES)
        )
    ).all()
    return {(code, day): pct for code, day, pct in rows}


def _assert_expected(conn):
    got = _pcts(conn)
    assert set(got) == set(EXPECTED), set(got) ^ set(EXPECTED)
    for key, want in EXPECTED.items():
        actual = got[key]
        if want is None:
            assert actual is None, f"{key} 期望留 NULL，实得 {actual}"
        else:
            assert actual is not None, f"{key} 期望 {want}，实得 NULL"
            assert abs(float(actual) - want) < 1e-4, f"{key} 期望 {want}，实得 {actual}"


# ---------------------------------------------------------------------------
# 修订链、模型绑定与分支谓词
# ---------------------------------------------------------------------------


class TestRevisionChain:
    def test_links_onto_0019(self):
        assert migration.revision == "0020"
        assert migration.down_revision == "0019"

    def test_targets_the_price_record_model(self):
        """迁移认定的表就是模型声明的那张；基准列必须真实存在（拼错列名 = 整条 UPDATE 报错）。"""
        assert migration.TABLE == PriceRecord.__tablename__
        columns = {col.name for col in PriceRecord.__table__.columns}
        assert {"accumulated_nav", "unit_price", "pct_change", "source", "market"} <= columns

    def test_pct_range_matches_the_column_scale(self):
        """范围守卫的边界由列标度决定，不能写死后与模型漂移。

        `Numeric(8,4)` 的可表示范围就是 ±9999.9999；标度改了而守卫没跟上 ⇒ 越界值
        照旧 errno 1264 拖垮部署。
        """
        column = PriceRecord.__table__.c.pct_change.type
        assert isinstance(column, sa.Numeric)
        limit = Decimal(10) ** (column.precision - column.scale) - Decimal(1).scaleb(-column.scale)
        assert Decimal(str(migration.PCT_RANGE[0])) == -limit
        assert Decimal(str(migration.PCT_RANGE[1])) == limit

    def test_migration_does_not_import_models(self):
        """迁移只带冻结的字面量，不 import `app.models` / `app.constants`（#128）。

        import 模型等于把「已执行迁移的行为」交给后续模型演进决定：模型一改，这条
        早已跑完的迁移在全新库上的行为就跟着变。
        """
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        assert "from app.models" not in source
        assert "import app.models" not in source
        assert "from app.constants" not in source


class TestBranchPredicates:
    def test_branches_use_the_documented_baseline_columns(self):
        """基准列写死在这里：改基准列就是改口径，必须由评审看见而不是被顺手改掉。"""
        assert migration.OTC_TUSHARE["column"] == "accumulated_nav"
        assert migration.UPSTREAM_PCT["column"] == "unit_price"
        assert migration.BRANCHES == (migration.OTC_TUSHARE, migration.UPSTREAM_PCT)

    def test_branches_are_disjoint(self, migration_conn):
        """同一行命中两条分支 ⇒ 按两种基准各算一次，谁赢取决于 BRANCHES 顺序。"""
        count = migration_conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM price_record WHERE "
                f"({migration.OTC_TUSHARE['where']}) AND ({migration.UPSTREAM_PCT['where']})"
            )
        ).scalar()
        assert count == 0, count

    def test_branches_cover_every_row_the_write_side_handles(self, migration_conn):
        """场外与港互认必须恰好落进一条分支——漏掉就是历史页恒 `--`（#692 原症状）。

        场内（CN_EXCHANGE）走上游 `pct_chg` 透传，写侧不推导、本迁移也不回填。
        基准列为 NULL 的行无从推导，落不进任何分支是正确行为（不是漏覆盖），故判据按
        「该行有没有可推导的基准」分别陈述。
        """
        rows = migration_conn.execute(
            sa.text(
                "SELECT product_code, market, source, accumulated_nav IS NULL, "
                f"CASE WHEN {migration.OTC_TUSHARE['where']} THEN 1 ELSE 0 END, "
                f"CASE WHEN {migration.UPSTREAM_PCT['where']} THEN 1 ELSE 0 END "
                "FROM price_record WHERE market IN ('CN_OTC', 'HK_MUTUAL', 'CN_EXCHANGE')"
            )
        ).all()
        assert rows, "种子行缺失，本用例失去判据"
        for code, market, source, accum_null, in_otc, in_upstream in rows:
            hits = in_otc + in_upstream
            if market == "CN_EXCHANGE" or (market == "CN_OTC" and source is None):
                # 场内走上游透传；场外 source 缺失则无从选基准列（分支 2 的
                # `source <> 'tushare'` 对 NULL 为假），一律不认领。HK_MUTUAL 不在此列：
                # 其分支谓词不引用 source，NULL-source 行照常按 unit_price 回填（该市场
                # 只有 akshare 一个上游，无基准列歧义）。
                assert hits == 0, (code, market, source)
            elif market == "CN_OTC" and source == "tushare":
                # unit_price 基准对该源不成立（上游无涨跌幅字段），只有 accum 可推导
                assert hits == (0 if accum_null else 1), (code, market, source, accum_null)
            else:
                assert hits == 1, (code, market, source)

        # 自有行必须被逐条覆盖到，否则上面的判据可能一条都没检查到本用例造的形态
        own = {code for code, market, _, accum_null, in_otc, in_upstream in rows
               if code in OWN_CODES and (in_otc + in_upstream) == 1}
        assert {OTC_TUSHARE, OTC_AKSHARE, HK, HK_NO_SOURCE} <= own, own


# ---------------------------------------------------------------------------
# 回填语义（双后端可跑）
# ---------------------------------------------------------------------------


class TestBackfill:
    def test_upgrade_fills_both_branches(self, migration_conn):
        _run(migration.upgrade, migration_conn)
        _assert_expected(migration_conn)

    def test_upgrade_is_idempotent(self, migration_conn, caplog):
        """重复执行无副作用，且日志如实报 0 行（回填量是部署时的核对依据）。"""
        with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
            _run(migration.upgrade, migration_conn)
        first = _pcts(migration_conn)
        messages = [r.getMessage() for r in caplog.records if r.getMessage().startswith("0020")]
        assert any("CN_OTC/tushare" in m for m in messages), messages
        assert any("HK_MUTUAL" in m for m in messages), messages

        caplog.clear()
        with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
            _run(migration.upgrade, migration_conn)
        assert _pcts(migration_conn) == first
        # 只取本迁移自己的行计数，alembic 的 "Context impl ..." 不在此列
        counts = [r.getMessage() for r in caplog.records if r.getMessage().startswith("0020")]
        assert len(counts) == len(migration.BRANCHES), counts
        assert all("0 行" in message for message in counts), counts

    def test_does_not_touch_rows_outside_its_own_scope(self, migration_conn):
        """场内行与已有值都不动：回填只补自己口径内的 NULL。"""
        _run(migration.upgrade, migration_conn)
        got = _pcts(migration_conn)
        assert got[(EXCHANGE, date(2026, 9, 30))] is None
        assert float(got[(OTC_TUSHARE, date(2026, 10, 3))]) == 1.5

    def test_downgrade_is_a_no_op(self, migration_conn, caplog):
        """`ci.yml` 的 `alembic downgrade -1` 是真实执行路径，no-op 不等于 raise。

        回填不反向清除：库里无从区分「迁移补的」与「上线后同步写的」，置 NULL 会连带
        删掉新同步的合法值，而 `pct_change` 是纯展示列、无账务下游。
        """
        _run(migration.upgrade, migration_conn)
        before = _pcts(migration_conn)
        with caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
            _run(migration.downgrade, migration_conn)
        assert _pcts(migration_conn) == before
        assert any("no-op" in r.getMessage() for r in caplog.records), [
            r.getMessage() for r in caplog.records
        ]


# ---------------------------------------------------------------------------
# MySQL：SQL ROUND 与写侧 quantize_nav 逐位对齐
# ---------------------------------------------------------------------------


@pytest.mark.dialect
class TestMysqlRoundingMatchesWriteSide:
    """生产落值必须与写侧 `_derive_otc_pct_change` 逐位一致，否则同一产品跨批次同步
    会出现两种末位口径。

    MySQL 的 `ROUND(DECIMAL, 4)` 是 half-away-from-zero，与 `quantize_nav` 的
    ROUND_HALF_UP 同口径；SQLite 的 ROUND 走二进制 float，不在此断言。
    """

    # (code, price_date) → (基准值, 当日值)：手写基准链，期望值一律经 quantize_nav 算出，
    # 不复制迁移里的 ROUND 表达式，才构成两侧口径的交叉验证。
    CHAIN = {
        (OTC_TUSHARE, date(2026, 9, 29)): ("1.0700", "1.0750"),
        (OTC_TUSHARE, date(2026, 9, 30)): ("1.0750", "1.0796"),
        (OTC_TUSHARE, date(2026, 10, 2)): ("1.0796", "1.0900"),
        (OTC_TUSHARE_ZERO, date(2026, 10, 1)): ("1.0000", "1.0200"),
        (OTC_AKSHARE, date(2026, 9, 30)): ("2.0000", "2.0100"),
        (HK, date(2026, 9, 30)): ("10.5700", "10.5600"),
    }

    def _mysql_only(self, conn):
        if conn.dialect.name != "mysql":
            pytest.skip("SQL ROUND 与 HALF_UP 的同口径只在 MySQL 成立")

    def test_derived_values_equal_quantize_nav(self, migration_conn):
        self._mysql_only(migration_conn)
        _run(migration.upgrade, migration_conn)
        got = _pcts(migration_conn)
        for key, (prev, current) in self.CHAIN.items():
            want = quantize_nav((Decimal(current) / Decimal(prev) - 1) * 100)
            assert Decimal(str(got[key])) == want, (key, got[key], want)

    def test_rounding_is_half_up_on_the_fifth_decimal(self, migration_conn):
        """半数边界：第 5 位恰为 5 时 ROUND 与 HALF_UP 必须同向进位。

        基准列本身是 `Numeric(10,4)`，故边界值只能由比值造出：8.0000 → 8.0001 得
        100 × 0.0001 / 8 = 0.00125，HALF_UP → 0.0013（HALF_EVEN 会给 0.0012）。
        """
        self._mysql_only(migration_conn)
        for day, accum in ((date(2026, 12, 1), "8.0000"), (date(2026, 12, 2), "8.0001")):
            migration_conn.execute(
                PRICE.insert().values(
                    product_code=OTC_TUSHARE_EDGE, market="CN_OTC", price_date=day,
                    unit_price=1.0, accumulated_nav=accum, source="tushare",
                )
            )
        _run(migration.upgrade, migration_conn)
        got = _pcts(migration_conn)
        assert got[(OTC_TUSHARE_EDGE, date(2026, 12, 1))] is None  # 首行无基准
        assert Decimal(str(got[(OTC_TUSHARE_EDGE, date(2026, 12, 2))])) == Decimal("0.0013")
        assert Decimal("0.0013") == quantize_nav(Decimal("0.00125"))

    def test_zero_baseline_does_not_raise_division_error(self, migration_conn):
        """errno 1365 回归：MySQL 对含窗口函数的派生表不保证「先过滤后求值」。

        基准为 0 的行若先被算进 SELECT 列表，整条 UPDATE 直接失败、部署被拖垮；
        SQLite 的除零返回 NULL，本地全量绿也发现不了，故只在 MySQL 具名钉住。
        """
        self._mysql_only(migration_conn)
        _run(migration.upgrade, migration_conn)
        got = _pcts(migration_conn)
        assert got[(OTC_TUSHARE_ZERO, date(2026, 9, 30))] is None   # 基准 0，跳过
        assert got[(OTC_TUSHARE_ZERO, date(2026, 10, 1))] is not None  # 同批其他行照写

    def test_out_of_range_value_does_not_abort_the_migration(self, migration_conn):
        """越界守卫的必要性：MySQL 严格模式下 errno 1264 会让整条 UPDATE 失败、拖垮部署。

        断言的是「越界行留 NULL 且**同批其他行照常写入**」——只看越界行为 NULL 的话，
        整条语句被拒也会通过。
        """
        self._mysql_only(migration_conn)
        _run(migration.upgrade, migration_conn)
        got = _pcts(migration_conn)
        assert got[(OTC_AKSHARE, date(2026, 10, 1))] is None
        assert got[(OTC_AKSHARE, date(2026, 9, 30))] is not None
        assert got[(HK, date(2026, 9, 30))] is not None
