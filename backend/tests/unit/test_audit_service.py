# ============================================================================
# 单元测试：issue #405 审计与系统错误日志写入服务 (test_audit_service.py)
# ============================================================================
# - audit_actions 常量宽度：audit_log 列宽是硬约束（action 20 / resource_type 50 /
#   investor_code 20），超宽即在写入时被 DB 拒绝，故在常量层设防
# - _serialize_value / _diff_fields 契约（中文不转义、diff-only、非 JSON 类型字符串化）
# - actor 归属：请求上下文有 actor 则记之，后台执行体落 SYSTEM 哨兵
# - savepoint 隔离：审计 flush 失败不外抛、不回滚业务改动、不落审计行，
#   并留 stdout ERROR + system_error_log 双痕迹
# - record_system_error：独立 session 写入 + best-effort（写失败只记 stdout）
# ============================================================================

import json
import logging
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.constants import audit_actions
from app.constants.audit_actions import (
    ACTION_CREATE,
    ACTION_UPDATE,
    RESOURCE_TRADE,
    SYSTEM_ACTOR,
)
from app.context import RequestContext, request_context_var
from app.models.audit_log import AuditLog
from app.models.system_error_log import SystemErrorLog
from app.services.audit_service import (
    _diff_fields,
    _serialize_value,
    record_audit,
    record_system_error,
)
from tests.factories import create_portfolio, create_trade

PORT = "AUDIT_UT"
PRODUCT = "510300.SH"
MARKET = "CN_EXCHANGE"
D0 = date(2025, 6, 6)


class _Obj:
    """_diff_fields 只需要属性读写，用轻量替身避免依赖具体模型。"""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


@contextmanager
def _bound_context(**kwargs):
    """绑定一个请求上下文并在退出时还原（模拟中间件的绑定/解绑）。"""
    token = request_context_var.set(RequestContext(**kwargs))
    try:
        yield
    finally:
        request_context_var.reset(token)


