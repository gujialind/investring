# ============================================================================
# 集成测试：金额精度统一 2 位小数 (test_amount_precision.py)
# ============================================================================
# 覆盖金额统一量化到 2 位（ROUND_HALF_UP，issue #94）后的核心场景：
# - issue 复刻：卖出 6837.30 份 × 净值 1.1024 → 回笼 7537.44（2 位），
#   再用 7537.44 买入恰好等于回笼现金，现金闸门精确放行（0.0005 尾差不再拒单）
# - 赎回确认金额 = quantize(shares × nav)，配对 CASH sell 腿同口径
# - 现金分红 cash_change / forced_adjustment 用户填写 cash_change 量化
# - manual_market_value 写入量化
# - 现金转移金额先量化再精确校验
# - 快照 CASH cash_amount 继承 2 位口径（与平台对账一致）
# ============================================================================

import pytest
from datetime import date
from decimal import Decimal

from app.models.portfolio_position import PortfolioPosition
from app.models.trade import Trade
from app.models.manual_market_value import ManualMarketValue
from app.services.cash_transfer_service import create_cash_transfer
from app.services.exceptions import BusinessError
from app.services.position_service import (
    calculate_available_cash,
    update_cash_position,
)
from app.services.share_change_event_service import compute_event_fields
from app.services.subscription_service import (
    confirm_single_subscription,
    create_subscription,
)
from app.services.trade_service import (
    create_trade as create_trade_service,
    confirm_single_trade,
)
from tests.factories import (
    create_portfolio, create_platform, create_product, create_investor,
    create_investor_holding, create_position_snapshot, create_value_snapshot,
    create_price_record,
    create_subscription as factory_create_subscription,
)


SNAP = date(2025, 1, 6)   # 周一，基线快照日
T = date(2025, 1, 7)      # 周二，卖出交易日
T1 = date(2025, 1, 8)     # 周三，卖出确认日（confirm_days=1）/ 买入交易日


def _get_product(db, code, market):
    from app.models.product import Product
    return db.query(Product).filter(
        Product.code == code, Product.market == market
    ).first()


def _seed_sell_portfolio(db, pc, plat):
    """构造 issue #94 场景基线：组合 + 平台 + 022959.OF 持仓 6837.30 份 + 现金 0"""
    create_portfolio(db, code=pc, status="active")
    create_platform(db, code=plat)
    create_product(db, code="022959.OF", market="CN_OTC",
                   product_type="OEF", confirm_days=1, is_qdii=False)
    create_value_snapshot(db, pc, SNAP,
                          total_value=7537.44, total_shares=7537.44, unit_price=1.0)
    create_position_snapshot(
        db, pc, "CASH", "", SNAP,
        cash_amount=0, unit_price=None, cost_price=None,
        platform_code=plat,
    )
    create_position_snapshot(
        db, pc, "022959.OF", "CN_OTC", SNAP,
        shares=6837.30, unit_price=1.1024, cost_price=1.0,
        market_value=7537.44, platform_code=plat,
    )
    create_price_record(db, "022959.OF", "CN_OTC", T, unit_price=1.1024)


