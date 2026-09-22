# ============================================================================
# 单元测试：service 层事务与领域异常边界（backend/AGENTS.md「分层目录与职责」节）
# ============================================================================
# 断言以下 service 函数全程不调用 db.commit()/rollback()（事务边界交调用方）：
# - snapshot_service.generate_daily_snapshots / recalculate_snapshots
# - trading_calendar_service.sync_trading_calendar
# - market_data_service.sync_product_prices（含 _mark_failed 失败路径）
# - task_runner.cleanup_old_logs
# - trade_service.calculate_confirm_preview（纯计算，不修改 trade，issue #65）
# 方式：monkeypatch 注入会话的 commit/rollback，保留 flush 与 savepoint 操作。
# 另守任务投递入口的反向约定（#592）：submit_* 不得接受注入会话，必须自持
# SessionLocal 并先行 commit（后台线程凭 job_id 跨会话查任务）。
# ============================================================================

import ast
import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from app.models import (
    Investor,
    PortfolioValueSnapshot,
    PriceRecord,
    SyncJob,
    TradingCalendar,
)
from app.services.market_data_service import ConflictError, submit_price_sync_job
from app.services.snapshot_recalc_job import submit_snapshot_recalc_job
from tests.factories import (
    create_portfolio,
    create_position_snapshot,
    create_value_snapshot,
    create_product,
    create_trade,
    create_price_record,
)
from tests.session_helpers import patch_non_closing_session_local


D0 = date(2025, 6, 6)       # 周五（conftest 日历工作日为交易日）
NEXT_DAY = date(2025, 6, 9)  # 下一交易日（周一）


def _forbid_commit(monkeypatch, db):
    """注入会话不得提交或整体回滚；不改 SessionTransaction 的 savepoint 方法。"""
    for method in ("commit", "rollback"):
        def _fail(*args, _method=method, **kwargs):
            raise AssertionError(f"service 层不得调用 db.{_method}()（事务边界交调用方）")
        monkeypatch.setattr(db, method, _fail)


class TestInjectedSessionBoundaryGuard:
    @pytest.mark.parametrize("method", ["commit", "rollback"])
    def test_injected_session_transaction_boundary_is_rejected(self, test_db, monkeypatch, method):
        _forbid_commit(monkeypatch, test_db)
        with pytest.raises(AssertionError, match=rf"db\.{method}\(\)"):
            getattr(test_db, method)()

    @pytest.mark.parametrize("method", ["commit", "rollback"])
    def test_savepoint_transaction_boundary_is_allowed(self, test_db, monkeypatch, method):
        _forbid_commit(monkeypatch, test_db)
        sp = test_db.begin_nested()
        test_db.flush()
        getattr(sp, method)()
        assert not sp.is_active
        assert test_db.is_active
        test_db.flush()

    def test_owned_session_is_not_patched(self, test_db, monkeypatch):
        _forbid_commit(monkeypatch, test_db)
        with Session() as owned:
            owned.commit()
            owned.rollback()


_SUBMIT_ENTRIES = [submit_price_sync_job, submit_snapshot_recalc_job]


