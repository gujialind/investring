# ============================================================================
# 集成测试：快照生成净值严格匹配 (test_snapshot_nav_strict.py)
# ============================================================================
# issue #96：快照生成净值必须严格与快照日一致——取不到即拒绝生成（MISSING_NAV），
# 禁止静默回退到更早净值。
# issue #178：场内 QDII 与普通场内产品一致取 target_date 当日收盘价（T+0），
# 仅场外 QDII 保持 T-1 滞后口径（见 TestExchangeQdiiPricing）。
# issue #228：滞后取价泛化为产品列 `nav_lag_days` 驱动——0=严格取当日、N>0=严格取
# 前第 N 个交易日净值；`is_qdii` 降级为纯展示标签，不再参与取价分支（见
# TestNavLagDaysPricing：非 QDII 也可设 1、is_qdii=True 但 lag=0 仍取当日）。
#
# 覆盖验收断言：
# 1. 缺 target_date 当日净值 → generate 422 MISSING_NAV，指出缺失产品与日期
# 2. 补齐净值后生成成功，持仓 unit_price == 当日净值
# 3. nav_lag_days=1（如场外 QDII）：T-1 缺失拒绝（不回退 T-2）；T-1 存在严格用 T-1
# 4. 净值齐全的正常交易日生成不回归
# 5. 被拒绝后不产生快照（目标日无快照行、最新快照日不变）
# 附：recalculate 预校验同样严格（删除任何快照前拦截）；生成点硬性兜底
#    （新确认持仓不在前日快照中、闸门覆盖不到时，_generate_portfolio_position
#    直接抛 MISSING_NAV）。
# ============================================================================

from datetime import date
from decimal import Decimal

import pytest

from app.models import PortfolioPosition, PortfolioValueSnapshot, TradingCalendar
from app.services.exceptions import BusinessError
from app.services.snapshot_service import generate_daily_snapshots, validate_snapshot_dependencies
from tests.factories import (
    create_portfolio,
    create_position_snapshot,
    create_value_snapshot,
    create_investor_holding,
    create_product,
    create_price_record,
    create_trade,
)

# conftest 交易日历：工作日均为交易日
D0 = date(2025, 6, 6)         # 周五（最新快照日）
NEXT_DAY = date(2025, 6, 9)   # 周一（下一交易日 = 生成目标日）
T_MINUS_2 = date(2025, 6, 5)  # 周四（相对 NEXT_DAY 的 T-2）
LAG2_TARGET = date(2025, 6, 16)
LAG2_NAV_DATE = date(2025, 6, 5)


@pytest.fixture
def closed_week_calendar(test_db):
    """模拟 6/9–6/13 连续闭市，不修改 session 种子。"""
    closed_dates = tuple(date(2025, 6, day) for day in range(9, 14))
    for day in closed_dates:
        row = test_db.query(TradingCalendar).filter_by(calendar_date=day).one()
        row.is_open = False
    test_db.flush()
    assert dict(test_db.query(
        TradingCalendar.calendar_date, TradingCalendar.is_open,
    ).filter(TradingCalendar.calendar_date.in_(closed_dates)).all()) == {
        day: False for day in closed_dates
    }


@pytest.fixture
def lag2_snapshot(test_db, closed_week_calendar):
    port = create_portfolio(test_db, code="NAV_LAG2", status="active")
    product = create_product(
        test_db, code="LAG2.OF", market="CN_OTC", nav_lag_days=2,
    )
    _setup_fund_snapshot(test_db, port.code, product.code, product.market, D0)
    for price_date, price in (
        (date(2025, 6, 4), 1.1),
        (D0, 1.6),
        (date(2025, 6, 12), 1.8),
        (date(2025, 6, 14), 1.9),
        (LAG2_TARGET, 3.0),
    ):
        create_price_record(test_db, product.code, product.market, price_date, price)
    return port, product


def _setup_fund_snapshot(db, portfolio_code: str, product_code: str, market: str,
                         snapshot_date: date, shares: float = 100.0):
    """制造指定日的完整三表快照：基金持仓 shares 份（unit_price=1.0）"""
    create_position_snapshot(
        db, portfolio_code, product_code, market,
        snapshot_date=snapshot_date, shares=shares, unit_price=1.0,
        cost_price=1.0, market_value=shares, platform_code="MYCF",
    )
    create_value_snapshot(
        db, portfolio_code, snapshot_date,
        total_value=shares, total_shares=shares, unit_price=1.0,
    )
    create_investor_holding(db, portfolio_code, "VIEWER", snapshot_date, shares=shares)


