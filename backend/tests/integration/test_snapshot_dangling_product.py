# ============================================================================
# 集成测试：悬空产品码响亮失败（issue #906 修复 A）
# ============================================================================
# 缺陷形态：非现金持仓行引用的 (product_code, market) 在 product 表无行时，
# 生成侧 if is_cash / elif product 两个分支都不进 → 市值留 None → 汇总按 0
# 静默累加，净值系统性偏小且无告警；预校验侧 if not product: continue 同病。
#
# 修复后口径（SPEC v3.1 §2/§6）：产品行缺失视同缺价（#701「『缺价』只有一种
# 形态」同族），并入 MISSING_NAV 响亮失败、整体回滚零落库。分流与现有缺价
# 逐字同构：纯净值/产品缺失（failed checks 全为 price_data）→ MISSING_NAV；
# 混合失败（如 pending 交易 + 缺产品）→ ValueError → VALIDATION_FAILED。
#
# 造场景手段（SPEC §6，测试专用、非运行期行为）：测试库 FK 强制开启
# （conftest.py:93/:180），经 delete_product 造不出悬空引用，故在 test_db
# 外层事务开启**之前**用独立 session 于同一连接开 FK-off 窗口直插悬空持仓行，
# 成对恢复后再交给测试事务。SQLite 的 PRAGMA foreign_keys 在事务内是 no-op，
# 这是必须前置的原因；MySQL 的 FOREIGN_KEY_CHECKS 会话级可事务内切换，统一
# 走前置窗口保持双方言同构。StaticPool 单连接下 fixture 先于 test_db 实例化
# （测试签名参数顺序保证）、teardown 反序清理，不污染后续用例。
# ============================================================================

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.models import Portfolio, PortfolioPosition, PortfolioValueSnapshot
from app.services.exceptions import BusinessError
from app.services.snapshot_service import (
    _generate_portfolio_position,
    generate_daily_snapshots,
    validate_snapshot_dependencies,
)
from tests.factories import (
    create_portfolio,
    create_position_snapshot,
    create_value_snapshot,
    create_investor_holding,
    create_product,
    create_price_record,
    create_trade,
)
from tests.integration.snapshot_helpers import capture_portfolio_state

# conftest 交易日历：工作日均为交易日（与 test_snapshot_nav_strict 同锚）
D0 = date(2025, 6, 6)         # 周五（最新快照日）
NEXT_DAY = date(2025, 6, 9)   # 周一（生成目标日）

DANGLING_PORT = "DANGLING_PORT"
GONE_CODE = "GONE.OF"
GONE_MARKET = "CN_OTC"


def _set_fk(db, enabled: bool) -> None:
    """按方言切换外键强制（成对调用，见模块 doc 的造场景手段说明）。"""
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        db.execute(text(f"PRAGMA foreign_keys={'ON' if enabled else 'OFF'}"))
    elif dialect == "mysql":
        db.execute(text(f"SET FOREIGN_KEY_CHECKS={1 if enabled else 0}"))
    else:
        raise AssertionError(f"未支持的测试方言: {dialect}")


@pytest.fixture
def dangling_fund_portfolio(test_engine, _seed_base_data):
    """FK-off 窗口落一行悬空基金持仓（product 行恒不存在）+ active 组合。

    必须先于 test_db 实例化（签名顺序保证）：SQLite 事务内 PRAGMA 是 no-op。
    yield 后 test_db 先回滚测试内写入，本 fixture 再清理前置提交的行。
    """
    SessionFactory = sessionmaker(bind=test_engine)
    db = SessionFactory()
    try:
        db.add(Portfolio(code=DANGLING_PORT, name="悬空产品测试组合", status="active"))
        db.commit()
        _set_fk(db, False)
        try:
            db.add(PortfolioPosition(
                portfolio_code=DANGLING_PORT,
                platform_code="MYCF",
                product_code=GONE_CODE,
                market=GONE_MARKET,
                shares=Decimal("100"),
                unit_price=Decimal("1.0"),
                cost_price=Decimal("1.0"),
                market_value=Decimal("100"),
                snapshot_date=D0,
            ))
            db.commit()
        finally:
            _set_fk(db, True)
        db.commit()
        yield {
            "portfolio_code": DANGLING_PORT,
            "product_code": GONE_CODE,
            "market": GONE_MARKET,
        }
    finally:
        db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == DANGLING_PORT
        ).delete()
        db.query(Portfolio).filter(Portfolio.code == DANGLING_PORT).delete()
        db.commit()
        db.close()


def _complete_d0_snapshots(db, portfolio_code: str) -> None:
    """补齐 D0 市值/投资人快照（持仓行已由 fixture 前置落库）。"""
    create_value_snapshot(
        db, portfolio_code, D0,
        total_value=100.0, total_shares=100.0, unit_price=1.0,
    )
    create_investor_holding(db, portfolio_code, "VIEWER", D0, shares=100.0)