class TestIssue94SellThenBuy:
    """issue #94 复刻：卖出确认金额量化到 2 位，买入恰好等于回笼现金时闸门放行"""

    def test_sell_amount_two_decimals_and_exact_buy_passes(self, test_db):
        _seed_sell_portfolio(test_db, "AMT_P1", "AMT_PL1")

        # 1. 卖出 6837.30 份并确认：6837.30 × 1.1024 = 7537.43952 → 7537.44
        sell = create_trade_service(
            test_db,
            portfolio_code="AMT_P1", product_code="022959.OF", market="CN_OTC",
            trade_type="sell", trade_date=T,
            shares=Decimal("6837.30"), platform_code="AMT_PL1",
        )
        test_db.flush()
        product = _get_product(test_db, "022959.OF", "CN_OTC")
        confirm_single_trade(test_db, sell, product)
        test_db.flush()

        # 基金腿金额量化到 2 位（未修复前落库 7537.4395）
        assert Decimal(str(sell.amount)) == Decimal("7537.44")
        assert Decimal(str(sell.actual_amount)) == Decimal("7537.44")

        # 配对 CASH buy 腿同口径（现金回笼 7537.44）
        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == sell.transfer_group,
            Trade.product_code == "CASH",
        ).one()
        assert cash_leg.trade_type == "buy"
        assert cash_leg.status == "confirmed"
        assert Decimal(str(cash_leg.amount)) == Decimal("7537.44")
        assert Decimal(str(cash_leg.actual_amount)) == Decimal("7537.44")

        # 2. 可用现金为 2 位口径（卖出到账确认日 T1）
        available = calculate_available_cash(
            test_db, "AMT_P1", "AMT_PL1", as_of_date=T1
        )
        assert available == Decimal("7537.44")

        # 3. 多 1 分仍被精确拒绝（闸门无容差）
        create_price_record(test_db, "000300.OF", "CN_OTC", T1, unit_price=1.0)
        with pytest.raises(BusinessError) as exc:
            create_trade_service(
                test_db,
                portfolio_code="AMT_P1", product_code="000300.OF", market="CN_OTC",
                trade_type="buy", trade_date=T1,
                actual_amount=Decimal("7537.45"), platform_code="AMT_PL1",
            )
        assert exc.value.code == "INSUFFICIENT_CASH"

        # 4. 恰好 7537.44 买入（issue 中用户按平台 2 位口径录入的金额）→ 创建与确认均放行
        buy = create_trade_service(
            test_db,
            portfolio_code="AMT_P1", product_code="000300.OF", market="CN_OTC",
            trade_type="buy", trade_date=T1,
            actual_amount=Decimal("7537.44"), platform_code="AMT_PL1",
        )
        test_db.flush()
        buy_product = _get_product(test_db, "000300.OF", "CN_OTC")
        confirm_single_trade(test_db, buy, buy_product)
        test_db.flush()
        assert buy.status == "confirmed"


class TestRedeemAmountPrecision:
    """赎回确认金额 = quantize(shares × nav)，配对 CASH sell 腿同口径"""

    def test_redeem_confirm_amount_two_decimals(self, test_db):
        create_portfolio(test_db, code="AMT_P2", status="active")
        create_investor(test_db, code="AMT_I2")
        create_investor_holding(test_db, "AMT_P2", "AMT_I2", SNAP, shares=6837.30)
        # 已有 confirmed 申购 → 本笔非首次，净值取申请日快照 unit_price
        factory_create_subscription(
            test_db, "AMT_P2", "AMT_I2",
            sub_type="subscribe", amount=10000.0, shares=10000.0,
            unit_price=1.0, apply_date=date(2025, 1, 2),
            confirm_date=date(2025, 1, 3), status="confirmed",
        )

        sub = create_subscription(
            test_db,
            portfolio_code="AMT_P2", investor_code="AMT_I2", platform_code="MYCF",
            sub_type="redeem", apply_date=T, shares=Decimal("6837.30"),
        )
        # 申请日快照在创建之后生成（真实流程：当日快照生成后确认）
        create_value_snapshot(test_db, "AMT_P2", T,
                              total_value=7537.44, total_shares=6837.30,
                              unit_price=1.1024)
        # #203 消费点校验：赎回确认需平台可用现金覆盖赎回金额，预置确认入金。
        # 入金确认日须晚于基线日（申请日快照即基线日），否则落入基线之前的
        # 全量口径被基线窗口排除——确认日取 T1（与赎回确认日同日，窗口含当日）
        test_db.add(Trade(
            portfolio_code="AMT_P2", platform_code="MYCF",
            product_code="CASH", market="",
            trade_type="buy", amount=Decimal("7537.44"), price=Decimal("1"),
            fee=Decimal("0"), actual_amount=Decimal("7537.44"),
            trade_date=T1, confirm_date=T1, status="confirmed",
            transfer_group="amt_p2_seed",
        ))
        test_db.flush()
        confirm_single_subscription(test_db, sub)
        test_db.flush()

        # 6837.30 × 1.1024 = 7537.43952 → 7537.44
        assert Decimal(str(sub.amount)) == Decimal("7537.44")

        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == f"sub_{sub.id}"
        ).one()
        assert cash_leg.trade_type == "sell"
        assert Decimal(str(cash_leg.amount)) == Decimal("7537.44")


