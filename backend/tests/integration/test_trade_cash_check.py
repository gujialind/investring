# ============================================================================
# 集成测试：交易确认现金校验与自然键防重 (test_trade_cash_check.py)
# ============================================================================
# 覆盖 issue #70/#78/#82/#182：
# - 可用现金时点口径（流出锚定 trade_date）下的创建拦截（事故复刻）
# - 无快照组合的超额买入拦截（#515：可用现金重复计账的危害面）
# - confirm_single_trade 买入现金/卖出份额可用量校验（含 skip_available_check 与自身加回）
# - create_trade 自然键防重（DUPLICATE_TRADE / allow_duplicate）
# ============================================================================

import pytest
from datetime import date
from decimal import Decimal

from app.models.trade import Trade
from app.models.product import Product
from app.services.exceptions import BusinessError
from app.services.trade_service import (
    create_trade as create_trade_service,
    confirm_single_trade,
    cancel_trade as cancel_trade_service,
)
from tests.factories import (
    create_portfolio, create_platform, create_trade,
    create_position_snapshot, create_value_snapshot, create_price_record,
)


SNAP = date(2025, 1, 6)  # 周一，基线快照日
T = date(2025, 1, 7)     # 周二，交易日（> 快照日）


def _seed_cash(db, portfolio_code, platform_code, cash, snap_date=SNAP):
    """构造基线：组合 + 平台 + 价值快照 + CASH 持仓快照（可用现金 = cash）"""
    create_portfolio(db, code=portfolio_code, status="active")
    create_platform(db, code=platform_code)
    create_value_snapshot(db, portfolio_code, snap_date,
                          total_value=cash, total_shares=cash, unit_price=1.0)
    create_position_snapshot(
        db, portfolio_code, "CASH", "", snap_date,
        cash_amount=cash, platform_code=platform_code,
    )


def _get_product(db, code, market):
    return db.query(Product).filter(
        Product.code == code, Product.market == market
    ).first()


def _create_otc_buy(db, portfolio_code, platform_code, amount, trade_date=T):
    """创建场外基金买入（000300.OF，confirm_days=1，confirm_date=下一交易日）"""
    return create_trade_service(
        db,
        portfolio_code=portfolio_code,
        product_code="000300.OF",
        market="CN_OTC",
        trade_type="buy",
        trade_date=trade_date,
        actual_amount=Decimal(str(amount)),
        platform_code=platform_code,
    )


class TestIncidentReplay:
    """事故复刻（#78）：同日两笔同额基金买入，第二笔须被拦截"""

    def test_second_buy_blocked_after_first_confirmed(self, test_db):
        """第一笔创建并确认（confirm_date=下一交易日）后，第二笔创建被拦截。

        旧口径下第一笔的 CASH sell 腿 confirmed 后 confirm_date(T+1) > as_of(T)
        导致预留隐身；新口径按 trade_date 扣减，第二笔必拦。
        """
        _seed_cash(test_db, "IC_P1", "IC_PL1", 4000)
        create_price_record(test_db, "000300.OF", "CN_OTC", T, unit_price=1.0)

        t1 = _create_otc_buy(test_db, "IC_P1", "IC_PL1", 3000)
        test_db.flush()
        product = _get_product(test_db, "000300.OF", "CN_OTC")
        confirm_single_trade(test_db, t1, product)
        test_db.flush()
        assert t1.status == "confirmed"
        assert t1.confirm_date == date(2025, 1, 8)  # 下一交易日

        with pytest.raises(BusinessError) as exc:
            _create_otc_buy(test_db, "IC_P1", "IC_PL1", 3000)
        assert exc.value.code == "INSUFFICIENT_CASH"

    def test_second_buy_blocked_while_first_pending(self, test_db):
        """第一笔仍 pending 时，第二笔同样被拦（既有行为不回退）"""
        _seed_cash(test_db, "IC_P2", "IC_PL2", 4000)

        t1 = _create_otc_buy(test_db, "IC_P2", "IC_PL2", 3000)
        test_db.flush()
        assert t1.status == "pending"

        with pytest.raises(BusinessError) as exc:
            _create_otc_buy(test_db, "IC_P2", "IC_PL2", 3000)
        assert exc.value.code == "INSUFFICIENT_CASH"