class TestTaskSubmissionSessionOwnership:
    """任务投递入口（#592 方案 A）：自持 SessionLocal，绝不接受注入会话。

    与其余「service 不 commit」守卫方向相反——投递入口**必须** commit（后台线程
    另开会话按 job_id 取任务，只 flush 则线程查不到、任务丢失）；正因它必 commit，
    才不得借用调用方的会话。此处同时钉死该结构性事实与两条实证过的失败形态。
    """

    @pytest.mark.parametrize("func", _SUBMIT_ENTRIES, ids=lambda f: f.__name__)
    def test_signatures_reject_injected_session(self, func):
        """签名守卫：双路径（注入/自持）不得回归——形参里不能再出现 db/session。"""
        params = inspect.signature(func).parameters
        for name in ("db", "session"):
            assert name not in params, (
                f"{func.__name__} 重新接受注入会话（形参 {name}）：投递入口的 commit "
                f"会连带提交调用方未提交的写入（#592）"
            )

    def test_submitted_job_is_visible_to_independent_session(self, test_db):
        """交接契约：submit 必须把 job 提交到**其他会话可读见**的持久状态。

        用真实自持会话（非 test_db）——这正是后台线程的执行形态。本用例会留下
        已提交行（test_db 的 SAVEPOINT 回滚撤不回独立会话的 commit），故必须自清理。
        """
        from app.database import SessionLocal

        with patch("app.services.market_data_service._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            job_id = submit_price_sync_job({"scope": "all"}, triggered_by="manual")

        try:
            probe = SessionLocal()
            try:
                job = probe.query(SyncJob).filter(SyncJob.id == job_id).first()
                assert job is not None, "job 未持久化：后台线程将查不到任务"
                assert job.status == "pending"
            finally:
                probe.close()
        finally:
            # 自持会话的 commit 逃逸 test_db 回滚，显式清行避免污染同会话后续用例
            cleanup = SessionLocal()
            try:
                cleanup.query(SyncJob).filter(SyncJob.id == job_id).delete()
                cleanup.commit()
            finally:
                cleanup.close()

    def test_caller_uncommitted_writes_are_not_swept_by_submit(self, test_db):
        """危害反转实证（#592 的核心）：自持会话的 commit 绝不连带提交调用方写入。

        旧注入形态下 submit 直接在 test_db 上 commit，调用方未提交的 Investor 被
        一起提交、随后 rollback 也撤不回。方案 A 后必须反向成立：test_db 回滚后
        Investor 消失。用真实自持会话（同后台线程形态），造出的 job 行须自清理。
        """
        from app.database import SessionLocal

        investor = Investor(
            code="NC592", name="未提交投资人", role="viewer", password_hash="x"
        )
        test_db.add(investor)

        try:
            with patch("app.services.market_data_service._get_executor") as mock_exec:
                mock_exec.return_value = MagicMock()
                job_id = submit_price_sync_job({"scope": "all"}, triggered_by="manual")

            test_db.rollback()
            assert test_db.query(Investor).filter(Investor.code == "NC592").first() is None
        finally:
            cleanup = SessionLocal()
            try:
                cleanup.query(SyncJob).filter(
                    SyncJob.job_type.in_(
                        ["price_history_sync", "price_incremental_sync"]
                    )
                ).delete(synchronize_session=False)
                cleanup.commit()
            finally:
                cleanup.close()

    def test_submission_failure_marks_job_failed_not_pending(self, test_db, monkeypatch):
        """投递抛错 → job 终态 failed，不得留下永久占锁的 pending 孤儿。"""
        patch_non_closing_session_local(monkeypatch, test_db)
        with patch("app.services.market_data_service._get_executor") as mock_exec:
            mock_exec.return_value.submit.side_effect = RuntimeError("线程池已关闭")
            with pytest.raises(RuntimeError, match="线程池已关闭"):
                submit_price_sync_job({"scope": "all"}, triggered_by="manual")

        job = test_db.query(SyncJob).order_by(SyncJob.id.desc()).first()
        assert job.status == "failed"
        assert job.error_message and "任务投递失败" in job.error_message
        assert job.finished_at is not None

        # 关键回归：failed 终态不再阻塞后续提交（pending 孤儿会永久 409）
        with patch("app.services.market_data_service._get_executor") as mock_exec:
            mock_exec.return_value = MagicMock()
            second_id = submit_price_sync_job({"scope": "all"}, triggered_by="manual")
        assert second_id > job.id

    def test_conflict_lock_counts_pending_rows(self, test_db, monkeypatch):
        """单 active 锁对 pending 生效——这正是孤儿会永久 409 的机制，钉死语义。"""
        patch_non_closing_session_local(monkeypatch, test_db)
        test_db.add(SyncJob(job_type="price_history_sync", status="pending",
                            triggered_by="manual"))
        test_db.commit()
        with pytest.raises(ConflictError, match="已有价格同步任务在运行中"):
            submit_price_sync_job({"scope": "all"}, triggered_by="manual")


def _setup_cash_snapshot(db, portfolio_code: str, snapshot_date: date, amount: float = 10000.0):
    """制造指定日的持仓+市值快照（仅 CASH，无需行情）"""
    create_position_snapshot(
        db, portfolio_code, "CASH", "",
        snapshot_date=snapshot_date,
        cash_amount=amount, unit_price=None, cost_price=None,
        market_value=amount, platform_code="MYCF",
    )
    create_value_snapshot(
        db, portfolio_code, snapshot_date,
        total_value=amount, total_shares=amount, unit_price=1.0,
    )


class TestSnapshotServiceNoCommit:
    """快照 service 全程无 commit（issue #58 核心 + 遗漏缺口）"""

    def test_generate_daily_snapshots_no_commit(self, test_db, monkeypatch):
        """generate_daily_snapshots 正常生成路径不 commit"""
        from app.services.snapshot_service import generate_daily_snapshots

        create_portfolio(test_db, code="NC_GEN", status="active")
        _setup_cash_snapshot(test_db, "NC_GEN", D0)

        _forbid_commit(monkeypatch, test_db)
        result = generate_daily_snapshots(test_db, "NC_GEN", NEXT_DAY)

        assert result["success"] is True
        # flush 后同事务内可见
        assert test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == "NC_GEN",
            PortfolioValueSnapshot.snapshot_date == NEXT_DAY,
        ).first() is not None

    def test_generate_daily_snapshots_skip_path_no_commit(self, test_db, monkeypatch):
        """无持仓跳过路径也不 commit"""
        from app.services.snapshot_service import generate_daily_snapshots

        create_portfolio(test_db, code="NC_SKIP", status="active")

        _forbid_commit(monkeypatch, test_db)
        result = generate_daily_snapshots(test_db, "NC_SKIP", D0)
        assert result["success"] is True
        assert "跳过" in result["message"]

    def test_recalculate_snapshots_no_commit(self, test_db, monkeypatch):
        """recalculate_snapshots 全区间重算不 commit（issue #58 本体）"""
        from app.services.snapshot_service import recalculate_snapshots

        create_portfolio(test_db, code="NC_RECALC", status="active")
        _setup_cash_snapshot(test_db, "NC_RECALC", D0)
        _setup_cash_snapshot(test_db, "NC_RECALC", NEXT_DAY)

        _forbid_commit(monkeypatch, test_db)
        result = recalculate_snapshots(test_db, "NC_RECALC", D0, NEXT_DAY)

        assert result["success"] is True
        assert result["results"][0]["errors"] == []
        assert result["results"][0]["total_processed"] == 2

    def test_recalculate_precheck_failure_no_deletion(self, test_db, monkeypatch):
        """预校验失败（NAV 缺失）→ 抛 ValueError，不删除任何快照"""
        from app.services.snapshot_service import recalculate_snapshots

        create_portfolio(test_db, code="NC_PRE", status="active")
        create_product(test_db, code="NAVX.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        _setup_cash_snapshot(test_db, "NC_PRE", D0)
        # 最新持仓含无任何价格记录的基金 → price_data 预校验必失败
        create_position_snapshot(
            test_db, "NC_PRE", "NAVX.OF", "CN_OTC",
            snapshot_date=D0, shares=100.0, market_value=100.0,
            platform_code="MYCF",
        )

        _forbid_commit(monkeypatch, test_db)
        with pytest.raises(ValueError, match="预校验失败"):
            recalculate_snapshots(test_db, "NC_PRE", D0, D0)

        # 未进入删除/重建流程，原快照仍在
        assert test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == "NC_PRE",
            PortfolioValueSnapshot.snapshot_date == D0,
        ).first() is not None


