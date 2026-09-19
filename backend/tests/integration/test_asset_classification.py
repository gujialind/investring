# ============================================================================
# 集成测试：资产分类正交维度字典 + 迁移 0008（issue #128）
# ============================================================================
# 覆盖：
# - 测试种子维度字典与 ASSET_DIMENSIONS 一致（旧扁平码不存在）
# - 迁移 0008 upgrade：asset_classification 加列 + 维度值插入 + product 回填
#   （逐产品判定表优先 / 旧码兜底）+ 旧行清退 + portfolio_position 删 asset_type
# - 维度适用矩阵校验：股票必填 region/style/size、商品/现金维度 NULL
# - 迁移幂等：重复 upgrade 结果不变
# - 校验失败中止：存量违例产品时 raise，且旧分类行未被删除（不留半成品）
# - downgrade 不可逆（NotImplementedError）
# ============================================================================

import importlib.util
import os

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from app.constants.asset_dimensions import (
    ASSET_DIMENSIONS,
    DIMENSION_APPLICABILITY,
    DIMENSION_RULES,
    PRODUCT_DIMENSIONS,
    RULE_DIMENSIONS,
    RULES,
)
from app.models.asset_classification import (
    AssetClassification,
    AssetClassDimensionRule,
    AssetDimensionApplicability,
)


MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "alembic", "versions", "0008_asset_dimension_refactor.py",
)

_OLD_CODES = [
    "STOCK_CN_LARGE", "STOCK_CN_SMALL", "STOCK_CN_VALUE", "STOCK_CN_GROWTH",
    "STOCK_CN_MIXED", "STOCK_HK_LARGE", "STOCK_HK_SMALL", "STOCK_US",
    "STOCK_EU", "STOCK_JP", "STOCK_GLOBAL", "BOND_SHORT", "BOND_LONG",
    "BOND_MIXED", "BOND_US", "BOND_GLOBAL", "GOLD", "CASH",
]