class TestNoSnapshotOverBuyBlocked:
    """无快照组合的创建拦截（#515）：危害面而非仅函数返回值

    无快照时可用现金曾被「compute_cash_balance 全量基线 + 1970 哨兵增量」重复
    累计，约为真实到账额的 2 倍，于是**钱不够也能下单**，直到快照生成才以
    NEGATIVE_CASH 暴露。本类走真实 service 路径（create_trade →
    validate_buy_cash_with_addback），锁住「超出真实余额必拒、恰好够用放行」。
    """

    def _seed_confirmed_arrival(self, db, pc, plat, amount):
        """无任何快照；仅一笔已确认到账（真实可用现金 = amount）"""
        create_portfolio(db, code=pc, status="active")
        create_platform(db, code=plat)
        create_trade(
            db, pc, "CASH", "",
            trade_type="buy", amount=amount, status="confirmed",
            trade_date=SNAP, confirm_date=SNAP, platform_code=plat,
        )

    def test_over_buy_rejected(self, test_db):
        """真实可用 100，创建 150 的买入 → INSUFFICIENT_CASH（旧实现在此放行）"""
        self._seed_confirmed_arrival(test_db, "NSB_P1", "NSB_PL1", 100)
        with pytest.raises(BusinessError) as exc:
            _create_otc_buy(test_db, "NSB_P1", "NSB_PL1", 150)
        assert exc.value.code == "INSUFFICIENT_CASH"
        assert Decimal(exc.value.details["available"]) == Decimal("100")
        assert Decimal(exc.value.details["deficit"]) == Decimal("50")

    def test_exact_balance_buy_allowed(self, test_db):
        """恰好够用（100）应放行——修复不得收紧合法操作"""
        self._seed_confirmed_arrival(test_db, "NSB_P2", "NSB_PL2", 100)
        t = _create_otc_buy(test_db, "NSB_P2", "NSB_PL2", 100)
        test_db.flush()
        assert t.status == "pending"
        assert Decimal(t.actual_amount) == Decimal("100")