class TestTradingCalendarServiceNoCommit:
    """sync_trading_calendar 不 commit"""

    @patch("app.services.trading_calendar_service.get_trade_calendar")
    def test_sync_trading_calendar_no_commit(self, mock_cal, test_db, monkeypatch):
        mock_cal.return_value = [
            {"date": "2030-01-02", "is_open": True},
            {"date": "2030-01-03", "is_open": True},
        ]
        _forbid_commit(monkeypatch, test_db)
        from app.services.trading_calendar_service import sync_trading_calendar
        result = sync_trading_calendar(test_db, 2030)

        assert result["synced_count"] == 2
        # flush 后同事务内可见
        assert test_db.query(TradingCalendar).filter(
            TradingCalendar.calendar_date == date(2030, 1, 2)
        ).first() is not None


class TestMarketDataServiceNoCommit:
    """sync_product_prices 及失败标记路径不 commit"""

    @patch("app.services.market_data_service.get_fund_daily")
    def test_sync_product_prices_no_commit(self, mock_daily, test_db, monkeypatch, sample_etf_product):
        mock_daily.return_value = [
            {"trade_date": "20250606", "close": 4.0, "pre_close": 3.9, "pct_chg": 2.56},
        ]
        _forbid_commit(monkeypatch, test_db)
        from app.services.market_data_service import sync_product_prices
        result = sync_product_prices(
            test_db, sample_etf_product.code, sample_etf_product.market,
            start_date=D0, end_date=D0,
        )
        assert result["success"] is True
        assert test_db.query(PriceRecord).filter(
            PriceRecord.product_code == sample_etf_product.code,
            PriceRecord.price_date == D0,
        ).first() is not None

    @patch("app.services.market_data_service.get_fund_daily")
    def test_mark_failed_path_no_commit(self, mock_daily, test_db, monkeypatch, sample_etf_product):
        """数据源异常 → _mark_failed 不 commit，失败标记停留在事务内待调用方提交"""
        mock_daily.side_effect = Exception("tushare boom")
        _forbid_commit(monkeypatch, test_db)
        from app.services.market_data_service import sync_product_prices
        result = sync_product_prices(
            test_db, sample_etf_product.code, sample_etf_product.market,
            start_date=D0, end_date=D0,
        )
        assert result["success"] is False
        # 同事务内失败状态已写入（flush）
        assert sample_etf_product.data_source_status == "failed"


