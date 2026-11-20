# ============================================================================
# 单元测试：产品删除判据的依赖表名单守门（issue #906，同构 #820）
# ============================================================================
# `_count_product_dependents` 的 FK 表集合按元数据派生而**不手写清单**：新增
# 一张引用 product(code, market) 的表会自动进入删除判据（正确），但也静默改变
# 契约（评审需要看见）。本文件把当前快照钉成断言：集合变化即翻红。
#
# 与 portfolio 侧（test_portfolio_delete_dependents.py）的两点差异：
# 1. 产品主键是复合键 (code, market)，判据按**整条约束**逐列等值过滤——
#    「单列外键」前提断言在这里反转成「复合约束完整性」断言。
# 2. 无 FK 的 product_code 列不是全豁免：manual_market_value 显式补查计入
#    判据（SPEC §3），nav_sync_detail 显式豁免（同步日志，见下）。
#
# 不做的事：不重复 test_product_delete.py 的行为断言（有行即拒 / 无引用可删），
# 那份管行为，本份管名单与前提。
# ============================================================================

from app.models.base import Base
from app.models.product import Product
from app.services.product_service import _product_dependent_tables

EXPECTED_DEPENDENT_TABLES = {
    "trade",
    "portfolio_position",
    "share_change_event",
    "price_record",
    "dividend_method_config",
}

# 有 product_code 列但**无外键**、且**不计入**删除判据的表：逐个显式豁免并写明
# 理由（#820 同口径）。新增此类表必须改这里，强迫评审看见决定。
NON_FK_PRODUCT_CODE_EXEMPT_TABLES: dict[str, str] = {
    "nav_sync_detail": (
        "净值同步明细日志（observability，append-only）：记录每次同步尝试的"
        "产品码/日期/结果，读侧不参与估值与账务聚合，悬空产品码不会造成静默"
        "错值。与 audit_log 保留已删资源 resource_id 同语义——历史日志不因"
        "资源删除而改写或阻塞删除。#906 SPEC §3 只列了 manual_market_value，"
        "本表是实施期元数据盘点新发现，按日志语义豁免并在 PR 说明披露。"
    ),
}

# 无 FK 但**显式补查计入**判据的表（SPEC §3：现金重估台账是账务事实）
NON_FK_COUNTED_TABLES = {"manual_market_value"}


def _derived_tables():
    return {table.name for table, _pairs in _product_dependent_tables()}


class TestDependentTableSet:
    def test_derived_set_matches_current_schema_snapshot(self):
        assert _derived_tables() == EXPECTED_DEPENDENT_TABLES

    def test_product_itself_is_not_treated_as_dependent(self):
        assert Product.__tablename__ not in _derived_tables()

    def test_every_entry_is_a_full_composite_fk_to_product_key(self):
        """判据按整条约束逐列等值过滤，约束必须完整覆盖 (code, market)。

        半截约束（只引用 code 或 market 之一）会让计数偏大或偏小——真出现时
        本断言翻红，要求显式决定判据口径，而不是让删除判据静默退化。
        """
        for table, pairs in _product_dependent_tables():
            referred = {name for _column, name in pairs}
            assert referred == {"code", "market"}, (
                f"{table.name} 的外键未完整覆盖产品复合主键: {referred}"
            )
            local = {column.name for column, _name in pairs}
            assert local == {"product_code", "market"}, (
                f"{table.name} 的本侧引用列异常: {local}"
            )

    def test_referenced_columns_are_the_product_primary_key(self):
        for table, pairs in _product_dependent_tables():
            for column, _name in pairs:
                for fk in column.foreign_keys:
                    assert fk.column.table.name == Product.__tablename__
                    assert fk.column.primary_key is True


class TestNonForeignKeyReferences:
    """元数据派生只看外键；带 product_code 列而无 FK 的表逐表决定（计入/豁免）。"""

    def _tables_with_product_code_column(self) -> set:
        return {
            table.name
            for table in Base.metadata.tables.values()
            if "product_code" in table.columns
        }

    def test_all_product_code_tables_are_fk_counted_or_explained(self):
        derived = _derived_tables()
        unexplained = (
            self._tables_with_product_code_column()
            - derived
            - set(NON_FK_PRODUCT_CODE_EXEMPT_TABLES)
            - NON_FK_COUNTED_TABLES
        )
        assert unexplained == set(), (
            f"新表带 product_code 列却无外键且未登记：{sorted(unexplained)}；"
            "删除判据对它失明，须显式决定计入判据（如 manual_market_value）"
            "或登记豁免理由（如 nav_sync_detail）"
        )

    def test_exempted_tables_truly_have_no_fk_to_product(self):
        for name in NON_FK_PRODUCT_CODE_EXEMPT_TABLES:
            table = Base.metadata.tables[name]
            assert not any(
                fk.column.table.name == Product.__tablename__
                for fk in table.foreign_keys
            ), f"{name} 已有指向 product 的外键，豁免条目应删除"

    def test_counted_non_fk_table_truly_has_no_fk_to_product(self):
        # manual_market_value 若将来补了 FK，会同时进元数据派生集合——
        # 显式补查仍在（幂等），但本断言提醒把 NON_FK_COUNTED_TABLES 收敛掉
        table = Base.metadata.tables["manual_market_value"]
        assert not any(
            fk.column.table.name == Product.__tablename__
            for fk in table.foreign_keys
        )

    def test_no_overlap_between_exempt_and_counted(self):
        assert set(NON_FK_PRODUCT_CODE_EXEMPT_TABLES) & NON_FK_COUNTED_TABLES == set()