class TestEventCashChangePrecision:
    """份额变动事件现金字段量化到 2 位"""

    def test_cash_dividend_cash_change_two_decimals(self, test_db):
        """现金分红：6837.30 份 × 每份 0.0256 元 = 175.03488 → 175.03"""
        result = compute_event_fields(
            "cash_dividend", Decimal("6837.30"), div_cash=Decimal("0.0256")
        )
        assert result.cash_change == Decimal("175.03")
        assert result.shares_change == Decimal("0")
        assert result.shares_after == Decimal("6837.30")

    def test_reinvest_dividend_amount_quantized_before_shares(self, test_db):
        """#425：分红再投资的「金额 → 份额」转换必须先量化红利金额到分。

        5377.61 × 0.0119 = 63.993559 → 分 63.99 → / 1.0899 = 58.711808… → 58.71
        跳过金额量化的一步式算法得 58.72（博时实际派发 58.71，差 0.01 份）
        """
        result = compute_event_fields(
            "reinvest_dividend",
            Decimal("5377.61"),
            div_cash=Decimal("0.0119"),
            reinvest_nav=Decimal("1.0899"),
        )
        assert result.shares_change == Decimal("58.71")
        assert result.shares_after == Decimal("5436.32")
        assert result.cash_change == Decimal("0")

    def test_forced_adjustment_user_cash_change_quantized(self, test_db):
        """强制调整：用户填写 4 位 cash_change 量化为 2 位"""
        result = compute_event_fields(
            "forced_adjustment",
            Decimal("100.00"),
            shares_change=Decimal("10.005"),
            cash_change=Decimal("100.005"),
        )
        assert result.shares_change == Decimal("10.01")
        assert result.cash_change == Decimal("100.01")


class TestManualCashOverridePrecision:
    """manual_market_value 写入量化到 2 位"""

    def test_update_cash_position_quantized(self, test_db):
        create_portfolio(test_db, code="AMT_P4", status="active")
        result = update_cash_position(
            test_db,
            portfolio_code="AMT_P4", platform_code="MYCF",
            amount=Decimal("1000.005"), update_date=T,
        )
        manual = test_db.query(ManualMarketValue).filter(
            ManualMarketValue.portfolio_code == "AMT_P4",
            ManualMarketValue.platform_code == "MYCF",
            ManualMarketValue.value_date == T,
        ).one()
        assert Decimal(str(manual.market_value)) == Decimal("1000.01")
        assert result["cash_amount"] == 1000.01


class TestCashTransferPrecision:
    """现金转移金额先量化到 2 位再精确校验"""

    def _seed_transfer_cash(self, db, pc, cash="2000"):
        create_portfolio(db, code=pc, status="active")
        create_value_snapshot(db, pc, SNAP,
                              total_value=2000, total_shares=2000, unit_price=1.0)
        create_position_snapshot(
            db, pc, "CASH", "", SNAP,
            cash_amount=Decimal(cash), unit_price=None, cost_price=None,
            platform_code="MYCF",
        )

    def test_transfer_amount_quantized_to_two_decimals(self, test_db):
        """2000.004 → 量化为 2000.00，恰好等于可用现金时放行"""
        self._seed_transfer_cash(test_db, "AMT_P5")
        result = create_cash_transfer(
            test_db,
            portfolio_code="AMT_P5", from_platform="MYCF", to_platform="HBZQ",
            amount=Decimal("2000.004"), transfer_date=T,
        )
        legs = test_db.query(Trade).filter(
            Trade.transfer_group == result["transfer_group"]
        ).all()
        assert len(legs) == 2
        for leg in legs:
            assert Decimal(str(leg.amount)) == Decimal("2000.00")
            assert Decimal(str(leg.actual_amount)) == Decimal("2000.00")

    def test_transfer_quantized_exceeds_available_rejected(self, test_db):
        """2000.005 → 量化为 2000.01 > 可用 2000.00，精确拒绝"""
        self._seed_transfer_cash(test_db, "AMT_P6")
        with pytest.raises(BusinessError) as exc:
            create_cash_transfer(
                test_db,
                portfolio_code="AMT_P6", from_platform="MYCF", to_platform="HBZQ",
                amount=Decimal("2000.005"), transfer_date=T,
            )
        assert exc.value.code == "INSUFFICIENT_CASH"


