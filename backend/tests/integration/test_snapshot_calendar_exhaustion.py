# ============================================================================
# 集成测试：交易日历耗尽下的快照路径（issue #591）
# ============================================================================
# 核心反例：nav_lag_days≥1 的产品在取价日前序交易日不足时，旧实现回退成
# `target_date - lag 自然日`——若那个编造日上恰好有一条 PriceRecord，快照会**静默**
# 按错误日期估值（不报错、不崩溃）。现契约：抛 CALENDAR_NOT_SYNCED、422、零写入。
# 同时钉住「日历完整但正确取价日无价 → 仍报 MISSING_NAV」的边界（两码不得互串）。
#
# 日历裁剪走事务内 DELETE + add（与 test_trading_day.closed_week_calendar 同型），
# 不用 ensure_trading_day（它 commit、逃逸 SAVEPOINT 隔离）。
# ============================================================================
from datetime import date

import pytest

from app.models import PriceRecord, TradingCalendar
from app.services.exceptions import BusinessError
from app.services.snapshot_service import (
    generate_daily_snapshots,
    validate_snapshot_dependencies,
)
from tests.factories import (
    create_investor_holding,
    create_portfolio,
    create_position_snapshot,
    create_product,
    create_price_record,
    create_value_snapshot,
)
from tests.integration.snapshot_helpers import capture_portfolio_state

BASELINE = date(2025, 6, 3)      # 持仓基线日（组合已有资产）
TARGET = date(2025, 6, 6)        # 周五；受控日历下它之前没有任何开市日
FABRICATED = date(2025, 6, 5)    # 旧实现会伪造出的取价日（TARGET - 1 自然日）


def _calendar_without_prior_days(db):
    """日历只留 TARGET 及其之后的开市日——TARGET 之前什么都不存在。"""
    db.query(TradingCalendar).delete(synchronize_session=False)
    for d in (TARGET, date(2025, 6, 9), date(2025, 6, 10)):
        db.add(TradingCalendar(calendar_date=d, is_open=True, exchange="SSE"))
    db.flush()


def _lag1_portfolio(db, code="CEX1", baseline=BASELINE):
    """active 组合 + nav_lag_days=1 场外基金持仓（估值取价须 T-1 交易日）。"""
    create_portfolio(db, code=code, status="active")
    create_product(db, code="CEXL1.OF", market="CN_OTC", nav_lag_days=1)
    create_position_snapshot(
        db, code, "CEXL1.OF", "CN_OTC", baseline,
        shares=100.0, unit_price=2.0, cost_price=2.0, market_value=200.0,
        platform_code="MYCF",
    )
    create_value_snapshot(db, code, baseline, total_value=200.0, total_shares=100.0,
                          unit_price=2.0)
    create_investor_holding(db, code, "VIEWER", baseline, shares=100.0)
    return code