class TestTaskRunnerCleanupNoCommit:
    """cleanup_old_logs 不 commit（单次性原子操作，事务交调用方）"""

    def test_cleanup_old_logs_no_commit(self, test_db, monkeypatch):
        from app.services.task_runner import cleanup_old_logs

        _forbid_commit(monkeypatch, test_db)
        result = cleanup_old_logs(test_db)
        assert set(result.keys()) == {
            "login_logs", "audit_logs", "nav_sync_details", "task_logs", "error_logs",
        }


class TestTradePreviewNoCommit:
    """calculate_confirm_preview 纯计算：不 commit、不修改 trade（issue #65）"""

    def test_calculate_confirm_preview_no_commit_no_mutation(self, test_db, monkeypatch):
        from app.services.trade_service import calculate_confirm_preview

        create_portfolio(test_db, code="NC_PRV", status="active")
        product = create_product(
            test_db, code="PRVNC.OF", market="CN_OTC",
            product_type="OEF", asset_class_code="ASSET_STOCK", confirm_days=1,
        )
        create_price_record(test_db, "PRVNC.OF", "CN_OTC", D0, unit_price=1.25)
        trade = create_trade(
            test_db, "NC_PRV", "PRVNC.OF", "CN_OTC",
            trade_type="buy", amount=10000.0, actual_amount=10000.0,
            price=None, trade_date=D0, confirm_date=NEXT_DAY, status="pending",
        )

        _forbid_commit(monkeypatch, test_db)
        result = calculate_confirm_preview(test_db, trade, product)

        # 计算结果正确（与 confirm 共用同一实现）
        assert result["price"] == Decimal("1.25")
        assert result["shares"] == Decimal("8000")
        assert result["confirm_date"] == NEXT_DAY
        assert result["is_otc_nav_fund"] is True
        assert result["paired_cash_amount"] == Decimal("10000")
        # 纯计算：trade 对象未被修改
        assert trade.status == "pending"
        assert trade.price is None
        assert trade.shares is None


class TestSubscriptionPreviewNoCommit:
    """calculate_subscription_confirm_preview 纯计算：不 commit、不修改 subscription（#248）"""

    def test_calculate_subscription_confirm_preview_no_commit_no_mutation(
        self, test_db, monkeypatch
    ):
        from app.services.subscription_service import (
            calculate_subscription_confirm_preview,
        )
        from tests.factories import (
            create_investor,
            create_subscription,
            ensure_trading_day,
            create_value_snapshot,
        )

        create_portfolio(test_db, code="NC_SPRV", status="active")
        create_investor(test_db, code="NC_SPINV")
        ensure_trading_day(test_db, D0, is_open=True)
        ensure_trading_day(test_db, NEXT_DAY, is_open=True)
        create_value_snapshot(test_db, "NC_SPRV", D0,
                              total_value=12500, total_shares=10000, unit_price=1.25)
        sub = create_subscription(
            test_db, "NC_SPRV", "NC_SPINV", sub_type="subscribe",
            amount=10000.0, apply_date=D0,
        )

        _forbid_commit(monkeypatch, test_db)
        result = calculate_subscription_confirm_preview(test_db, sub)

        # 计算结果正确（与 confirm 共用同一实现）
        assert result["nav"] == Decimal("1.25")
        assert result["shares"] == Decimal("8000")
        assert result["amount"] == Decimal("10000")
        assert result["confirm_date"] == NEXT_DAY
        assert result["is_first"] is True
        # 纯计算：subscription 对象未被修改
        assert sub.status == "pending"
        assert sub.unit_price is None
        assert sub.shares is None
        assert sub.confirm_date is None