def _load_migration():
    """按文件路径加载迁移 0008 模块（文件名以数字开头，无法常规 import）"""
    spec = importlib.util.spec_from_file_location("migration_0008", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _create_old_schema(engine, products):
    """构造 0008 之前的旧版三表（仅迁移涉及的列）并插入旧种子与产品"""
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE asset_classification ("
            " code VARCHAR(30) PRIMARY KEY,"
            " asset_type VARCHAR(20) NOT NULL,"
            " asset_category VARCHAR(50) NOT NULL,"
            " asset_subcat VARCHAR(50),"
            " asset_name VARCHAR(50),"
            " description TEXT"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE TABLE product ("
            " code VARCHAR(20) PRIMARY KEY,"
            " market VARCHAR(20),"
            " name VARCHAR(100) NOT NULL,"
            " product_type VARCHAR(20) NOT NULL,"
            " asset_class_code VARCHAR(30),"
            " confirm_days INTEGER,"
            " is_qdii BOOLEAN"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE TABLE portfolio_position ("
            " id INTEGER PRIMARY KEY,"
            " portfolio_code VARCHAR(20) NOT NULL,"
            " product_code VARCHAR(20) NOT NULL,"
            " market VARCHAR(20) NOT NULL,"
            " asset_type VARCHAR(20),"
            " snapshot_date DATE NOT NULL"
            ")"
        ))
        for code in _OLD_CODES:
            conn.execute(
                sa.text(
                    "INSERT INTO asset_classification (code, asset_type, asset_category)"
                    " VALUES (:code, '股票', '测试')"
                ).bindparams(code=code)
            )
        for code, old_class in products:
            conn.execute(
                sa.text(
                    "INSERT INTO product (code, market, name, product_type, asset_class_code)"
                    " VALUES (:code, 'CN_OTC', '测试产品', 'OEF', :old_class)"
                ).bindparams(code=code, old_class=old_class)
            )
        conn.execute(sa.text(
            "INSERT INTO portfolio_position"
            " (id, portfolio_code, product_code, market, asset_type, snapshot_date)"
            " VALUES (1, 'P1', 'CASH', '', 'cash', '2025-11-03')"
        ))


def _run_upgrade(engine, migration):
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            migration.upgrade()


def _read_product_dims(engine):
    with engine.connect() as conn:
        result = conn.execute(sa.text(
            "SELECT code, asset_class_code, region_code, style_code, size_code, segment_code"
            " FROM product"
        ))
        return {r[0]: r[1:] for r in result}


_SAMPLE_PRODUCTS = [
    ("510300.SH", "STOCK_CN_LARGE"),   # 不在逐产品判定表 → 旧码兜底
    ("513050.SH", "STOCK_GLOBAL"),     # 判定表：中概互联 → 中国/互联网
    ("518880.SH", "GOLD"),             # 判定表：黄金 → 商品
    ("CASH", "CASH"),                  # 判定表：现金
    ("IN_TRANSIT_BUY", None),          # 无分类：不迁移、保持全 NULL
]


class TestDimensionSeed:
    """测试种子（conftest._seed_base_data）与 ASSET_DIMENSIONS 一致"""

    def test_seed_dimensions_match_constants(self, test_db):
        rows = test_db.query(AssetClassification).all()
        by_code = {row.code: row for row in rows}
        assert len(by_code) == len(ASSET_DIMENSIONS)
        for code, dimension, name, sort_order, _ in ASSET_DIMENSIONS:
            row = by_code[code]
            assert row.dimension == dimension
            assert row.name == name
            assert row.sort_order == sort_order
        # 旧扁平码（含僵尸分类）不存在
        for old in _OLD_CODES:
            assert old not in by_code


class TestMigration0008:
    """迁移 0008：维度字典 + 回填 + 校验 + 删列（SQLite 方言）"""

    def test_upgrade_full_flow(self):
        migration = _load_migration()
        engine = create_engine("sqlite:///:memory:")
        _create_old_schema(engine, _SAMPLE_PRODUCTS)

        _run_upgrade(engine, migration)

        # asset_classification：新列存在、旧列删除
        ac_cols = {c["name"] for c in sa.inspect(engine).get_columns("asset_classification")}
        assert {"dimension", "name", "sort_order"} <= ac_cols
        assert not {"asset_type", "asset_category", "asset_subcat", "asset_name"} & ac_cols

        # 维度值字典完整、旧行清退
        with engine.connect() as conn:
            codes = {r[0] for r in conn.execute(sa.text("SELECT code FROM asset_classification"))}
        assert codes == {code for code, *_ in ASSET_DIMENSIONS}
        assert "STOCK_EU" not in codes and "STOCK_HK_SMALL" not in codes

        # product 回填：兜底 vs 逐产品判定
        dims = _read_product_dims(engine)
        assert dims["510300.SH"] == (
            "ASSET_STOCK", "REGION_CN", "STYLE_BALANCED", "SIZE_LARGE", "SEG_COMPOSITE")
        assert dims["513050.SH"] == (
            "ASSET_STOCK", "REGION_CN", "STYLE_GROWTH", "SIZE_LARGE", "SEG_INTERNET")
        assert dims["518880.SH"] == ("ASSET_COMMODITY", None, None, None, "SEG_GOLD")
        assert dims["CASH"] == ("ASSET_CASH", None, None, None, None)
        assert dims["IN_TRANSIT_BUY"] == (None, None, None, None, None)

        # portfolio_position.asset_type 已删除
        pp_cols = {c["name"] for c in sa.inspect(engine).get_columns("portfolio_position")}
        assert "asset_type" not in pp_cols

    def test_upgrade_idempotent(self):
        """重复执行 upgrade 结果不变"""
        migration = _load_migration()
        engine = create_engine("sqlite:///:memory:")
        _create_old_schema(engine, _SAMPLE_PRODUCTS)

        _run_upgrade(engine, migration)
        first = _read_product_dims(engine)
        _run_upgrade(engine, migration)
        second = _read_product_dims(engine)

        assert first == second
        with engine.connect() as conn:
            count = conn.execute(sa.text("SELECT COUNT(*) FROM asset_classification")).scalar()
        assert count == len(ASSET_DIMENSIONS)

    def test_validation_failure_aborts_before_delete(self):
        """存量违例产品（股票缺 region）→ raise 且旧分类行未被删除"""
        migration = _load_migration()
        engine = create_engine("sqlite:///:memory:")
        _create_old_schema(engine, _SAMPLE_PRODUCTS)
        with engine.begin() as conn:
            conn.execute(sa.text(
                "INSERT INTO product (code, market, name, product_type, asset_class_code)"
                " VALUES ('BROKEN.OF', 'CN_OTC', '违例产品', 'OEF', 'ASSET_STOCK')"
            ))

        with pytest.raises(RuntimeError, match="回填校验失败"):
            _run_upgrade(engine, migration)

        # 中止点在所有破坏性操作之前：旧分类行与旧列仍在
        with engine.connect() as conn:
            old_count = conn.execute(
                sa.text("SELECT COUNT(*) FROM asset_classification WHERE code IN ("
                        + ",".join(f"'{c}'" for c in _OLD_CODES) + ")")
            ).scalar()
        assert old_count == len(_OLD_CODES)
        ac_cols = {c["name"] for c in sa.inspect(engine).get_columns("asset_classification")}
        assert "asset_type" in ac_cols

    def test_downgrade_not_supported(self):
        migration = _load_migration()
        with pytest.raises(NotImplementedError):
            migration.downgrade()


class TestProductDimensionMap:
    """逐产品判定表自洽性（验收断言：124 只全覆盖、维度适用矩阵、点名判定）"""

    def test_all_dimensions_reference_valid_values(self):
        valid = {code for code, *_ in ASSET_DIMENSIONS}
        for code, dims in PRODUCT_DIMENSIONS.items():
            for dim in dims:
                assert dim is None or dim in valid, f"{code} 引用不存在维度值 {dim}"

    def test_applicability_matrix(self):
        for code, (asset, region, style, size, segment) in PRODUCT_DIMENSIONS.items():
            if asset == "ASSET_STOCK":
                assert region and style and size, f"{code} 股票缺 region/style/size"
            elif asset == "ASSET_BOND":
                assert region and segment, f"{code} 债券缺 region/segment"
                assert style is None and size is None
            elif asset in ("ASSET_COMMODITY", "ASSET_CASH"):
                assert region is None and style is None and size is None

    def test_named_judgments(self):
        """中概互联 3 只→中国；广发全球医疗→全球；标普油气→美国；黄金→商品"""
        assert PRODUCT_DIMENSIONS["513050.SH"][1] == "REGION_CN"
        assert PRODUCT_DIMENSIONS["164906.SZ"][1] == "REGION_CN"
        assert PRODUCT_DIMENSIONS["006327.OF"][1] == "REGION_CN"
        assert PRODUCT_DIMENSIONS["000369.OF"][1] == "REGION_GLOBAL"
        assert PRODUCT_DIMENSIONS["162411.SZ"][1] == "REGION_US"
        assert PRODUCT_DIMENSIONS["518880.SH"][0] == "ASSET_COMMODITY"
        assert PRODUCT_DIMENSIONS["518880.SH"][4] == "SEG_GOLD"
        # 用户确认的 style 判定
        for code in ("519062.OF", "270002.OF", "519697.OF"):
            assert PRODUCT_DIMENSIONS[code][2] == "STYLE_BALANCED"
        assert PRODUCT_DIMENSIONS["512400.SH"][2] == "STYLE_VALUE"


# ============================================================================
# issue #135：矩阵落库（asset_class_dimension_rule）+ 值级适用关联
# （asset_dimension_applicability）+ is_active 软失效
# ============================================================================

MIGRATION_0009_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..",
    "alembic", "versions", "0009_dimension_rules_and_applicability.py",
)


def _load_migration_0009():
    spec = importlib.util.spec_from_file_location("migration_0009", MIGRATION_0009_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestApplicabilityConstants:
    """常量自洽性（常驻测试，防常量漂移）"""

    def test_every_non_asset_class_value_has_applicability(self):
        dim_values = {code for code, dim, *_ in ASSET_DIMENSIONS if dim != "asset_class"}
        assert dim_values == set(DIMENSION_APPLICABILITY), (
            f"缺失: {dim_values - set(DIMENSION_APPLICABILITY)}；"
            f"多余: {set(DIMENSION_APPLICABILITY) - dim_values}"
        )

    def test_applicability_targets_are_asset_class(self):
        asset_classes = {code for code, dim, *_ in ASSET_DIMENSIONS if dim == "asset_class"}
        for value, classes in DIMENSION_APPLICABILITY.items():
            assert classes, f"{value} 适用大类为空"
            for c in classes:
                assert c in asset_classes, f"{value} 关联了非 asset_class 值 {c}"

    def test_rules_reference_valid_values(self):
        asset_classes = {code for code, dim, *_ in ASSET_DIMENSIONS if dim == "asset_class"}
        for asset_class, rules in DIMENSION_RULES.items():
            assert asset_class in asset_classes
            for dimension, rule in rules.items():
                assert dimension in RULE_DIMENSIONS
                assert rule in RULES

    def test_rules_respect_applicability(self):
        """值关联的大类必须允许该值所属维度（无 nonsense 关联）"""
        dim_of_value = {code: dim for code, dim, *_ in ASSET_DIMENSIONS}
        for value, classes in DIMENSION_APPLICABILITY.items():
            for c in classes:
                assert dim_of_value[value] in DIMENSION_RULES.get(c, {}), (
                    f"{value} 关联 {c}，但 {c} 禁止 {dim_of_value[value]} 维度"
                )

    def test_product_dimensions_pass_value_level(self):
        """存量兼容（验收断言）：PRODUCT_DIMENSIONS 全部通过值级适用校验"""
        for code, (asset, region, style, size, segment) in PRODUCT_DIMENSIONS.items():
            for value in (region, style, size, segment):
                if value is None:
                    continue
                assert asset in DIMENSION_APPLICABILITY[value], (
                    f"{code}: {value} 不适用于 {asset}"
                )


class TestApplicabilitySeed:
    """conftest 种子与常量一致（常驻测试）"""

    def test_applicability_seed_matches_constants(self, test_db):
        rows = test_db.query(AssetDimensionApplicability).all()
        db_pairs = {(r.dimension_value_code, r.asset_class_code) for r in rows}
        expected = {(v, c) for v, classes in DIMENSION_APPLICABILITY.items() for c in classes}
        assert db_pairs == expected

    def test_rules_seed_matches_constants(self, test_db):
        rows = test_db.query(AssetClassDimensionRule).all()
        db_rules = {(r.asset_class_code, r.dimension): r.rule for r in rows}
        expected = {
            (c, d): rule for c, rules in DIMENSION_RULES.items() for d, rule in rules.items()
        }
        assert db_rules == expected

    def test_classification_seed_is_active_default(self, test_db):
        rows = test_db.query(AssetClassification).all()
        assert all(r.is_active for r in rows)


def _create_pre0009_schema(engine, products):
    """构造 0009 之前的结构（0008 完成态，仅相关列）并插入字典与产品"""
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE asset_classification ("
            " code VARCHAR(30) PRIMARY KEY,"
            " dimension VARCHAR(20) NOT NULL,"
            " name VARCHAR(50) NOT NULL,"
            " sort_order INTEGER,"
            " description TEXT"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE TABLE product ("
            " code VARCHAR(20) PRIMARY KEY,"
            " market VARCHAR(20),"
            " name VARCHAR(100) NOT NULL,"
            " product_type VARCHAR(20) NOT NULL,"
            " asset_class_code VARCHAR(30),"
            " region_code VARCHAR(30),"
            " style_code VARCHAR(30),"
            " size_code VARCHAR(30),"
            " segment_code VARCHAR(30)"
            ")"
        ))
        for code, dimension, name, sort_order, description in ASSET_DIMENSIONS:
            conn.execute(
                sa.text(
                    "INSERT INTO asset_classification (code, dimension, name, sort_order)"
                    " VALUES (:code, :dimension, :name, :sort_order)"
                ).bindparams(
                    code=code, dimension=dimension, name=name, sort_order=sort_order,
                )
            )
        for code, dims in products:
            asset, region, style, size, segment = dims
            conn.execute(
                sa.text(
                    "INSERT INTO product (code, market, name, product_type,"
                    " asset_class_code, region_code, style_code, size_code, segment_code)"
                    " VALUES (:code, 'CN_OTC', '测试产品', 'OEF',"
                    " :asset, :region, :style, :size, :segment)"
                ).bindparams(
                    code=code, asset=asset, region=region,
                    style=style, size=size, segment=segment,
                )
            )


_SAMPLE_PRODUCTS_0009 = [
    ("510300.SH", ("ASSET_STOCK", "REGION_CN", "STYLE_BALANCED", "SIZE_LARGE", "SEG_COMPOSITE")),
    ("518880.SH", ("ASSET_COMMODITY", None, None, None, "SEG_GOLD")),
    ("007823.OF", ("ASSET_BOND", "REGION_CN", None, None, "SEG_BOND_SHORT")),
    ("CASH", ("ASSET_CASH", None, None, None, None)),
    ("IN_TRANSIT_BUY", (None, None, None, None, None)),
]


class TestMigration0009:
    """迁移 0009：两表 + is_active + 回填 + 校验（SQLite 方言）"""

    def test_upgrade_full_flow(self):
        migration = _load_migration_0009()
        engine = create_engine("sqlite:///:memory:")
        _create_pre0009_schema(engine, _SAMPLE_PRODUCTS_0009)

        _run_upgrade(engine, migration)

        # is_active 列存在且存量回填为 active
        ac_cols = {c["name"] for c in sa.inspect(engine).get_columns("asset_classification")}
        assert "is_active" in ac_cols
        with engine.connect() as conn:
            inactive = conn.execute(sa.text(
                "SELECT COUNT(*) FROM asset_classification WHERE is_active = 0"
            )).scalar()
            app_rows = conn.execute(sa.text(
                "SELECT dimension_value_code, asset_class_code FROM asset_dimension_applicability"
            )).fetchall()
            rule_rows = conn.execute(sa.text(
                "SELECT asset_class_code, dimension, rule FROM asset_class_dimension_rule"
            )).fetchall()
        assert inactive == 0
        assert {(r[0], r[1]) for r in app_rows} == {
            (v, c) for v, classes in DIMENSION_APPLICABILITY.items() for c in classes
        }
        assert {(r[0], r[1]): r[2] for r in rule_rows} == {
            (c, d): rule for c, rules in DIMENSION_RULES.items() for d, rule in rules.items()
        }

    def test_upgrade_idempotent(self):
        migration = _load_migration_0009()
        engine = create_engine("sqlite:///:memory:")
        _create_pre0009_schema(engine, _SAMPLE_PRODUCTS_0009)

        _run_upgrade(engine, migration)
        _run_upgrade(engine, migration)

        with engine.connect() as conn:
            app_count = conn.execute(sa.text(
                "SELECT COUNT(*) FROM asset_dimension_applicability"
            )).scalar()
            rule_count = conn.execute(sa.text(
                "SELECT COUNT(*) FROM asset_class_dimension_rule"
            )).scalar()
        assert app_count == sum(len(c) for c in DIMENSION_APPLICABILITY.values())
        assert rule_count == sum(len(r) for r in DIMENSION_RULES.values())

    def test_value_level_violation_aborts(self):
        """存量产品值级违例（股票挂 SEG_GOLD）→ raise 中止"""
        migration = _load_migration_0009()
        engine = create_engine("sqlite:///:memory:")
        bad = _SAMPLE_PRODUCTS_0009 + [
            ("BROKEN.OF", ("ASSET_STOCK", "REGION_CN", "STYLE_BALANCED", "SIZE_LARGE", "SEG_GOLD")),
        ]
        _create_pre0009_schema(engine, bad)

        with pytest.raises(RuntimeError, match="0009"):
            _run_upgrade(engine, migration)

    def test_downgrade(self):
        migration = _load_migration_0009()
        engine = create_engine("sqlite:///:memory:")
        _create_pre0009_schema(engine, _SAMPLE_PRODUCTS_0009)
        _run_upgrade(engine, migration)

        with engine.begin() as conn:
            ctx = MigrationContext.configure(conn)
            with Operations.context(ctx):
                migration.downgrade()

        tables = set(sa.inspect(engine).get_table_names())
        assert "asset_dimension_applicability" not in tables
        assert "asset_class_dimension_rule" not in tables
        ac_cols = {c["name"] for c in sa.inspect(engine).get_columns("asset_classification")}
        assert "is_active" not in ac_cols


# ============================================================================
# issue #135 PR-2：字典管理 API（POST/PUT/GET 单条）
# ============================================================================
from tests.factories import create_product  # noqa: E402

_API = "/api/asset-classifications"


class TestClassificationGetOne:
    def test_get_not_found_404(self, client, admin_headers):
        resp = client.get(f"{_API}/NOPE_XX", headers=admin_headers)
        assert resp.status_code == 404

    def test_get_asset_class_returns_rules(self, client, admin_headers):
        resp = client.get(f"{_API}/ASSET_STOCK", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["dimension_rules"] == {
            "region": "required", "style": "required",
            "size": "required", "segment": "optional",
        }
        assert data["applicable_asset_classes"] == []

    def test_get_value_returns_applicability(self, client, admin_headers):
        resp = client.get(f"{_API}/SEG_GOLD", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["applicable_asset_classes"] == ["ASSET_COMMODITY"]
        assert data["dimension_rules"] == {}


class TestClassificationCreate:
    def test_create_region_value_ok(self, client, admin_headers):
        resp = client.post(_API, headers=admin_headers, json={
            "code": "REGION_ANT", "dimension": "region", "name": "南极洲",
            "sort_order": 7, "applicable_asset_classes": ["ASSET_STOCK", "ASSET_BOND"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_active"] is True
        assert data["applicable_asset_classes"] == ["ASSET_STOCK", "ASSET_BOND"]
        # 列表端可见
        items = {i["code"]: i for i in client.get(
            _API, headers=admin_headers).json()["items"]}
        assert items["REGION_ANT"]["name"] == "南极洲"

    def test_create_asset_class_with_rules_ok(self, client, admin_headers):
        """新建大类带规则：运行期闭环可用（无需发版）"""
        resp = client.post(_API, headers=admin_headers, json={
            "code": "ASSET_REIT", "dimension": "asset_class", "name": "REITs",
            "dimension_rules": {"region": "required", "segment": "optional"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["dimension_rules"] == {"region": "required", "segment": "optional"}
        assert data["applicable_asset_classes"] == []

    def test_create_asset_class_default_cash_like(self, client, admin_headers):
        """新建大类不带规则 = 现金型全 forbidden（无规则行）"""
        resp = client.post(_API, headers=admin_headers, json={
            "code": "ASSET_ALTERNATIVE", "dimension": "asset_class", "name": "另类",
        })
        assert resp.status_code == 200
        assert resp.json()["dimension_rules"] == {}

    def test_create_prefix_mismatch_422(self, client, admin_headers):
        resp = client.post(_API, headers=admin_headers, json={
            "code": "REGION_XX", "dimension": "style", "name": "错前缀",
            "applicable_asset_classes": ["ASSET_STOCK"],
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"

    def test_create_lowercase_code_422(self, client, admin_headers):
        resp = client.post(_API, headers=admin_headers, json={
            "code": "REGION_jp", "dimension": "region", "name": "小写",
            "applicable_asset_classes": ["ASSET_STOCK"],
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"

    def test_create_invalid_dimension_422(self, client, admin_headers):
        resp = client.post(_API, headers=admin_headers, json={
            "code": "ASSET_X", "dimension": "country", "name": "坏维度",
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"

    def test_create_duplicate_400(self, client, admin_headers):
        resp = client.post(_API, headers=admin_headers, json={
            "code": "REGION_CN", "dimension": "region", "name": "重复",
            "applicable_asset_classes": ["ASSET_STOCK"],
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"] == "ALREADY_EXISTS"

    def test_create_non_asset_class_requires_applicable_422(self, client, admin_headers):
        resp = client.post(_API, headers=admin_headers, json={
            "code": "REGION_ANT", "dimension": "region", "name": "南极洲",
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"

    def test_create_applicable_target_not_asset_class_422(self, client, admin_headers):
        resp = client.post(_API, headers=admin_headers, json={
            "code": "REGION_ANT", "dimension": "region", "name": "南极洲",
            "applicable_asset_classes": ["REGION_CN"],
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"

    def test_create_nonsense_applicability_422(self, client, admin_headers):
        """segment 值挂到全禁 segment 的大类（ASSET_CASH 无规则行）→ 拒绝"""
        resp = client.post(_API, headers=admin_headers, json={
            "code": "SEG_WHISKY", "dimension": "segment", "name": "威士忌",
            "applicable_asset_classes": ["ASSET_CASH"],
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"

    def test_create_asset_class_with_applicable_422(self, client, admin_headers):
        resp = client.post(_API, headers=admin_headers, json={
            "code": "ASSET_REIT", "dimension": "asset_class", "name": "REITs",
            "applicable_asset_classes": ["ASSET_STOCK"],
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"

    def test_create_non_asset_class_with_rules_422(self, client, admin_headers):
        resp = client.post(_API, headers=admin_headers, json={
            "code": "REGION_ANT", "dimension": "region", "name": "南极洲",
            "applicable_asset_classes": ["ASSET_STOCK"],
            "dimension_rules": {"region": "required"},
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"

    def test_create_invalid_rule_422(self, client, admin_headers):
        resp = client.post(_API, headers=admin_headers, json={
            "code": "ASSET_REIT", "dimension": "asset_class", "name": "REITs",
            "dimension_rules": {"region": "mandatory"},
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"

    def test_viewer_cannot_create_403(self, client, viewer_headers):
        resp = client.post(_API, headers=viewer_headers, json={
            "code": "REGION_ANT", "dimension": "region", "name": "南极洲",
            "applicable_asset_classes": ["ASSET_STOCK"],
        })
        assert resp.status_code == 403


class TestClassificationUpdate:
    def test_update_scalar_fields_ok(self, client, admin_headers):
        resp = client.put(f"{_API}/STYLE_VALUE", headers=admin_headers, json={
            "name": "价值型", "sort_order": 9, "description": "深度价值", "is_active": False,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "价值型"
        assert data["sort_order"] == 9
        assert data["description"] == "深度价值"
        assert data["is_active"] is False
        # 软失效后列表仍返回该值（消费方自行过滤）
        items = {i["code"]: i for i in client.get(
            _API, headers=admin_headers).json()["items"]}
        assert items["STYLE_VALUE"]["is_active"] is False

    def test_update_explicit_null_rejected(self, client, admin_headers):
        """显式 null 收口（#573）：sort_order 列可空且无 server_default，落 NULL 后响应模型
        500 且该行 GET 恒 500（name/is_active 列 NOT NULL，落 NULL 是 IntegrityError 500）；
        dimension_rules 显式 null 此前静默清空全部规则、applicable_asset_classes null 走
        「保留至少一个」分支——均与「不传 = 不动」矛盾，现统一 INVALID_PARAM"""
        before = client.get(f"{_API}/ASSET_STOCK", headers=admin_headers).json()
        assert before["dimension_rules"], "种子规则非空（防空转）"
        for field in ("name", "sort_order", "is_active",
                      "dimension_rules", "applicable_asset_classes"):
            resp = client.put(f"{_API}/ASSET_STOCK", headers=admin_headers, json={field: None})
            assert resp.status_code == 422, field
            assert resp.json()["detail"]["error"] == "INVALID_PARAM", field
        # 拒绝即零写入：规则与标量字段全部保持原值
        after = client.get(f"{_API}/ASSET_STOCK", headers=admin_headers).json()
        assert after["dimension_rules"] == before["dimension_rules"]
        assert (after["name"], after["sort_order"], after["is_active"]) == (
            before["name"], before["sort_order"], before["is_active"])

    def test_update_description_null_clears(self, client, admin_headers):
        """description 是 allow 例外（列可空 + 响应 Optional）：显式 null = 清除；未传不动"""
        before = client.get(f"{_API}/STYLE_VALUE", headers=admin_headers).json()
        client.put(f"{_API}/STYLE_VALUE", headers=admin_headers,
                   json={"description": "深度价值"})
        resp = client.put(f"{_API}/STYLE_VALUE", headers=admin_headers,
                          json={"description": None})
        assert resp.status_code == 200, resp.json()
        assert resp.json()["description"] is None
        assert resp.json()["name"] == before["name"]

    def test_update_not_found_404(self, client, admin_headers):
        resp = client.put(f"{_API}/NOPE_XX", headers=admin_headers,
                          json={"name": "x"})
        assert resp.status_code == 404

    def test_update_applicability_replace_ok(self, client, admin_headers):
        client.post(_API, headers=admin_headers, json={
            "code": "REGION_ANT", "dimension": "region", "name": "南极洲",
            "applicable_asset_classes": ["ASSET_STOCK", "ASSET_BOND"],
        })
        resp = client.put(f"{_API}/REGION_ANT", headers=admin_headers,
                          json={"applicable_asset_classes": ["ASSET_BOND"]})
        assert resp.status_code == 200
        assert resp.json()["applicable_asset_classes"] == ["ASSET_BOND"]

    def test_update_remove_in_use_applicability_422(self, client, admin_headers):
        """引用保护：种子产品 510300.SH/000300.OF 引用 (REGION_CN, ASSET_STOCK)"""
        resp = client.put(f"{_API}/REGION_CN", headers=admin_headers,
                          json={"applicable_asset_classes": ["ASSET_BOND"]})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "DIMENSION_VALUE_IN_USE"
        assert detail["details"]["asset_class_code"] == "ASSET_STOCK"
        assert sorted(detail["details"]["products"]) == [
            "000300.OF", "161017.OF", "161017.SZ", "510300.SH",
        ]
        # 关联未被改动
        data = client.get(f"{_API}/REGION_CN", headers=admin_headers).json()
        assert data["applicable_asset_classes"] == ["ASSET_STOCK", "ASSET_BOND"]

    def test_update_applicability_to_empty_422(self, client, admin_headers):
        resp = client.put(f"{_API}/REGION_CN", headers=admin_headers,
                          json={"applicable_asset_classes": []})
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"

    def test_update_applicability_nonsense_422(self, client, admin_headers):
        resp = client.put(f"{_API}/STYLE_VALUE", headers=admin_headers,
                          json={"applicable_asset_classes": ["ASSET_COMMODITY"]})
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"

    def test_update_rules_relax_ok(self, client, admin_headers):
        """放宽自由：required → optional 无冲突校验"""
        resp = client.put(f"{_API}/ASSET_STOCK", headers=admin_headers, json={
            "dimension_rules": {
                "region": "required", "style": "required",
                "size": "optional", "segment": "optional",
            },
        })
        assert resp.status_code == 200
        assert resp.json()["dimension_rules"]["size"] == "optional"

    def test_update_rules_tighten_required_conflict_422(self, client, admin_headers, test_db):
        """→required 需存量产品该维度全非空"""
        client.post(_API, headers=admin_headers, json={
            "code": "ASSET_REIT", "dimension": "asset_class", "name": "REITs",
            "dimension_rules": {"segment": "optional"},
        })
        create_product(test_db, code="508000.SH", market="CN_EXCHANGE",
                       asset_class_code="ASSET_REIT", region_code=None,
                       style_code=None, size_code=None, segment_code=None,
                       confirm_days=0)
        resp = client.put(f"{_API}/ASSET_REIT", headers=admin_headers,
                          json={"dimension_rules": {"segment": "required"}})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "DIMENSION_RULE_CONFLICT"
        assert detail["details"]["products"] == ["508000.SH"]

    def test_update_rules_tighten_forbidden_conflict_422(self, client, admin_headers, test_db):
        """→forbidden（删规则行）需存量产品该维度全空"""
        client.post(_API, headers=admin_headers, json={
            "code": "ASSET_REIT", "dimension": "asset_class", "name": "REITs",
            "dimension_rules": {"segment": "optional"},
        })
        create_product(test_db, code="508001.SH", market="CN_EXCHANGE",
                       asset_class_code="ASSET_REIT", region_code=None,
                       style_code=None, size_code=None, segment_code="SEG_GOLD",
                       confirm_days=0)
        resp = client.put(f"{_API}/ASSET_REIT", headers=admin_headers,
                          json={"dimension_rules": {}})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "DIMENSION_RULE_CONFLICT"
        assert detail["details"]["rule"] == "forbidden"
        assert detail["details"]["products"] == ["508001.SH"]

    def test_update_rules_forbidden_keeps_dormant_links(self, client, admin_headers):
        """forbidden 化不级联删值关联（dormant），规则回转自动复活"""
        client.post(_API, headers=admin_headers, json={
            "code": "ASSET_FOF", "dimension": "asset_class", "name": "FOF",
            "dimension_rules": {"segment": "optional"},
        })
        client.post(_API, headers=admin_headers, json={
            "code": "SEG_FOF", "dimension": "segment", "name": "FOF份额",
            "applicable_asset_classes": ["ASSET_FOF"],
        })
        resp = client.put(f"{_API}/ASSET_FOF", headers=admin_headers,
                          json={"dimension_rules": {}})
        assert resp.status_code == 200
        assert resp.json()["dimension_rules"] == {}
        # 关联保留（dormant）
        data = client.get(f"{_API}/SEG_FOF", headers=admin_headers).json()
        assert data["applicable_asset_classes"] == ["ASSET_FOF"]
        # 规则回转后关联自动复活可用
        resp = client.put(f"{_API}/ASSET_FOF", headers=admin_headers,
                          json={"dimension_rules": {"segment": "optional"}})
        assert resp.status_code == 200
        assert resp.json()["dimension_rules"] == {"segment": "optional"}

    def test_update_rules_on_non_asset_class_422(self, client, admin_headers):
        resp = client.put(f"{_API}/REGION_CN", headers=admin_headers,
                          json={"dimension_rules": {"region": "required"}})
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"

    def test_update_applicability_on_asset_class_422(self, client, admin_headers):
        resp = client.put(f"{_API}/ASSET_STOCK", headers=admin_headers,
                          json={"applicable_asset_classes": ["ASSET_BOND"]})
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"

    def test_update_invalid_rule_422(self, client, admin_headers):
        resp = client.put(f"{_API}/ASSET_STOCK", headers=admin_headers,
                          json={"dimension_rules": {"country": "required"}})
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INVALID_CLASSIFICATION"