class TestSilentWrongValuationIsGone:
    """#591 核心：日历不足 + 伪造日上有价 → 拒绝，且绝不写下错误快照。"""

    def test_generate_rejects_when_prior_trading_day_missing(self, test_db):
        code = _lag1_portfolio(test_db)
        _calendar_without_prior_days(test_db)
        # 在旧实现伪造的那个自然日上**真的**插一条价格：静默错误估值的燃料
        create_price_record(test_db, "CEXL1.OF", "CN_OTC", FABRICATED, unit_price=9.99)
        before = capture_portfolio_state(test_db, code)

        with pytest.raises(BusinessError) as ei:
            generate_daily_snapshots(test_db, code, TARGET, check_continuity=False)

        assert ei.value.code == "CALENDAR_NOT_SYNCED"
        assert ei.value.details["requested_offset"] == 1
        assert ei.value.details["resolved_offset"] == 0
        # 逐字段不变 ⟹ 没有按伪造日写下错误估值（校验在任何写操作之前即拒绝）
        assert capture_portfolio_state(test_db, code) == before

    def test_validation_path_also_rejects_not_passes(self, test_db):
        """预校验侧同样拒绝——不得返回「passed」把问题推给写入阶段。"""
        code = _lag1_portfolio(test_db)
        _calendar_without_prior_days(test_db)
        create_price_record(test_db, "CEXL1.OF", "CN_OTC", FABRICATED, unit_price=9.99)
        with pytest.raises(BusinessError) as ei:
            validate_snapshot_dependencies(test_db, code, TARGET, static_only=True)
        assert ei.value.code == "CALENDAR_NOT_SYNCED"

    def test_lag2_partial_prior_days_rejects(self, test_db):
        """nav_lag_days=2 只剩 1 个前序交易日 → 部分耗尽同样拒绝（不取中间日）。"""
        code = _lag1_portfolio(test_db, code="CEX2")
        create_product(test_db, code="CEXL2.OF", market="CN_OTC", nav_lag_days=2)
        create_position_snapshot(
            test_db, code, "CEXL2.OF", "CN_OTC", TARGET,
            shares=50.0, unit_price=2.0, cost_price=2.0, market_value=100.0,
            platform_code="MYCF",
        )
        test_db.query(TradingCalendar).delete(synchronize_session=False)
        for d in (BASELINE, TARGET):  # TARGET 之前只有 BASELINE 一个开市日
            test_db.add(TradingCalendar(calendar_date=d, is_open=True, exchange="SSE"))
        test_db.flush()

        with pytest.raises(BusinessError) as ei:
            generate_daily_snapshots(test_db, code, TARGET, check_continuity=False)
        assert ei.value.code == "CALENDAR_NOT_SYNCED"
        assert ei.value.details["requested_offset"] == 2
        assert ei.value.details["resolved_offset"] == 1


class TestMissingNavStaysDistinct:
    """日历解析得出取价日、但该日无价格 → 仍报 MISSING_NAV（两码边界不得互串）。"""

    def test_calendar_complete_but_price_missing(self, test_db):
        code = _lag1_portfolio(test_db)
        # 完整种子日历（06-09 的前一交易日 06-06 可解析），但刻意不在 06-06 放价格记录
        assert test_db.query(PriceRecord).filter(
            PriceRecord.product_code == "CEXL1.OF",
            PriceRecord.price_date == TARGET,
        ).first() is None

        with pytest.raises(BusinessError) as ei:
            generate_daily_snapshots(
                test_db, code, date(2025, 6, 9), check_continuity=False
            )
        assert ei.value.code == "MISSING_NAV"


class TestRecalculatePrecheckIs422Not500:
    """site 22：预校验在 recalculate 的 try 之外，router 必须映射 422 而非 500。"""

    def test_recalculate_sync_is_422_calendar_not_synced(
        self, client, admin_headers, test_db
    ):
        code = _lag1_portfolio(test_db)
        _calendar_without_prior_days(test_db)
        create_price_record(test_db, "CEXL1.OF", "CN_OTC", FABRICATED, unit_price=9.99)
        before = capture_portfolio_state(test_db, code)

        resp = client.post(
            "/api/snapshots/recalculate",
            json={"portfolio_code": code,
                  "start_date": TARGET.isoformat(), "end_date": TARGET.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["error"] == "CALENDAR_NOT_SYNCED"
        # 预校验早于任何删除/重建：对外仍是「无变化」
        assert capture_portfolio_state(test_db, code) == before

    def test_recalculate_async_job_records_error_not_crash(self, test_db):
        """同场景走异步 job：终态 failed + 日历错误入 error_message，不抛未处理异常。"""
        from app.models.sync_job import SyncJob
        from app.services.snapshot_recalc_job import _run_snapshot_recalc_job_impl

        code = _lag1_portfolio(test_db)
        _calendar_without_prior_days(test_db)
        job = SyncJob(
            job_type="snapshot_recalc", status="pending", triggered_by="manual",
            params={"portfolio_code": code, "start_date": TARGET.isoformat(),
                    "end_date": TARGET.isoformat()},
        )
        test_db.add(job)
        test_db.commit()

        _run_snapshot_recalc_job_impl(job.id, db=test_db)

        test_db.refresh(job)
        assert job.status == "failed"
        assert "交易日历" in (job.error_message or "")


# 连续性校验的「最新快照日之后无交易日」分支经 generate_daily_snapshots 不可达
# （target 若非交易日会先被 _validate_trading_day 拒掉），故由
# tests/unit/test_snapshot_service.py 直接对 _validate_snapshot_continuity 立测。