_HTTP_EXCEPTION_MODULES = {
    "fastapi", "fastapi.exceptions", "starlette", "starlette.exceptions",
}


def _collect_http_exception_dependencies(sources):
    assert sources, "empty services scan"
    dependencies = set()
    scopes = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

    def local_nodes(node):
        yield node
        if not isinstance(node, scopes):
            for child in ast.iter_child_nodes(node):
                yield from local_nodes(child)

    def scan_scope(filename, scope, inherited):
        nodes = [node for child in ast.iter_child_nodes(scope) for node in local_nodes(child)]
        aliases = inherited.copy()
        for node in nodes:
            if isinstance(node, ast.arg):
                aliases.pop(node.arg, None)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                aliases.pop(node.id, None)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                aliases.pop(node.name, None)
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name.split(".")[0]] = (
                        alias.name if alias.asname else alias.name.split(".")[0]
                    )
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    qualified = f"{'.' * node.level}{node.module or ''}.{alias.name}"
                    aliases[alias.asname or alias.name] = qualified
                    if node.level == 0 and node.module in _HTTP_EXCEPTION_MODULES and alias.name in {"HTTPException", "*"}:
                        dependencies.add((filename, node.lineno, qualified))
        for node in nodes:
            if isinstance(node, scopes):
                # 方法与嵌套类不捕获类体局部名字，仍可使用类外的词法绑定。
                outer = inherited if isinstance(scope, ast.ClassDef) else aliases
                scan_scope(filename, node, outer)
            elif isinstance(node, ast.Attribute) and node.attr == "HTTPException":
                parts = []
                value = node.value
                while isinstance(value, ast.Attribute):
                    parts.append(value.attr)
                    value = value.value
                if isinstance(value, ast.Name) and value.id in aliases:
                    module = ".".join([aliases[value.id], *reversed(parts)])
                    if module in _HTTP_EXCEPTION_MODULES:
                        dependencies.add((filename, node.lineno, f"{module}.HTTPException"))

    for filename, source in sorted(sources.items()):
        scan_scope(filename, ast.parse(source, filename=filename), {})
    return sorted(dependencies)