class TestSnapshotCashPrecision:
    """快照 CASH cash_amount 继承 2 位口径（与平台对账一致）"""

    def test_snapshot_cash_amount_two_decimals(self, client, admin_headers, test_db):
        _seed_sell_portfolio(test_db, "AMT_P7", "AMT_PL7")
        create_investor_holding(test_db, "AMT_P7", "VIEWER", SNAP, shares=7537.44)
        create_price_record(test_db, "022959.OF", "CN_OTC", T1, unit_price=1.1024)

        # 卖出 6837.30 份并确认（CASH 回笼 7537.44，确认日 T1）
        sell = create_trade_service(
            test_db,
            portfolio_code="AMT_P7", product_code="022959.OF", market="CN_OTC",
            trade_type="sell", trade_date=T,
            shares=Decimal("6837.30"), platform_code="AMT_PL7",
        )
        test_db.flush()
        product = _get_product(test_db, "022959.OF", "CN_OTC")
        confirm_single_trade(test_db, sell, product)
        test_db.flush()

        # 顺延生成 T、T1 两日快照
        for target in (T, T1):
            resp = client.post(
                "/api/snapshots/generate",
                json={"portfolio_code": "AMT_P7", "target_date": target.isoformat()},
                headers=admin_headers,
            )
            assert resp.status_code == 200, f"{target}: {resp.json()}"
            assert resp.json()["success"] is True

        cash_pos = test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == "AMT_P7",
            PortfolioPosition.product_code == "CASH",
            PortfolioPosition.snapshot_date == T1,
        ).one()
        assert Decimal(str(cash_pos.cash_amount)) == Decimal("7537.44")


class TestRestInputQuantization:
    """REST 边界：4 位金额输入先量化到 2 位再做现金闸门精确比较"""

    def _seed_rest_cash(self, db, pc, plat, cash="100"):
        create_portfolio(db, code=pc, status="active")
        create_platform(db, code=plat)
        create_value_snapshot(db, pc, SNAP,
                              total_value=100, total_shares=100, unit_price=1.0)
        create_position_snapshot(
            db, pc, "CASH", "", SNAP,
            cash_amount=Decimal(cash), unit_price=None, cost_price=None,
            platform_code=plat,
        )

    def test_buy_four_decimal_input_quantized_passes_gate(self, client, admin_headers, test_db):
        """可用 100.00，输入 100.0049 → 量化为 100.00 后闸门放行，落库 2 位"""
        self._seed_rest_cash(test_db, "AMT_P8", "AMT_PL8")
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "AMT_P8",
                "product_code": "000300.OF",
                "market": "CN_OTC",
                "trade_type": "buy",
                "amount": 100.0049,
                "platform_code": "AMT_PL8",
                "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade = test_db.query(Trade).filter(Trade.id == resp.json()["id"]).first()
        assert Decimal(str(trade.actual_amount)) == Decimal("100.00")

    def test_buy_quantized_exceeds_available_rejected(self, client, admin_headers, test_db):
        """可用 100.00，输入 100.005 → 量化为 100.01 超出，精确拒绝"""
        self._seed_rest_cash(test_db, "AMT_P9", "AMT_PL9")
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "AMT_P9",
                "product_code": "000300.OF",
                "market": "CN_OTC",
                "trade_type": "buy",
                "amount": 100.005,
                "platform_code": "AMT_PL9",
                "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "INSUFFICIENT_CASH"