class TestConfirmCashCheck:
    """confirm_single_trade 买入确认现金校验（#70/#78）"""

    def _seed_pending_buy_and_drain(self, db, pc, plat):
        """现金 4000，创建 pending 买入 3000，再用确认赎回流出 2000 耗现金"""
        _seed_cash(db, pc, plat, 4000)
        create_price_record(db, "000300.OF", "CN_OTC", T, unit_price=1.0)
        t1 = _create_otc_buy(db, pc, plat, 3000)
        db.flush()
        # 模拟赎回流出：confirmed CASH sell（trade_date 早于生效确认日）
        create_trade(
            db, pc, "CASH", "",
            trade_type="sell", amount=2000, status="confirmed",
            trade_date=T, confirm_date=T, platform_code=plat,
        )
        return t1

    def test_confirm_insufficient_cash_rejected(self, test_db):
        """确认时现金已被耗尽 → INSUFFICIENT_CASH（details 含缺口）"""
        t1 = self._seed_pending_buy_and_drain(test_db, "CF_P1", "CF_PL1")
        product = _get_product(test_db, "000300.OF", "CN_OTC")
        with pytest.raises(BusinessError) as exc:
            confirm_single_trade(test_db, t1, product)
        assert exc.value.code == "INSUFFICIENT_CASH"
        # 需 3000、可用 4000-2000（赎回）=2000（自身腿已加回），缺口 1000
        assert Decimal(exc.value.details["deficit"]) == Decimal("1000")
        assert Decimal(exc.value.details["required"]) == Decimal("3000")
        assert Decimal(exc.value.details["available"]) == Decimal("2000")
        assert t1.status == "pending"

    def test_skip_available_check_allows_confirm(self, test_db):
        """skip_available_check=True（auto_confirm 场景）跳过校验可确认"""
        t1 = self._seed_pending_buy_and_drain(test_db, "CF_P2", "CF_PL2")
        product = _get_product(test_db, "000300.OF", "CN_OTC")
        confirm_single_trade(test_db, t1, product, skip_available_check=True)
        test_db.flush()
        assert t1.status == "confirmed"

    def test_auto_confirm_oversell_skipped_not_raised(self, test_db):
        """auto_confirm 路径（skip_available_check=True）超卖不抛、照常确认
        （#182：confirm 卖出份额校验不破坏 auto_confirm_failed 部分成功语义）"""
        create_portfolio(test_db, code="CF_P6", status="active")
        create_platform(test_db, code="CF_PL6")
        create_value_snapshot(test_db, "CF_P6", SNAP,
                              total_value=1500, total_shares=1000, unit_price=1.5)
        create_position_snapshot(
            test_db, "CF_P6", "510300.SH", "CN_EXCHANGE", SNAP,
            shares=100, platform_code="CF_PL6",
        )
        # 份额 100 已被另一笔 pending 卖出占用，本笔 100 为超卖
        # （两笔均经工厂直插，绕过创建侧校验以构造超卖存量）
        create_trade(
            test_db, "CF_P6", "510300.SH", "CN_EXCHANGE",
            trade_type="sell", shares=100, price=1.5, status="pending",
            trade_date=T, confirm_date=T, platform_code="CF_PL6",
            transfer_group="test_oversell_other",
        )
        t1 = create_trade(
            test_db, "CF_P6", "510300.SH", "CN_EXCHANGE",
            trade_type="sell", shares=100, price=1.5, status="pending",
            trade_date=T, confirm_date=T, platform_code="CF_PL6",
            transfer_group="test_oversell_self",
        )
        product = _get_product(test_db, "510300.SH", "CN_EXCHANGE")
        # 手动确认路径应被拦截
        with pytest.raises(BusinessError) as exc:
            confirm_single_trade(test_db, t1, product)
        assert exc.value.code == "INSUFFICIENT_SHARES"
        # auto_confirm 路径跳过校验，不抛异常
        confirm_single_trade(test_db, t1, product, skip_available_check=True)
        test_db.flush()
        assert t1.status == "confirmed"

    def test_confirm_full_cash_own_leg_added_back(self, test_db):
        """现金恰好全额买入时确认不被误拒（自身在途腿加回生效）"""
        _seed_cash(test_db, "CF_P3", "CF_PL3", 3000)
        create_price_record(test_db, "000300.OF", "CN_OTC", T, unit_price=1.0)
        t1 = _create_otc_buy(test_db, "CF_P3", "CF_PL3", 3000)
        test_db.flush()
        product = _get_product(test_db, "000300.OF", "CN_OTC")
        confirm_single_trade(test_db, t1, product)
        test_db.flush()
        assert t1.status == "confirmed"

    def test_sell_confirm_not_checked(self, test_db):
        """卖出确认不做现金校验（零现金也可确认）"""
        create_portfolio(test_db, code="CF_P4", status="active")
        create_platform(test_db, code="CF_PL4")
        create_value_snapshot(test_db, "CF_P4", SNAP,
                              total_value=1500, total_shares=1000, unit_price=1.5)
        create_position_snapshot(
            test_db, "CF_P4", "510300.SH", "CN_EXCHANGE", SNAP,
            shares=1000, platform_code="CF_PL4",
        )
        t1 = create_trade_service(
            test_db,
            portfolio_code="CF_P4",
            product_code="510300.SH",
            market="CN_EXCHANGE",
            trade_type="sell",
            trade_date=T,
            shares=Decimal("100"),
            price=Decimal("1.5"),
            actual_amount=Decimal("150"),
            platform_code="CF_PL4",
        )
        test_db.flush()
        product = _get_product(test_db, "510300.SH", "CN_EXCHANGE")
        confirm_single_trade(test_db, t1, product)
        test_db.flush()
        assert t1.status == "confirmed"

    def test_cash_leg_confirm_not_checked(self, test_db):
        """CASH 腿（product_code=CASH 的 buy）确认不做现金校验"""
        create_portfolio(test_db, code="CF_P5", status="active")
        create_platform(test_db, code="CF_PL5")
        cash_trade = create_trade(
            test_db, "CF_P5", "CASH", "",
            trade_type="buy", amount=1000, status="pending",
            trade_date=T, confirm_date=T, platform_code="CF_PL5",
        )
        confirm_single_trade(test_db, cash_trade, None)
        test_db.flush()
        assert cash_trade.status == "confirmed"