class TestServiceDomainExceptionGuard:
    def test_services_do_not_depend_on_http_exception(self):
        services = Path(__file__).resolve().parents[2] / "app" / "services"
        sources = {
            path.relative_to(services).as_posix(): path.read_text(encoding="utf-8")
            for path in services.rglob("*.py")
        }
        assert _collect_http_exception_dependencies(sources) == []

    @pytest.mark.parametrize("module", [
        "fastapi", "fastapi.exceptions", "starlette", "starlette.exceptions",
    ])
    @pytest.mark.parametrize("alias", ["", " as WebError"])
    def test_direct_and_aliased_exception_imports_are_rejected(self, module, alias):
        source = f"from {module} import HTTPException{alias}\n"
        assert _collect_http_exception_dependencies({"service.py": source}) == [
            ("service.py", 1, f"{module}.HTTPException"),
        ]

    @pytest.mark.parametrize("package", ["fastapi", "starlette"])
    @pytest.mark.parametrize("statement, reference, suffix", [
        ("import {package}", "{package}.HTTPException", ""),
        ("import {package} as web", "web.HTTPException", ""),
        ("import {package}", "{package}.exceptions.HTTPException", ".exceptions"),
        ("import {package}.exceptions", "{package}.exceptions.HTTPException", ".exceptions"),
        ("import {package}.exceptions as errors", "errors.HTTPException", ".exceptions"),
        ("from {package} import exceptions", "exceptions.HTTPException", ".exceptions"),
        ("from {package} import exceptions as errors", "errors.HTTPException", ".exceptions"),
    ])
    def test_module_qualified_references_are_rejected(self, package, statement, reference, suffix):
        source = (
            statement.format(package=package) + "\n"
            + "error_type = " + reference.format(package=package) + "\n"
        )
        assert _collect_http_exception_dependencies({"nested/service.py": source}) == [
            ("nested/service.py", 2, f"{package}{suffix}.HTTPException"),
        ]

    def test_nested_async_import_and_raise_are_rejected(self):
        source = dedent('''\
            async def service():
                from fastapi import HTTPException as WebError
                def fail():
                    import starlette.exceptions as errors
                    raise errors.HTTPException(status_code=422)
                raise WebError(status_code=400)
        ''')
        assert _collect_http_exception_dependencies({"service.py": source}) == [
            ("service.py", 2, "fastapi.HTTPException"),
            ("service.py", 5, "starlette.exceptions.HTTPException"),
        ]

    @pytest.mark.parametrize("module", [
        "fastapi", "fastapi.exceptions", "starlette", "starlette.exceptions",
    ])
    def test_wildcard_cannot_hide_exception_dependency(self, module):
        assert _collect_http_exception_dependencies({
            "service.py": f"from {module} import *\n",
        }) == [("service.py", 1, f"{module}.*")]

    def test_business_errors_comments_and_strings_are_allowed(self):
        source = dedent('''\
            from app.services.exceptions import BusinessError
            from app.services import exceptions as domain
            # from fastapi import HTTPException
            note = "from starlette.exceptions import HTTPException; fastapi.HTTPException(422)"
            def service():
                raise BusinessError("INVALID_PARAM", "invalid")
            def other_service():
                raise domain.BusinessError("INVALID_PARAM", "invalid")
        ''')
        assert _collect_http_exception_dependencies({"service.py": source}) == []

    def test_unrelated_http_exception_name_is_allowed(self):
        source = "import http.client as transport\nerror_type = transport.HTTPException\n"
        assert _collect_http_exception_dependencies({"service.py": source}) == []

    @pytest.mark.parametrize("reverse", [False, True])
    def test_sibling_functions_do_not_overwrite_import_aliases(self, reverse):
        invalid = "def fail():\n    import fastapi as errors\n    raise errors.HTTPException(422)\n"
        valid = "def transport_error():\n    import http.client as errors\n    return errors.HTTPException\n"
        source = valid + invalid if reverse else invalid + valid
        assert _collect_http_exception_dependencies({"service.py": source}) == [
            ("service.py", 6 if reverse else 3, "fastapi.HTTPException"),
        ]

    @pytest.mark.parametrize("source, expected", [
        ('''\
            import starlette.exceptions as errors
            def transport_error():
                import http.client as errors
                return errors.HTTPException
            def fail():
                raise errors.HTTPException(422)
        ''', [(6, "starlette.exceptions.HTTPException")]),
        ('''\
            def outer():
                import fastapi as errors
                def transport_error():
                    import http.client as errors
                    return errors.HTTPException
                async def fail():
                    raise errors.HTTPException(422)
                return fail
        ''', [(7, "fastapi.HTTPException")]),
        ('''\
            import http.client as errors
            class Owner:
                import fastapi as errors
                error_type = errors.HTTPException
                def transport_error(self):
                    return errors.HTTPException
        ''', [(4, "fastapi.HTTPException")]),
        ('''\
            import fastapi as errors
            class Owner:
                import http.client as errors
                error_type = errors.HTTPException
                def fail(self):
                    raise errors.HTTPException(422)
        ''', [(6, "fastapi.HTTPException")]),
        ('''\
            import fastapi as errors
            def from_argument(errors):
                return errors.HTTPException
            def from_local(other):
                errors = other
                return errors.HTTPException
        ''', []),
    ], ids=["module-alias", "closure-alias", "class-local", "method-outer", "local-shadow"])
    def test_scoped_alias_resolution(self, source, expected):
        assert _collect_http_exception_dependencies({"service.py": dedent(source)}) == [
            ("service.py", line, name) for line, name in expected
        ]

    def test_empty_scan_fails(self):
        with pytest.raises(AssertionError, match="empty services scan"):
            _collect_http_exception_dependencies({})
