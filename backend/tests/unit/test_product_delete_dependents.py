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

# 无 FK 产品码**列**（列级普查，含 *_product_code，L2 r5）且**不计入**删除判据：
# 逐个显式豁免并写明理由（#820 同口径）。新增此类列必须改这里，强迫评审看见决定。
NON_FK_PRODUCT_CODE_EXEMPT_COLUMNS: dict[tuple[str, str], str] = {
    ("nav_sync_detail", "product_code"): (
        "净值同步明细日志（observability，append-only）：记录每次同步尝试的"
        "产品码/日期/结果，读侧不参与估值与账务聚合，悬空产品码不会造成静默"
        "错值。与 audit_log 保留已删资源 resource_id 同语义——历史日志不因"
        "资源删除而改写或阻塞删除。#906 SPEC §3 只列了 manual_market_value，"
        "本表是实施期元数据盘点新发现，按日志语义豁免并在 PR 说明披露。"
    ),
}

# 无 FK 但**显式补查计入**判据的列（账务事实/业务引用，非纯日志）：
# manual_market_value=现金重估台账（SPEC §3）；share_change_event.cash_product_code
# =分红现金腿业务引用（L2 r5，账务实体上的产品码，同 manual_market_value 类计入）。
NON_FK_COUNTED_COLUMNS: dict[tuple[str, str], str] = {
    ("manual_market_value", "product_code"): "现金重估台账，账务事实（SPEC §3）",
    ("share_change_event", "cash_product_code"): (
        "分红现金腿业务引用（账务实体非纯日志），计入判据；仅统计只经该列引用"
        "的行，复合 FK 已计过的行不双计（L2 r5）"
    ),
}


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

    def test_every_dependent_table_has_exactly_one_product_fk_constraint(self):
        """派生实现按表取**首条**指向 product 的约束（break）；本断言把「每表
        恰一条」钉成前提（L2 r2）：已列名单的表若新增第二条产品引用约束，
        仅经第二约束引用的行会对删除判据失明——前提失效即翻红，要求改成
        逐约束 OR 聚合，而不是让判据静默退化。
        """
        for table, _pairs in _product_dependent_tables():
            product_constraints = [
                c for c in table.foreign_key_constraints
                if c.elements and all(
                    fk.column.table.name == Product.__tablename__
                    for fk in c.elements
                )
            ]
            assert len(product_constraints) == 1, (
                f"{table.name} 有 {len(product_constraints)} 条指向 product 的外键约束，"
                "单约束前提失效，_product_dependent_tables 须改逐约束 OR 聚合"
            )


class TestNonForeignKeyReferences:
    """元数据派生只看外键；无 FK 的产品码**列**（列级普查，含 *_product_code）
    逐列决定（计入/豁免）。列级而非表级：share_change_event 已经复合 FK 进派生
    集合，但其 cash_product_code 列无 FK，表级普查会漏（L2 r5）。"""

    def _non_fk_product_code_columns(self) -> set:
        out = set()
        for table in Base.metadata.tables.values():
            for column in table.columns:
                if not (column.name == "product_code" or column.name.endswith("_product_code")):
                    continue
                if any(
                    fk.column.table.name == Product.__tablename__
                    for fk in column.foreign_keys
                ):
                    continue  # 有 FK → 已由元数据派生覆盖
                out.add((table.name, column.name))
        return out

    def test_all_non_fk_product_code_columns_are_counted_or_exempt(self):
        accounted = set(NON_FK_COUNTED_COLUMNS) | set(NON_FK_PRODUCT_CODE_EXEMPT_COLUMNS)
        unexplained = self._non_fk_product_code_columns() - accounted
        assert unexplained == set(), (
            f"新无 FK 产品码列未登记：{sorted(unexplained)}；删除判据对它失明，"
            "须显式决定计入判据（如 manual_market_value / cash_product_code）"
            "或登记豁免理由（如 nav_sync_detail）"
        )

    def test_registered_columns_truly_have_no_fk_to_product(self):
        for table_name, column_name in (
            set(NON_FK_COUNTED_COLUMNS) | set(NON_FK_PRODUCT_CODE_EXEMPT_COLUMNS)
        ):
            column = Base.metadata.tables[table_name].columns[column_name]
            assert not any(
                fk.column.table.name == Product.__tablename__
                for fk in column.foreign_keys
            ), f"{table_name}.{column_name} 已有指向 product 的外键，登记条目应删除"

    def test_no_overlap_between_exempt_and_counted(self):
        assert set(NON_FK_PRODUCT_CODE_EXEMPT_COLUMNS) & set(NON_FK_COUNTED_COLUMNS) == set()