class TestDanglingProductGeneration:
    """生成侧 + 预校验侧：悬空产品码 → MISSING_NAV 响亮失败、零落库。"""

    def test_generate_api_rejects_with_missing_nav_and_zero_writes(
        self, dangling_fund_portfolio, client, admin_headers, test_db
    ):
        """验收断言 2：API 生成 422 MISSING_NAV，点名悬空产品，目标日零快照。"""
        _complete_d0_snapshots(test_db, DANGLING_PORT)
        before = capture_portfolio_state(test_db, DANGLING_PORT)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": DANGLING_PORT, "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "MISSING_NAV"
        assert f"{GONE_CODE}({GONE_MARKET})" in detail["message"]
        assert "产品行缺失" in detail["message"]

        # 整体回滚：三表与账务逐字不变（钉住「market_value 为空 ⇒ 按 0 计入」
        # 的静默形态不再可能发生——失败发生在汇总之前，验收断言 3）
        assert capture_portfolio_state(test_db, DANGLING_PORT) == before
        assert test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == DANGLING_PORT,
            PortfolioValueSnapshot.snapshot_date == NEXT_DAY,
        ).count() == 0

    def test_generation_side_else_raises_missing_nav(
        self, dangling_fund_portfolio, test_db
    ):
        """生成点兜底：绕过预校验直调 _generate_portfolio_position 仍响亮失败。

        预校验读「最新快照日持仓」，生成侧读「前快照 + 窗口内交易」——两者
        覆盖面不同（新确认持仓不在前日快照中时预校验失明），else 分支必须
        独立成立（SPEC §1 环节 3）。
        """
        _complete_d0_snapshots(test_db, DANGLING_PORT)
        with pytest.raises(BusinessError) as exc_info:
            _generate_portfolio_position(test_db, DANGLING_PORT, NEXT_DAY)
        assert exc_info.value.code == "MISSING_NAV"
        assert f"{GONE_CODE}({GONE_MARKET}) [产品行缺失]" in exc_info.value.details["missing"]

    def test_static_precheck_reports_missing_product(
        self, dangling_fund_portfolio, test_db
    ):
        """预校验侧（static_only，重算整区间前置）：price_data failed 点名产品缺失。"""
        _complete_d0_snapshots(test_db, DANGLING_PORT)
        checks = validate_snapshot_dependencies(
            test_db, DANGLING_PORT, NEXT_DAY, static_only=True
        )
        price_check = next(c for c in checks if c["check_type"] == "price_data")
        assert price_check["status"] == "failed"
        assert f"{GONE_CODE}({GONE_MARKET})" in price_check["message"]
        assert "产品行缺失" in price_check["message"]

    def test_recalculate_precheck_blocks_before_any_delete(
        self, dangling_fund_portfolio, client, admin_headers, test_db
    ):
        """重算整区间预校验响亮失败，未删除任何快照（与缺价预校验同构）。

        分流披露（SPEC §6 硬要求）：重算前置预校验对**任何**静态失败统一
        ValueError（现有缺价同此），router 映射 VALIDATION_FAILED；纯产品
        缺失在单日 generate 路径归 MISSING_NAV（见上一用例与分流用例）。
        """
        _complete_d0_snapshots(test_db, DANGLING_PORT)
        before = capture_portfolio_state(test_db, DANGLING_PORT)

        resp = client.post(
            "/api/snapshots/recalculate?confirm=true",
            json={
                "portfolio_code": DANGLING_PORT,
                "start_date": D0.isoformat(),
                "end_date": NEXT_DAY.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "VALIDATION_FAILED"
        assert "产品行缺失" in detail["message"]
        assert capture_portfolio_state(test_db, DANGLING_PORT) == before

    def test_mixed_failure_stays_validation_failed(
        self, dangling_fund_portfolio, client, admin_headers, test_db
    ):
        """混合失败（pending 交易 + 缺产品）保持 ValueError → VALIDATION_FAILED。

        分流依据：generate_daily_snapshots 的 all(check_type == "price_data")
        判据（#96 既有口径）——产品缺失并入 price_data 后与缺价逐字同构。
        """
        _complete_d0_snapshots(test_db, DANGLING_PORT)
        create_trade(
            test_db, DANGLING_PORT, "510300.SH", "CN_EXCHANGE",
            trade_date=D0, status="pending",
        )
        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": DANGLING_PORT, "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "VALIDATION_FAILED"
        assert "产品行缺失" in detail["message"]

    def test_normal_missing_price_message_unchanged(
        self, client, admin_headers, test_db
    ):
        """回归 #701：正常产品缺价仍 MISSING_NAV，消息形态逐字不变（无产品缺失段）。"""
        port = create_portfolio(test_db, code="DANGLING_REG", status="active")
        create_product(test_db, code="REG.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_position_snapshot(
            test_db, port.code, "REG.OF", "CN_OTC",
            snapshot_date=D0, shares=100.0, unit_price=1.0,
            cost_price=1.0, market_value=100.0, platform_code="MYCF",
        )
        create_value_snapshot(test_db, port.code, D0,
                              total_value=100.0, total_shares=100.0, unit_price=1.0)
        create_investor_holding(test_db, port.code, "VIEWER", D0, shares=100.0)
        create_price_record(test_db, "REG.OF", "CN_OTC", D0, 1.0)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == "MISSING_NAV"
        assert "REG.OF(CN_OTC) [T=2025-06-09]" in detail["message"]
        assert "产品行缺失" not in detail["message"]