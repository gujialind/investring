# ============================================================================
# 单元测试：删除判据的依赖表名单守门（issue #820）
# ============================================================================
# `_count_portfolio_dependents` 的表名集合按元数据派生而**不手写清单**，所以
# 「删了会不会留下孤儿行」这件事没有可见的维护点——新增一张引用 portfolio.code
# 的表，删除逻辑会静默把它纳入判据（正确），但也会静默改变契约（评审需要看见）。
# 本文件把当前快照钉成断言：集合变化即翻红，强制作者确认新表是否应阻止删除，
# 同时钉住「计数按单列外键才成立」这一实现前提。
#
# 不做的事：不重复 `test_portfolio_delete.py` 的行为断言（有行即拒 / 不级联），
# 那份管行为，本份管名单与前提。
# ============================================================================

from app.models.base import Base
from app.models.portfolio import Portfolio
from app.services.portfolio_service import _portfolio_dependent_tables

EXPECTED_DEPENDENT_TABLES = {
    "investor_holding",
    "manual_market_value",
    "portfolio_position",
    "portfolio_value_snapshot",
    "share_change_event",
    "subscription",
    "trade",
}

# 有 portfolio_code 列但**无外键**的表：元数据派生的删除判据对它们失明，
# 逐个显式豁免并写明理由（L2 Suggestion 1，#820）。新增此类表必须改这里，
# 强迫评审看见「新表引用组合但不阻止删除」这一决定。
NON_FK_PORTFOLIO_CODE_TABLES: dict[str, str] = {
    "idempotency_cache": (
        "全仓无生产读写点（仅模型定义）；主键 key 为请求键，与 portfolio_code "
        "无唯一绑定。删除组合后残留行不会阻碍同 code 重建——重建后同 key 请求"
        "是否误命中旧缓存属幂等机制自身的失效问题，不归删除判据兜底。若将来"
        "接入真实写路径，须重新评估是否纳入判据或补清理策略。"
    ),
}


def _derived_tables():
    return {table.name for table, _column, _constraint in _portfolio_dependent_tables()}


class TestDependentTableSet:
    def test_derived_set_matches_current_schema_snapshot(self):
        assert _derived_tables() == EXPECTED_DEPENDENT_TABLES

    def test_portfolio_itself_is_not_treated_as_dependent(self):
        # 否则「计数全为 0」恒假，任何组合都删不掉
        assert Portfolio.__tablename__ not in _derived_tables()

    def test_every_entry_is_a_single_column_fk_to_portfolio_code(self):
        """计数按 `fk.parent == code` 做等值比较，只对单列外键成立。

        复合外键（如 (portfolio_code, product_code)）会让该等值条件只约束一半，
        计数偏大或偏小都可能——真出现时本断言翻红，要求改成按整条约束过滤，
        而不是让删除判据静默退化。
        """
        for table, column, constraint in _portfolio_dependent_tables():
            assert len(constraint.columns) == 1, f"{table.name} 的外键是复合约束"
            assert column.name == "portfolio_code", f"{table.name} 引用列名异常"

    def test_referenced_column_is_the_portfolio_primary_key(self):
        for _table, _column, constraint in _portfolio_dependent_tables():
            for fk in constraint.elements:
                assert fk.column.table.name == Portfolio.__tablename__
                assert fk.column.primary_key is True


class TestNonForeignKeyReferences:
    """元数据派生只看外键；带 portfolio_code 列但无 FK 的表逐表豁免（#820 S2）。"""

    def _tables_with_portfolio_code_column(self) -> set:
        return {
            table.name
            for table in Base.metadata.tables.values()
            if "portfolio_code" in table.columns
        }

    def test_all_portfolio_code_tables_are_fk_or_exlicitly_exempted(self):
        derived = _derived_tables()
        unexplained = (
            self._tables_with_portfolio_code_column()
            - derived
            - set(NON_FK_PORTFOLIO_CODE_TABLES)
        )
        assert unexplained == set(), (
            f"新表带 portfolio_code 列却无外键且未豁免：{sorted(unexplained)}；"
            "删除判据对它失明，须显式决定纳入判据或登记豁免理由"
        )

    def test_exemption_list_has_no_overlap_with_fk_derived_set(self):
        # 若将来给豁免表补了 FK，应删除豁免条目而不是留着掩盖口径漂移
        assert set(NON_FK_PORTFOLIO_CODE_TABLES) & _derived_tables() == set()

    def test_exempted_tables_truly_have_no_fk_to_portfolio(self):
        for name in NON_FK_PORTFOLIO_CODE_TABLES:
            table = Base.metadata.tables[name]
            assert not any(
                fk.column.table.name == Portfolio.__tablename__
                for fk in table.foreign_keys
            ), f"{name} 已有指向 portfolio.code 的外键，豁免条目应删除"