@pytest.fixture
def error_log_db(tmp_path, monkeypatch):
    """把 record_system_error 的独立 session 指向一次性 SQLite 文件库。

    不能复用 test_db：独立 session 会另开一条连接，而 conftest 的外层事务已持有测试库
    写锁（SQLite 单写者），真实写入必然 database is locked。
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'error_log.db'}")
    SystemErrorLog.__table__.create(bind=engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr("app.database.SessionLocal", factory)
    yield factory
    engine.dispose()


@pytest.fixture
def trade(test_db):
    create_portfolio(test_db, code=PORT, status="active")
    return create_trade(
        test_db,
        portfolio_code=PORT,
        product_code=PRODUCT,
        market=MARKET,
        trade_type="buy",
        amount=5000.0,
        shares=3333.33,
        price=1.5,
        trade_date=D0,
        status="pending",
    )


class TestConstantWidths:
    """常量值必须落在 audit_log 的列宽内，否则每次写入都被 DB 拒绝。"""

    ACTIONS = {k: v for k, v in vars(audit_actions).items() if k.startswith("ACTION_")}
    RESOURCES = {k: v for k, v in vars(audit_actions).items() if k.startswith("RESOURCE_")}

    def test_actions_fit_column_width(self):
        assert self.ACTIONS, "未收集到 ACTION_* 常量，检查是否被重命名"
        for name, value in self.ACTIONS.items():
            assert isinstance(value, str)
            assert len(value) <= 20, f"{name}={value!r} 超出 audit_log.action(20)"

    def test_resource_types_fit_column_width(self):
        assert self.RESOURCES, "未收集到 RESOURCE_* 常量，检查是否被重命名"
        for name, value in self.RESOURCES.items():
            assert isinstance(value, str)
            assert len(value) <= 50, f"{name}={value!r} 超出 audit_log.resource_type(50)"

    def test_system_actor_fits_investor_code_width(self):
        assert len(SYSTEM_ACTOR) <= 20

    def test_values_are_unique(self):
        """值重复会让审计检索无法区分动作/资源。"""
        assert len(set(self.ACTIONS.values())) == len(self.ACTIONS)
        assert len(set(self.RESOURCES.values())) == len(self.RESOURCES)


class TestSerializeValue:
    def test_none_passthrough(self):
        assert _serialize_value(None) is None

    def test_str_passthrough(self):
        assert _serialize_value("原样保留") == "原样保留"

    def test_chinese_not_ascii_escaped(self):
        serialized = _serialize_value({"备注": "现金分红"})
        assert "现金分红" in serialized
        assert json.loads(serialized) == {"备注": "现金分红"}

    def test_non_json_types_stringified(self):
        """default=str：Decimal / date 不可 JSON 序列化，落库前统一字符串化。"""
        serialized = _serialize_value({"amount": Decimal("10.50"), "d": D0})
        assert json.loads(serialized) == {"amount": "10.50", "d": "2025-06-06"}


class TestDiffFields:
    def test_only_changed_fields_kept(self):
        obj = _Obj(notes="旧备注", fee=Decimal("1.00"), shares=Decimal("100"))
        old, new = _diff_fields(obj, {"notes": "新备注", "fee": Decimal("1.00")})
        assert old == {"notes": "旧备注"}
        assert new == {"notes": "新备注"}

    def test_no_change_yields_empty_dicts(self):
        assert _diff_fields(_Obj(notes="x"), {"notes": "x"}) == ({}, {})

    def test_absent_attribute_read_as_none(self):
        old, new = _diff_fields(_Obj(), {"notes": "y"})
        assert old == {"notes": None}
        assert new == {"notes": "y"}

    def test_decimal_vs_float_same_value_is_not_a_change(self):
        """DB Numeric 读出 Decimal、update schema 是 Optional[float]：
        `Decimal("1234.5600") != 1234.56` 恒真，原样重提交会被判为变更。

        Decimal 侧显式构造，复现「DB Numeric 列（Decimal）撞上 schema 的
        Optional[float]」的真实形态；SQLAlchemy 的 Numeric 在 SQLite 与 MySQL
        都回 Decimal（实测 `Decimal("0.5000")`），故本地即可复现。
        """
        obj = _Obj(cash_change=Decimal("1234.5600"), ratio=Decimal("1.1000"))
        assert _diff_fields(obj, {"cash_change": 1234.56, "ratio": 1.1}) == ({}, {})

    def test_genuine_numeric_change_still_detected(self):
        obj = _Obj(cash_change=Decimal("1234.5600"))
        old, new = _diff_fields(obj, {"cash_change": 1234.57})
        assert old == {"cash_change": Decimal("1234.5600")}
        assert new == {"cash_change": 1234.57}

    def test_none_to_number_is_a_change(self):
        old, new = _diff_fields(_Obj(div_cash=None), {"div_cash": 0.5})
        assert old == {"div_cash": None}
        assert new == {"div_cash": 0.5}

    def test_bool_bypasses_decimal_conversion(self):
        """isinstance(True, int) 为真，不排除则 Decimal(str(True)) 抛 InvalidOperation。"""
        old, new = _diff_fields(_Obj(flag=False), {"flag": True})
        assert old == {"flag": False}
        assert new == {"flag": True}


class TestActorAttribution:
    def test_request_actor_and_ip_recorded(self, test_db, trade):
        with _bound_context(request_id="req-1", actor="ADMIN", client_ip="10.0.0.1"):
            record_audit(
                test_db,
                action=ACTION_UPDATE,
                resource_type=RESOURCE_TRADE,
                resource_id=str(trade.id),
            )
        row = test_db.query(AuditLog).order_by(AuditLog.id.desc()).first()
        assert row.investor_code == "ADMIN"
        assert row.ip_address == "10.0.0.1"
        assert row.action == ACTION_UPDATE
        assert row.resource_type == RESOURCE_TRADE

    def test_system_sentinel_without_actor(self, test_db, trade):
        """后台执行体（调度器/线程池）无请求主体：actor 落 SYSTEM 哨兵。

        investor_code 是 NOT NULL 列，缺哨兵会让后台路径的审计整条写不进去。
        """
        with _bound_context(request_id="req-bg"):
            record_audit(
                test_db,
                action=ACTION_CREATE,
                resource_type=RESOURCE_TRADE,
                resource_id=str(trade.id),
            )
        row = test_db.query(AuditLog).order_by(AuditLog.id.desc()).first()
        assert row.investor_code == SYSTEM_ACTOR
        assert row.ip_address is None

    def test_old_new_value_serialized_as_json(self, test_db, trade):
        with _bound_context(actor="ADMIN"):
            record_audit(
                test_db,
                action=ACTION_UPDATE,
                resource_type=RESOURCE_TRADE,
                resource_id=str(trade.id),
                old_value={"备注": "旧"},
                new_value={"备注": "新", "fee": Decimal("1.50")},
            )
        row = test_db.query(AuditLog).order_by(AuditLog.id.desc()).first()
        assert json.loads(row.old_value) == {"备注": "旧"}
        assert json.loads(row.new_value) == {"备注": "新", "fee": "1.50"}

    def test_no_commit(self, test_db, trade):
        """service 层不 commit（§1.1）：审计行随业务事务由调用方提交。"""
        with _bound_context(actor="ADMIN"):
            record_audit(
                test_db,
                action=ACTION_CREATE,
                resource_type=RESOURCE_TRADE,
                resource_id=str(trade.id),
            )
        test_db.rollback()
        assert test_db.query(AuditLog).count() == 0


class TestSavepointIsolation:
    def test_failure_never_propagates(self, test_db, trade, error_log_db):
        """action 为 NOT NULL 列，传 None 必然让 INSERT 失败——审计不得外抛。"""
        record_audit(
            test_db,
            action=None,
            resource_type=RESOURCE_TRADE,
            resource_id=str(trade.id),
        )

    def test_unflushed_business_change_survives(self, test_db, trade, error_log_db):
        """审计失败不得回滚调用方尚未 flush 的业务改动。

        savepoint 必须只包审计行：否则 ROLLBACK TO SAVEPOINT 连带撤销同一次 flush
        里的业务 UPDATE，expire_all() 再丢弃内存态，调用方却收到成功响应。
        """
        trade.notes = "业务改动"
        record_audit(
            test_db,
            action=None,
            resource_type=RESOURCE_TRADE,
            resource_id=str(trade.id),
        )
        test_db.flush()
        assert trade.notes == "业务改动"

    def test_session_remains_usable_and_audit_row_absent(
        self, test_db, trade, error_log_db
    ):
        record_audit(
            test_db,
            action=None,
            resource_type=RESOURCE_TRADE,
            resource_id=str(trade.id),
        )
        # 失败的审计行被 expunge，不会在后续 flush/commit 时重复 INSERT
        assert test_db.query(AuditLog).count() == 0
        # 业务事务未被毒化：后续审计与业务写入照常
        with _bound_context(actor="ADMIN"):
            record_audit(
                test_db,
                action=ACTION_UPDATE,
                resource_type=RESOURCE_TRADE,
                resource_id=str(trade.id),
            )
        assert test_db.query(AuditLog).count() == 1
        trade.notes = "后续改动"
        test_db.flush()
        assert trade.notes == "后续改动"

    def test_failure_leaves_error_traces(self, test_db, trade, error_log_db, caplog):
        with caplog.at_level(logging.ERROR, logger="app.services.audit_service"):
            record_audit(
                test_db,
                action=None,
                resource_type=RESOURCE_TRADE,
                resource_id=str(trade.id),
            )
        assert any("审计写入失败" in r.getMessage() for r in caplog.records)

        with error_log_db() as session:
            error = session.query(SystemErrorLog).one()
        assert error.error_type == "AuditWriteFailure"
        assert "审计写入失败" in error.error_message
        assert str(trade.id) in error.error_message
        assert error.error_stack


class TestRecordSystemError:
    def test_persists_all_fields(self, error_log_db):
        record_system_error(
            error_type="ValueError",
            error_message="净值缺失",
            error_stack="Traceback ...",
            request_path="/api/snapshots/generate",
            request_method="POST",
            investor_code="ADMIN",
            ip_address="10.0.0.1",
        )
        with error_log_db() as session:
            error = session.query(SystemErrorLog).one()
        assert error.error_type == "ValueError"
        assert error.error_message == "净值缺失"
        assert error.error_stack == "Traceback ..."
        assert error.request_path == "/api/snapshots/generate"
        assert error.request_method == "POST"
        assert error.investor_code == "ADMIN"
        assert error.ip_address == "10.0.0.1"

    def test_write_failure_is_swallowed(self, monkeypatch, caplog):
        """best-effort：system_error_log 自己写不进去时只记 stdout，绝不外抛。"""
        broken = MagicMock()
        broken.commit.side_effect = RuntimeError("db gone")
        monkeypatch.setattr("app.database.SessionLocal", lambda: broken)

        with caplog.at_level(logging.ERROR, logger="app.services.audit_service"):
            record_system_error(error_type="BoomError", error_message="炸了")

        broken.close.assert_called_once()
        assert any("system_error_log 写入失败" in r.getMessage() for r in caplog.records)