def _setup_cash_snapshot(db, portfolio_code: str, snapshot_date: date, amount: float = 10000.0):
    """制造指定日的完整三表快照（仅 CASH 持仓，无需行情数据）"""
    create_position_snapshot(
        db, portfolio_code, "CASH", "",
        snapshot_date=snapshot_date, cash_amount=amount, unit_price=None,
        cost_price=None, market_value=amount, platform_code="MYCF",
    )
    create_value_snapshot(
        db, portfolio_code, snapshot_date,
        total_value=amount, total_shares=amount, unit_price=1.0,
    )
    create_investor_holding(db, portfolio_code, "VIEWER", snapshot_date, shares=amount)


def _snapshot_ids(db, portfolio_code: str):
    return sorted(
        row[0] for row in db.query(PortfolioValueSnapshot.id).filter(
            PortfolioValueSnapshot.portfolio_code == portfolio_code
        ).all()
    )


class TestSnapshotNavStrict:
    """#96 普通基金/QDII 净值严格匹配"""

    def test_missing_target_date_nav_rejected(self, client, admin_headers, test_db):
        """缺 target_date 当日净值 → 422 MISSING_NAV，指出产品与日期，不产生快照（验收 1、5）"""
        port = create_portfolio(test_db, code="NAV_S1", status="active")
        create_product(test_db, code="STRICTA.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        _setup_fund_snapshot(test_db, port.code, "STRICTA.OF", "CN_OTC", D0)
        # 仅 D0 有净值，NEXT_DAY 无净值（旧逻辑会静默回退用 D0 净值）
        create_price_record(test_db, "STRICTA.OF", "CN_OTC", D0, 1.0)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "MISSING_NAV"
        assert "STRICTA.OF" in detail["message"]
        assert "2025-06-09" in detail["message"]

        # 目标日无快照行，最新快照日仍为 D0（不产生缺口/半截快照）
        assert test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == port.code,
            PortfolioValueSnapshot.snapshot_date == NEXT_DAY,
        ).first() is None
        latest = test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == port.code,
        ).order_by(PortfolioValueSnapshot.snapshot_date.desc()).first()
        assert latest.snapshot_date == D0

    def test_generate_uses_target_date_nav_after_sync(self, client, admin_headers, test_db):
        """补齐当日净值后生成成功，unit_price == target_date 当日净值（验收 2、4）"""
        port = create_portfolio(test_db, code="NAV_S2", status="active")
        create_product(test_db, code="STRICTB.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        _setup_fund_snapshot(test_db, port.code, "STRICTB.OF", "CN_OTC", D0)
        create_price_record(test_db, "STRICTB.OF", "CN_OTC", NEXT_DAY, 1.5)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"
        assert resp.json()["success"] is True

        pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == port.code,
            PortfolioPosition.product_code == "STRICTB.OF",
            PortfolioPosition.snapshot_date == NEXT_DAY,
        ).first()
        assert pos is not None
        assert Decimal(str(pos.unit_price)) == Decimal("1.5")
        assert Decimal(str(pos.market_value)) == Decimal("150.0")

    def test_qdii_missing_t1_nav_rejected_no_fallback(self, client, admin_headers, test_db):
        """场外 QDII（nav_lag_days=1）：T-1 缺失即拒绝，不回退 T-2 净值（验收 3 前半）"""
        port = create_portfolio(test_db, code="NAV_S3", status="active")
        create_product(test_db, code="QDIIA.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=2, is_qdii=True, nav_lag_days=1)
        _setup_fund_snapshot(test_db, port.code, "QDIIA.OF", "CN_OTC", D0)
        # 仅 T-2（06-05）有净值，T-1（06-06）缺失
        create_price_record(test_db, "QDIIA.OF", "CN_OTC", T_MINUS_2, 1.8)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "MISSING_NAV"
        assert "QDIIA.OF" in detail["message"]
        assert "2025-06-06" in detail["message"]  # 指出所需 T-1 日期

        assert test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == port.code,
            PortfolioValueSnapshot.snapshot_date == NEXT_DAY,
        ).first() is None

    def test_qdii_strict_t1_nav_used(self, client, admin_headers, test_db):
        """场外 QDII（nav_lag_days=1）：T-1 存在时严格用 T-1，不用 target_date 当日净值（验收 3 后半）"""
        port = create_portfolio(test_db, code="NAV_S4", status="active")
        create_product(test_db, code="QDIIB.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=2, is_qdii=True, nav_lag_days=1)
        _setup_fund_snapshot(test_db, port.code, "QDIIB.OF", "CN_OTC", D0)
        # NEXT_DAY 的 T-1 = D0（06-06）；两日净值不同值以区分取价日
        create_price_record(test_db, "QDIIB.OF", "CN_OTC", D0, 2.0)
        create_price_record(test_db, "QDIIB.OF", "CN_OTC", NEXT_DAY, 3.0)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"

        pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == port.code,
            PortfolioPosition.product_code == "QDIIB.OF",
            PortfolioPosition.snapshot_date == NEXT_DAY,
        ).first()
        assert pos is not None
        assert Decimal(str(pos.unit_price)) == Decimal("2.0")
        assert Decimal(str(pos.market_value)) == Decimal("200.0")

    def test_recalculate_precheck_rejects_missing_nav(self, client, admin_headers, test_db):
        """重算预校验同样严格：区间内含缺净值交易日 → 422 VALIDATION_FAILED，不删任何快照"""
        port = create_portfolio(test_db, code="NAV_S5", status="active")
        create_product(test_db, code="STRICTC.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        _setup_fund_snapshot(test_db, port.code, "STRICTC.OF", "CN_OTC", D0)
        # D0 净值齐全（D0 预校验通过），NEXT_DAY 缺失（旧逻辑 <= 会放行）
        create_price_record(test_db, "STRICTC.OF", "CN_OTC", D0, 1.0)
        ids_before = _snapshot_ids(test_db, port.code)

        resp = client.post(
            "/api/snapshots/recalculate",
            json={
                "portfolio_code": port.code,
                "start_date": D0.isoformat(),
                "end_date": NEXT_DAY.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "VALIDATION_FAILED"
        assert "预校验失败" in detail["message"]
        assert "STRICTC.OF" in detail["message"]
        assert "2025-06-09" in detail["message"]

        # 未删除任何快照（原行 id 不变）
        assert _snapshot_ids(test_db, port.code) == ids_before

    @pytest.mark.parametrize("nav_lag_days, required_date", [
        (0, LAG2_TARGET),
        (2, LAG2_NAV_DATE),
    ])
    def test_new_position_missing_nav_rejected_at_generation(
        self, test_db, closed_week_calendar, nav_lag_days, required_date,
    ):
        """新持仓未进入预校验范围时，生成点仍严格要求指定交易日净值。"""
        port = create_portfolio(test_db, code="NAV_S6", status="active")
        create_product(test_db, code="STRICTD.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       nav_lag_days=nav_lag_days)
        _setup_cash_snapshot(test_db, port.code, D0)
        create_trade(
            test_db, port.code, "STRICTD.OF", "CN_OTC",
            trade_type="buy", shares=100.0, amount=150.0, price=1.5,
            trade_date=D0, confirm_date=LAG2_TARGET, status="confirmed",
        )
        create_price_record(test_db, "STRICTD.OF", "CN_OTC", date(2025, 6, 4), 1.1)
        create_price_record(test_db, "STRICTD.OF", "CN_OTC", D0, 1.6)
        if nav_lag_days:
            create_price_record(test_db, "STRICTD.OF", "CN_OTC", LAG2_TARGET, 3.0)

        checks = validate_snapshot_dependencies(test_db, port.code, LAG2_TARGET)
        assert next(c for c in checks if c["check_type"] == "price_data")["status"] == "passed"
        assert not any(c["status"] == "failed" for c in checks)
        ids_before = _snapshot_ids(test_db, port.code)

        with pytest.raises(BusinessError) as exc:
            generate_daily_snapshots(test_db, port.code, LAG2_TARGET)
        assert exc.value.code == "MISSING_NAV"
        assert "STRICTD.OF(CN_OTC)" in exc.value.message
        assert required_date.isoformat() in exc.value.message
        assert exc.value.details["target_date"] == LAG2_TARGET.isoformat()
        assert _snapshot_ids(test_db, port.code) == ids_before
        assert test_db.query(PortfolioPosition).filter_by(
            portfolio_code=port.code, snapshot_date=LAG2_TARGET,
        ).count() == 0


class TestExchangeQdiiPricing:
    """#178 场内 QDII 快照取价：与普通场内产品一致取 target_date 当日收盘价（T+0）。
    #228 起该行为由 nav_lag_days=0 落库表达（is_qdii=True 也不滞后），
    证明 is_qdii 标签本身不驱动取价"""

    def test_exchange_qdii_uses_target_date_close_price(self, client, admin_headers, test_db):
        """T1：场内 QDII 取当日收盘价；T-1 与当日双价并存时不用 T-1（防回退歧义）"""
        port = create_portfolio(test_db, code="NAV_E1", status="active")
        create_product(test_db, code="EQDIIA.SZ", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0, is_qdii=True, nav_lag_days=0)
        _setup_fund_snapshot(test_db, port.code, "EQDIIA.SZ", "CN_EXCHANGE", D0)
        # T-1（D0）与当日（NEXT_DAY）均有价且不同值，确认取当日
        create_price_record(test_db, "EQDIIA.SZ", "CN_EXCHANGE", D0, 1.7)
        create_price_record(test_db, "EQDIIA.SZ", "CN_EXCHANGE", NEXT_DAY, 1.5)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"

        pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == port.code,
            PortfolioPosition.product_code == "EQDIIA.SZ",
            PortfolioPosition.snapshot_date == NEXT_DAY,
        ).first()
        assert pos is not None
        assert Decimal(str(pos.unit_price)) == Decimal("1.5")
        assert Decimal(str(pos.market_value)) == Decimal("150.0")

    def test_exchange_qdii_missing_target_date_price_rejected(self, client, admin_headers, test_db):
        """T2：场内 QDII 缺当日收盘价（仅 T-1 有价）-> MISSING_NAV，message 标注 [T=当日]，不产生快照"""
        port = create_portfolio(test_db, code="NAV_E2", status="active")
        create_product(test_db, code="EQDIIB.SZ", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0, is_qdii=True, nav_lag_days=0)
        _setup_fund_snapshot(test_db, port.code, "EQDIIB.SZ", "CN_EXCHANGE", D0)
        # 仅 T-1（D0）有价，NEXT_DAY 当日缺失（旧逻辑按 QDII T-1 会放行）
        create_price_record(test_db, "EQDIIB.SZ", "CN_EXCHANGE", D0, 1.8)
        ids_before = _snapshot_ids(test_db, port.code)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "MISSING_NAV"
        assert "EQDIIB.SZ(CN_EXCHANGE)" in detail["message"]
        assert "[T=2025-06-09]" in detail["message"]  # 指向当日而非 T-1

        # 目标日无快照行、已有快照原样保留（复用 _snapshot_ids 断言模式）
        assert test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == port.code,
            PortfolioValueSnapshot.snapshot_date == NEXT_DAY,
        ).first() is None
        assert _snapshot_ids(test_db, port.code) == ids_before

    def test_validation_check_matches_generation_rule(self, client, admin_headers, test_db):
        """T3：completeness 校验与生成口径同步--场内 QDII 仅当日无价 -> failed 指向当日；补齐 -> passed"""
        port = create_portfolio(test_db, code="NAV_E3", status="active")
        create_product(test_db, code="EQDIIC.SZ", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0, is_qdii=True, nav_lag_days=0)
        _setup_fund_snapshot(test_db, port.code, "EQDIIC.SZ", "CN_EXCHANGE", D0)
        create_price_record(test_db, "EQDIIC.SZ", "CN_EXCHANGE", D0, 1.8)

        def _price_check():
            resp = client.get(
                "/api/snapshots/validation",
                params={"portfolio_code": port.code, "target_date": NEXT_DAY.isoformat()},
                headers=admin_headers,
            )
            assert resp.status_code == 200
            checks = resp.json()["checks"]
            return next(c for c in checks if c["check_type"] == "price_data")

        # 当日无价：price_data failed 且 message 指向当日（旧逻辑按 T-1 会误报 passed）
        check = _price_check()
        assert check["status"] == "failed"
        assert "EQDIIC.SZ(CN_EXCHANGE)" in check["message"]
        assert "[T=2025-06-09]" in check["message"]

        # 补齐当日价：passed
        create_price_record(test_db, "EQDIIC.SZ", "CN_EXCHANGE", NEXT_DAY, 1.5)
        check = _price_check()
        assert check["status"] == "passed"

    def test_mixed_portfolio_three_pricing_rules(self, client, admin_headers, test_db):
        """T4：混合组合三口径互不串扰（#228 起口径由 nav_lag_days 表达）——
        场内 QDII lag=0 取当日收盘、场外 QDII lag=1 取 T-1、普通场外 lag=0 取当日净值"""
        port = create_portfolio(test_db, code="NAV_E4", status="active")
        products = [
            # (code, market, is_qdii, nav_lag_days, T-1 价, 当日价, 期望 unit_price)
            ("EQDIID.SZ", "CN_EXCHANGE", True, 0, 2.0, 2.5, Decimal("2.5")),
            ("QDIID.OF", "CN_OTC", True, 1, 3.0, 4.0, Decimal("3.0")),
            ("PLAIND.OF", "CN_OTC", False, 0, 1.0, 1.5, Decimal("1.5")),
        ]
        for code, market, is_qdii, nav_lag_days, _, _, _ in products:
            create_product(
                test_db, code=code, market=market,
                product_type="ETF" if market == "CN_EXCHANGE" else "OEF",
                asset_class_code="ASSET_STOCK",
                confirm_days=0 if market == "CN_EXCHANGE" else 1,
                is_qdii=is_qdii, nav_lag_days=nav_lag_days,
            )
            # D0 三表快照：三产品各 100 份（value/holding 仅一条，防唯一约束冲突）
            create_position_snapshot(
                test_db, port.code, code, market,
                snapshot_date=D0, shares=100.0, unit_price=1.0,
                cost_price=1.0, market_value=100.0, platform_code="MYCF",
            )
        create_value_snapshot(
            test_db, port.code, D0, total_value=300.0, total_shares=300.0, unit_price=1.0,
        )
        create_investor_holding(test_db, port.code, "VIEWER", D0, shares=300.0)

        for code, market, _, _, t1_price, t0_price, _ in products:
            create_price_record(test_db, code, market, D0, t1_price)
            create_price_record(test_db, code, market, NEXT_DAY, t0_price)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"

        for code, market, _, _, _, _, expected in products:
            pos = test_db.query(PortfolioPosition).filter(
                PortfolioPosition.portfolio_code == port.code,
                PortfolioPosition.product_code == code,
                PortfolioPosition.snapshot_date == NEXT_DAY,
            ).first()
            assert pos is not None, f"{code} 快照行缺失"
            assert Decimal(str(pos.unit_price)) == expected, (
                f"{code}({market}) unit_price={pos.unit_price}，期望 {expected}"
            )


class TestNavLagDaysPricing:
    """#228 滞后取价泛化：快照取价日只看产品 `nav_lag_days`，与 is_qdii/market 解耦
    （互认基金等非 QDII 品种可直接设 1；QDII 标签设 0 则不滞后）"""

    def test_non_qdii_nav_lag_1_uses_prev_trading_day_nav(self, client, admin_headers, test_db):
        """非 QDII 产品设 nav_lag_days=1（互认基金场景）→ 快照严格用 T-1 净值"""
        port = create_portfolio(test_db, code="NAV_L1", status="active")
        create_product(test_db, code="HKMR1", market="HK_MUTUAL",
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=1, is_qdii=False, nav_lag_days=1)
        _setup_fund_snapshot(test_db, port.code, "HKMR1", "HK_MUTUAL", D0)
        # T-1（D0）与当日（NEXT_DAY）双价并存且不同值，确认取 T-1
        create_price_record(test_db, "HKMR1", "HK_MUTUAL", D0, 2.0)
        create_price_record(test_db, "HKMR1", "HK_MUTUAL", NEXT_DAY, 3.0)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"

        pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == port.code,
            PortfolioPosition.product_code == "HKMR1",
            PortfolioPosition.snapshot_date == NEXT_DAY,
        ).first()
        assert pos is not None
        assert Decimal(str(pos.unit_price)) == Decimal("2.0")
        assert Decimal(str(pos.market_value)) == Decimal("200.0")

    def test_non_qdii_nav_lag_1_missing_t1_rejected_no_fallback(self, client, admin_headers, test_db):
        """非 QDII nav_lag_days=1 缺 T-1（仅 T-2 与当日有价）→ MISSING_NAV，
        既不回退 T-2 也不改用当日价，message 指出所需 T-1 日期"""
        port = create_portfolio(test_db, code="NAV_L2", status="active")
        create_product(test_db, code="HKMR2", market="HK_MUTUAL",
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=1, is_qdii=False, nav_lag_days=1)
        _setup_fund_snapshot(test_db, port.code, "HKMR2", "HK_MUTUAL", D0)
        # T-2（06-05）与当日（06-09）有价，T-1（06-06）缺失
        create_price_record(test_db, "HKMR2", "HK_MUTUAL", T_MINUS_2, 1.8)
        create_price_record(test_db, "HKMR2", "HK_MUTUAL", NEXT_DAY, 2.2)
        ids_before = _snapshot_ids(test_db, port.code)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["error"] == "MISSING_NAV"
        assert "HKMR2(HK_MUTUAL)" in detail["message"]
        assert "[T-1=2025-06-06]" in detail["message"]

        assert test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == port.code,
            PortfolioValueSnapshot.snapshot_date == NEXT_DAY,
        ).first() is None
        assert _snapshot_ids(test_db, port.code) == ids_before

    @pytest.mark.parametrize("endpoint, error_code", [
        ("generate", "MISSING_NAV"),
        ("recalculate", "VALIDATION_FAILED"),
    ])
    def test_lag2_missing_required_nav_across_closed_week(
        self, client, admin_headers, test_db, lag2_snapshot, endpoint, error_code,
    ):
        port, product = lag2_snapshot
        ids_before = _snapshot_ids(test_db, port.code)
        payload = {"portfolio_code": port.code}
        if endpoint == "generate":
            payload["target_date"] = LAG2_TARGET.isoformat()
        else:
            payload.update(start_date=D0.isoformat(), end_date=LAG2_TARGET.isoformat())

        resp = client.post(
            f"/api/snapshots/{endpoint}", json=payload, headers=admin_headers,
        )
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["error"] == error_code
        assert f"{product.code}({product.market})" in detail["message"]
        assert "[T-2=2025-06-05]" in detail["message"]
        if endpoint == "recalculate":
            assert "预校验失败" in detail["message"]
        assert _snapshot_ids(test_db, port.code) == ids_before
        assert test_db.query(PortfolioPosition).filter_by(
            portfolio_code=port.code, snapshot_date=LAG2_TARGET,
        ).count() == 0

    def test_lag2_validation_and_generation_use_exact_calendar_nav(
        self, client, admin_headers, test_db, lag2_snapshot,
    ):
        port, product = lag2_snapshot

        def price_check():
            resp = client.get(
                "/api/snapshots/validation",
                params={"portfolio_code": port.code, "target_date": LAG2_TARGET.isoformat()},
                headers=admin_headers,
            )
            assert resp.status_code == 200, resp.text
            return next(c for c in resp.json()["checks"] if c["check_type"] == "price_data")

        check = price_check()
        assert check["status"] == "failed"
        assert f"{product.code}({product.market})" in check["message"]
        assert "[T-2=2025-06-05]" in check["message"]

        create_price_record(test_db, product.code, product.market, LAG2_NAV_DATE, 2.0)
        assert price_check()["status"] == "passed"
        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": LAG2_TARGET.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["success"] is True
        pos = test_db.query(PortfolioPosition).filter_by(
            portfolio_code=port.code, product_code=product.code,
            market=product.market, snapshot_date=LAG2_TARGET,
        ).one()
        assert pos.unit_price == Decimal("2.0")
        assert pos.market_value == Decimal("200.0")

    def test_otc_qdii_with_nav_lag_0_uses_target_date_nav(self, client, admin_headers, test_db):
        """场外产品 is_qdii=True 但 nav_lag_days=0 → 仍取当日净值
        （证明取价只由 nav_lag_days 驱动，is_qdii 已是纯展示标签）"""
        port = create_portfolio(test_db, code="NAV_L3", status="active")
        create_product(test_db, code="QDIIL3.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK",
                       confirm_days=2, is_qdii=True, nav_lag_days=0)
        _setup_fund_snapshot(test_db, port.code, "QDIIL3.OF", "CN_OTC", D0)
        create_price_record(test_db, "QDIIL3.OF", "CN_OTC", D0, 2.0)
        create_price_record(test_db, "QDIIL3.OF", "CN_OTC", NEXT_DAY, 3.0)

        resp = client.post(
            "/api/snapshots/generate",
            json={"portfolio_code": port.code, "target_date": NEXT_DAY.isoformat()},
            headers=admin_headers,
        )
        assert resp.status_code == 200, f"Response: {resp.status_code} {resp.json()}"

        pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == port.code,
            PortfolioPosition.product_code == "QDIIL3.OF",
            PortfolioPosition.snapshot_date == NEXT_DAY,
        ).first()
        assert pos is not None
        assert Decimal(str(pos.unit_price)) == Decimal("3.0")
        assert Decimal(str(pos.market_value)) == Decimal("300.0")
