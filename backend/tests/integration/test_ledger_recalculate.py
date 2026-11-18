# ============================================================================
# 集成测试：台账型不进净值重算链（#905 PR1，L2 Blocker 1 反例）
# 修复前：_resolve_recalculate_portfolios 全量只校 active 漏 kind，台账型进循环
#   后闸门抛领域异常被 except Exception 收进 errors → 同批净值型重算整体回滚（200+errors）。
# 修复后：全量按 kind=='nav' 过滤；显式台账型 code 响亮 422 PORTFOLIO_KIND_NOT_ALLOWED。
# ============================================================================

from datetime import date

import pytest

from tests.factories import create_portfolio, create_investor, create_trade
from app.services import snapshot_service
from app.services.exceptions import BusinessError
from app.services.snapshot_recalc_job import _run_snapshot_recalc_job_impl
from app.models import PortfolioValueSnapshot, PortfolioPosition
from app.models.sync_job import SyncJob

D0 = date(2025, 1, 6)  # conftest 交易日历内的交易日


def _setup_nav_cash(db, code):
    """净值型 active 组合 + 一条 confirmed CASH 买入腿：重算可重建 CASH 持仓（无需行情）。"""
    create_portfolio(db, code=code, status="active")  # kind 默认 nav
    create_trade(
        db, code, "CASH", "", trade_type="buy", amount=10000.0,
        platform_code="MYCF", trade_date=D0, confirm_date=D0,
        status="confirmed", transfer_group=f"sub_{code}",
    )


class TestRecalculateKindFilter:
    def test_full_recalc_excludes_ledger(self, test_db):
        """active nav + active ledger 共存跑全量重算：results 只含 nav、errors 全空、
        nav 快照真重建、ledger 零账务行。"""
        create_investor(test_db, code="INV_R")
        _setup_nav_cash(test_db, "NAV_R")
        create_portfolio(test_db, code="LED_R", kind="ledger",
                         owner_investor_code="INV_R", status="active")

        result = snapshot_service.recalculate_snapshots(
            test_db, portfolio_code=None, start_date=D0, end_date=D0)

        codes = [r["portfolio_code"] for r in result["results"]]
        assert "NAV_R" in codes
        assert "LED_R" not in codes
        # ledger 未毒化批次：所有组合 errors 全空
        assert all(r["errors"] == [] for r in result["results"])
        # nav 快照真重建
        nav = next(r for r in result["results"] if r["portfolio_code"] == "NAV_R")
        assert nav["total_processed"] >= 1
        assert test_db.query(PortfolioValueSnapshot).filter_by(portfolio_code="NAV_R").count() >= 1
        # ledger 零账务行
        assert test_db.query(PortfolioValueSnapshot).filter_by(portfolio_code="LED_R").count() == 0
        assert test_db.query(PortfolioPosition).filter_by(portfolio_code="LED_R").count() == 0

    def test_explicit_ledger_code_rejected(self, test_db):
        """显式传台账型 code → 422 PORTFOLIO_KIND_NOT_ALLOWED（不降级 200+errors）。"""
        create_investor(test_db, code="INV_R2")
        create_portfolio(test_db, code="LED_R2", kind="ledger",
                         owner_investor_code="INV_R2", status="active")
        with pytest.raises(BusinessError) as ei:
            snapshot_service.recalculate_snapshots(
                test_db, portfolio_code="LED_R2", start_date=D0, end_date=D0)
        assert ei.value.code == "PORTFOLIO_KIND_NOT_ALLOWED"

    def test_preview_excludes_ledger(self, test_db):
        """preview_recalculate_range 同源修正：全量预览只含 nav。"""
        create_investor(test_db, code="INV_R4")
        _setup_nav_cash(test_db, "NAV_R4")
        create_portfolio(test_db, code="LED_R4", kind="ledger",
                         owner_investor_code="INV_R4", status="active")
        preview = snapshot_service.preview_recalculate_range(
            test_db, portfolio_code=None, start_date=D0, end_date=D0)
        codes = [p["portfolio_code"] for p in preview["portfolios"]]
        assert "NAV_R4" in codes
        assert "LED_R4" not in codes

    def test_async_job_excludes_ledger(self, test_db):
        """覆盖 _run_snapshot_recalc_job_impl：nav+ledger 共存全量重算，job 成功、
        nav 重建、ledger 零账务行（异步路径与同步共用 recalculate_snapshots）。"""
        create_investor(test_db, code="INV_R3")
        _setup_nav_cash(test_db, "NAV_R3")
        create_portfolio(test_db, code="LED_R3", kind="ledger",
                         owner_investor_code="INV_R3", status="active")
        job = SyncJob(
            job_type="snapshot_recalc", status="pending",
            params={"portfolio_code": None,
                    "start_date": D0.isoformat(), "end_date": D0.isoformat()},
        )
        test_db.add(job)
        test_db.commit()
        test_db.refresh(job)

        _run_snapshot_recalc_job_impl(job.id, db=test_db)

        test_db.refresh(job)
        assert job.status == "success"
        assert test_db.query(PortfolioValueSnapshot).filter_by(portfolio_code="LED_R3").count() == 0
        assert test_db.query(PortfolioValueSnapshot).filter_by(portfolio_code="NAV_R3").count() >= 1