class TestDuplicateTrade:
    """交易创建自然键防重（#82）"""

    def _etf_buy(self, db, pc, plat, amount="2000", allow_duplicate=False):
        return create_trade_service(
            db,
            portfolio_code=pc,
            product_code="510300.SH",
            market="CN_EXCHANGE",
            trade_type="buy",
            trade_date=T,
            actual_amount=Decimal(amount),
            price=Decimal("1.5"),
            platform_code=plat,
            allow_duplicate=allow_duplicate,
        )

    def test_pending_duplicate_rejected(self, test_db):
        """命中 pending 同参数交易 → DUPLICATE_TRADE（details 含既有 id）"""
        _seed_cash(test_db, "DP_P1", "DP_PL1", 10000)
        t1 = self._etf_buy(test_db, "DP_P1", "DP_PL1")
        test_db.flush()
        with pytest.raises(BusinessError) as exc:
            self._etf_buy(test_db, "DP_P1", "DP_PL1")
        assert exc.value.code == "DUPLICATE_TRADE"
        assert exc.value.details["existing_trade_id"] == t1.id

    def test_confirmed_duplicate_rejected(self, test_db):
        """命中 confirmed 同参数交易 → DUPLICATE_TRADE"""
        _seed_cash(test_db, "DP_P2", "DP_PL2", 10000)
        t1 = self._etf_buy(test_db, "DP_P2", "DP_PL2")
        test_db.flush()
        product = _get_product(test_db, "510300.SH", "CN_EXCHANGE")
        confirm_single_trade(test_db, t1, product)
        test_db.flush()
        with pytest.raises(BusinessError) as exc:
            self._etf_buy(test_db, "DP_P2", "DP_PL2")
        assert exc.value.code == "DUPLICATE_TRADE"

    def test_cancelled_then_recreate_allowed(self, test_db):
        """cancelled 记录不算重复，取消后可重建"""
        _seed_cash(test_db, "DP_P3", "DP_PL3", 10000)
        t1 = _create_otc_buy(test_db, "DP_P3", "DP_PL3", 2000)
        test_db.flush()
        cancel_trade_service(test_db, t1)
        test_db.flush()
        t2 = _create_otc_buy(test_db, "DP_P3", "DP_PL3", 2000)
        test_db.flush()
        assert t2.status == "pending"

    def test_different_platform_allowed(self, test_db):
        """不同 platform_code 不算重复"""
        _seed_cash(test_db, "DP_P4", "DP_PL4A", 10000)
        create_platform(test_db, code="DP_PL4B")
        create_position_snapshot(
            test_db, "DP_P4", "CASH", "", SNAP,
            cash_amount=10000, platform_code="DP_PL4B",
        )
        self._etf_buy(test_db, "DP_P4", "DP_PL4A")
        test_db.flush()
        t2 = self._etf_buy(test_db, "DP_P4", "DP_PL4B")
        test_db.flush()
        assert t2.status == "pending"

    def test_allow_duplicate_flag_allows(self, test_db):
        """allow_duplicate=True 强制放行重复交易"""
        _seed_cash(test_db, "DP_P5", "DP_PL5", 10000)
        self._etf_buy(test_db, "DP_P5", "DP_PL5")
        test_db.flush()
        t2 = self._etf_buy(test_db, "DP_P5", "DP_PL5", allow_duplicate=True)
        test_db.flush()
        assert t2.status == "pending"
        count = test_db.query(Trade).filter(
            Trade.portfolio_code == "DP_P5",
            Trade.product_code == "510300.SH",
            Trade.trade_type == "buy",
        ).count()
        assert count == 2

    def test_sell_duplicate_rejected_by_shares(self, test_db):
        """卖出以量化后份额匹配重复"""
        create_portfolio(test_db, code="DP_P6", status="active")
        create_platform(test_db, code="DP_PL6")
        create_value_snapshot(test_db, "DP_P6", SNAP,
                              total_value=1500, total_shares=1000, unit_price=1.5)
        create_position_snapshot(
            test_db, "DP_P6", "510300.SH", "CN_EXCHANGE", SNAP,
            shares=1000, platform_code="DP_PL6",
        )

        def _sell():
            return create_trade_service(
                test_db,
                portfolio_code="DP_P6",
                product_code="510300.SH",
                market="CN_EXCHANGE",
                trade_type="sell",
                trade_date=T,
                shares=Decimal("100"),
                price=Decimal("1.5"),
                actual_amount=Decimal("150"),
                platform_code="DP_PL6",
            )

        _sell()
        test_db.flush()
        with pytest.raises(BusinessError) as exc:
            _sell()
        assert exc.value.code == "DUPLICATE_TRADE"


class TestDuplicateTradeRest:
    """REST 契约：allow_duplicate 字段透传与 details 透出"""

    def test_rest_duplicate_then_allow_flag(self, client, admin_headers, test_db):
        _seed_cash(test_db, "DR_P1", "DR_PL1", 10000)
        payload = {
            "portfolio_code": "DR_P1",
            "product_code": "510300.SH",
            "market": "CN_EXCHANGE",
            "trade_type": "buy",
            "amount": 2000.0,
            "price": 1.5,
            "platform_code": "DR_PL1",
            "trade_date": T.isoformat(),
        }
        first = client.post("/api/trades", json=payload, headers=admin_headers)
        assert first.status_code in (200, 201), first.json()
        first_id = first.json()["id"]

        dup = client.post("/api/trades", json=payload, headers=admin_headers)
        assert dup.status_code == 422
        detail = dup.json()["detail"]
        assert detail["error"] == "DUPLICATE_TRADE"
        assert detail["details"]["existing_trade_id"] == first_id

        forced = client.post(
            "/api/trades",
            json={**payload, "allow_duplicate": True},
            headers=admin_headers,
        )
        assert forced.status_code in (200, 201), forced.json()
