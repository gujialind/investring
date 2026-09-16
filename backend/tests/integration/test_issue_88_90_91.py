# ============================================================================
# 集成测试：issue #88 / #90 / #91 (test_issue_88_90_91.py)
# ============================================================================
# - #88 手动现金覆盖（manual_market_value）查询/删除接口 + 冲突 warnings
# - #90 product create sync_history / trade confirm sync_nav 净值回填
# - #91 调仓交易跨平台现金腿（cash_platform_code）
# ============================================================================

import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import patch

from app.models.trade import Trade
from app.models.product import Product
from app.models.manual_market_value import ManualMarketValue
from app.services.exceptions import BusinessError, NotFoundError
from app.services.position_service import (
    delete_manual_cash_override,
    list_manual_cash_overrides,
    update_cash_position,
)
from app.services.product_service import create_product as create_product_service
from app.services.trade_service import (
    create_trade as create_trade_service,
    confirm_single_trade,
    cancel_trade as cancel_trade_service,
)
from tests.factories import (
    create_portfolio, create_platform, create_trade, create_product,
    create_position_snapshot, create_value_snapshot, create_price_record,
    create_manual_market_value, ensure_trading_day,
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


# ============================================================================
# issue #88：manual_market_value 查询 / 删除
# ============================================================================

class TestManualOverrideListDelete:
    """#88：覆盖层查询/删除 service 与 REST"""

    def test_list_and_delete_override_service(self, test_db):
        """list 返回覆盖记录；delete 后记录消失"""
        create_portfolio(test_db, code="MO_P1", status="active")
        create_platform(test_db, code="MO_PL1")
        create_manual_market_value(
            test_db, "MO_P1", "MO_PL1", "CASH", T, market_value=8003.3,
        )

        items = list_manual_cash_overrides(test_db, "MO_P1")
        assert len(items) == 1
        assert items[0]["platform_code"] == "MO_PL1"
        assert items[0]["market_value"] == 8003.3

        result = delete_manual_cash_override(
            test_db, portfolio_code="MO_P1", platform_code="MO_PL1", value_date=T,
        )
        test_db.commit()
        assert result["deleted_value"] == 8003.3
        # 无快照 → 无需重算
        assert result["requires_snapshot_regen"] is False
        assert list_manual_cash_overrides(test_db, "MO_P1") == []

    def test_delete_requires_regen_when_baked_in_snapshot(self, test_db):
        """覆盖日 <= 最新快照日 → requires_snapshot_regen=True"""
        _seed_cash(test_db, "MO_P2", "MO_PL2", 5000)
        create_manual_market_value(
            test_db, "MO_P2", "MO_PL2", "CASH", SNAP, market_value=5000,
        )
        result = delete_manual_cash_override(
            test_db, portfolio_code="MO_P2", platform_code="MO_PL2", value_date=SNAP,
        )
        assert result["requires_snapshot_regen"] is True

    def test_delete_not_found(self, test_db):
        """无对应覆盖记录 → MANUAL_OVERRIDE_NOT_FOUND"""
        create_portfolio(test_db, code="MO_P3", status="active")
        create_platform(test_db, code="MO_PL3")
        with pytest.raises(NotFoundError) as exc:
            delete_manual_cash_override(
                test_db, portfolio_code="MO_P3", platform_code="MO_PL3", value_date=T,
            )
        assert exc.value.code == "MANUAL_OVERRIDE_NOT_FOUND"

    def test_rest_list_and_delete(self, client, admin_headers, test_db):
        """REST：GET 列表 / DELETE 删除 / 再 DELETE 404"""
        create_portfolio(test_db, code="MO_P4", status="active")
        create_platform(test_db, code="MO_PL4")
        create_manual_market_value(
            test_db, "MO_P4", "MO_PL4", "CASH", T, market_value=123.45,
        )

        resp = client.get(
            "/api/positions/portfolio/MO_P4/cash-position", headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

        resp = client.delete(
            f"/api/positions/portfolio/MO_P4/cash-position"
            f"?platform_code=MO_PL4&update_date={T.isoformat()}",
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["deleted_value"] == 123.45

        resp = client.delete(
            f"/api/positions/portfolio/MO_P4/cash-position"
            f"?platform_code=MO_PL4&update_date={T.isoformat()}",
            headers=admin_headers,
        )
        assert resp.status_code == 404

    def test_update_cash_warns_on_conflicting_trade(self, test_db):
        """该日该平台存在 confirmed CASH trade → warnings 非空（不阻断）"""
        create_portfolio(test_db, code="MO_P5", status="active")
        create_platform(test_db, code="MO_PL5")
        ensure_trading_day(test_db, T, is_open=True)
        create_trade(
            test_db, "MO_P5", "CASH", "",
            trade_type="buy", amount=1000, status="confirmed",
            trade_date=T, confirm_date=T, platform_code="MO_PL5",
        )
        result = update_cash_position(
            test_db, portfolio_code="MO_P5", platform_code="MO_PL5",
            amount=Decimal("0"), update_date=T,
        )
        assert result["warnings"]

    def test_update_cash_no_warning_without_conflict(self, test_db):
        """无同日 confirmed CASH trade → warnings 为空"""
        create_portfolio(test_db, code="MO_P6", status="active")
        create_platform(test_db, code="MO_PL6")
        ensure_trading_day(test_db, T, is_open=True)
        result = update_cash_position(
            test_db, portfolio_code="MO_P6", platform_code="MO_PL6",
            amount=Decimal("100"), update_date=T,
        )
        assert result["warnings"] == []


# ============================================================================
# issue #90：净值回填（product create --sync / trade confirm --sync-nav）
# ============================================================================

class TestProductCreateSync:
    """#90：product create sync_history"""

    def test_create_with_sync_success(self, test_db):
        """sync_history=True 时调用同步并挂 sync_result"""
        with patch(
            "app.services.market_data_service.sync_price_data",
            return_value={"success": True, "message": "同步 5 条", "synced_count": 5},
        ) as mock_sync:
            product = create_product_service(
                test_db,
                code="I90SYNC.OF", market="CN_OTC", name="回填测试基金",
                product_type="OEF", asset_class_code="ASSET_STOCK",
                region_code="REGION_CN", style_code="STYLE_BALANCED", size_code="SIZE_LARGE",
                sync_history=True,
            )
        mock_sync.assert_called_once()
        assert product.sync_result == {
            "success": True, "message": "同步 5 条", "synced_count": 5,
        }

    def test_create_with_sync_failure_not_blocking(self, test_db):
        """同步失败不回滚产品创建，sync_result.success=False"""
        with patch(
            "app.services.market_data_service.sync_price_data",
            side_effect=RuntimeError("数据源不可用"),
        ):
            product = create_product_service(
                test_db,
                code="I90FAIL.OF", market="CN_OTC", name="回填失败基金",
                product_type="OEF", asset_class_code="ASSET_STOCK",
                region_code="REGION_CN", style_code="STYLE_BALANCED", size_code="SIZE_LARGE",
                sync_history=True,
            )
        test_db.commit()
        assert product.sync_result["success"] is False
        assert "数据源不可用" in product.sync_result["message"]
        assert _get_product(test_db, "I90FAIL.OF", "CN_OTC") is not None

    def test_create_without_sync_has_no_result(self, test_db):
        """缺省不同步，无 sync_result 属性"""
        product = create_product_service(
            test_db,
            code="I90OFF.OF", market="CN_OTC", name="不回填基金",
            product_type="OEF", asset_class_code="ASSET_STOCK",
                region_code="REGION_CN", style_code="STYLE_BALANCED", size_code="SIZE_LARGE",
        )
        assert getattr(product, "sync_result", None) is None


class TestTradeConfirmSyncNav:
    """#90：trade confirm sync_nav"""

    def _seed_pending_buy(self, db, pc, plat, amount=3000):
        _seed_cash(db, pc, plat, 5000)
        trade = create_trade_service(
            db,
            portfolio_code=pc, product_code="000300.OF", market="CN_OTC",
            trade_type="buy", trade_date=T,
            actual_amount=Decimal(str(amount)), platform_code=plat,
        )
        db.flush()
        return trade

    def test_confirm_without_sync_nav_raises_missing_nav(self, test_db):
        """T 日净值缺失且未开启 sync_nav → MISSING_NAV"""
        trade = self._seed_pending_buy(test_db, "SN_P1", "SN_PL1")
        product = _get_product(test_db, "000300.OF", "CN_OTC")
        with pytest.raises(BusinessError) as exc:
            confirm_single_trade(test_db, trade, product)
        assert exc.value.code == "MISSING_NAV"

    def test_confirm_with_sync_nav_backfills_and_confirms(self, test_db):
        """sync_nav=True：同步回填净值后重试确认成功"""
        trade = self._seed_pending_buy(test_db, "SN_P2", "SN_PL2")
        product = _get_product(test_db, "000300.OF", "CN_OTC")

        def fake_sync(db, code, market, start, end):
            create_price_record(db, code, market, T, unit_price=2.0)
            return {"success": True, "synced_count": 1}

        with patch(
            "app.services.market_data_service.sync_price_data",
            side_effect=fake_sync,
        ) as mock_sync:
            confirm_single_trade(test_db, trade, product, sync_nav=True)
        mock_sync.assert_called_once()
        assert trade.status == "confirmed"
        assert Decimal(str(trade.price)) == Decimal("2.0")

    def test_confirm_with_sync_nav_still_missing(self, test_db):
        """同步后净值仍缺失 → 照常 MISSING_NAV"""
        trade = self._seed_pending_buy(test_db, "SN_P3", "SN_PL3")
        product = _get_product(test_db, "000300.OF", "CN_OTC")
        with patch(
            "app.services.market_data_service.sync_price_data",
            return_value={"success": True, "synced_count": 0},
        ):
            with pytest.raises(BusinessError) as exc:
                confirm_single_trade(test_db, trade, product, sync_nav=True)
        assert exc.value.code == "MISSING_NAV"

    def test_confirm_sync_nav_sync_error_maps_to_missing_nav(self, test_db):
        """同步本身失败 → MISSING_NAV（携带同步错误信息）"""
        trade = self._seed_pending_buy(test_db, "SN_P4", "SN_PL4")
        product = _get_product(test_db, "000300.OF", "CN_OTC")
        with patch(
            "app.services.market_data_service.sync_price_data",
            side_effect=RuntimeError("网络中断"),
        ):
            with pytest.raises(BusinessError) as exc:
                confirm_single_trade(test_db, trade, product, sync_nav=True)
        assert exc.value.code == "MISSING_NAV"
        assert "网络中断" in exc.value.message


# ============================================================================
# issue #91：跨平台现金腿
# ============================================================================

class TestCrossPlatformCashLeg:
    """#91：cash_platform_code 跨平台扣款/到账"""

    def _create_cross_buy(self, db, pc, fund_plat, cash_plat, amount=3000):
        return create_trade_service(
            db,
            portfolio_code=pc, product_code="000300.OF", market="CN_OTC",
            trade_type="buy", trade_date=T,
            actual_amount=Decimal(str(amount)),
            platform_code=fund_plat, cash_platform_code=cash_plat,
        )

    def test_buy_cash_leg_on_source_platform(self, test_db):
        """买入：CASH sell 腿落在扣款平台，同 transfer_group"""
        # 现金全部在 ZG 平台，基金买在 TT 平台
        _seed_cash(test_db, "XP_P1", "XP_ZG1", 5000)
        create_platform(test_db, code="XP_TT1")

        fund = self._create_cross_buy(test_db, "XP_P1", "XP_TT1", "XP_ZG1")
        test_db.flush()

        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == fund.transfer_group,
            Trade.product_code == "CASH",
        ).first()
        assert cash_leg is not None
        assert cash_leg.trade_type == "sell"
        assert cash_leg.platform_code == "XP_ZG1"
        assert fund.platform_code == "XP_TT1"

    def test_buy_insufficient_on_source_platform(self, test_db):
        """扣款平台余额不足 → INSUFFICIENT_CASH（消息含扣款平台）"""
        # ZG 平台只有 1000，基金平台 TT 有充足现金也不应放行
        _seed_cash(test_db, "XP_P2", "XP_ZG2", 1000)
        create_platform(test_db, code="XP_TT2")
        create_position_snapshot(
            test_db, "XP_P2", "CASH", "", SNAP,
            cash_amount=99999, platform_code="XP_TT2",
        )

        with pytest.raises(BusinessError) as exc:
            self._create_cross_buy(test_db, "XP_P2", "XP_TT2", "XP_ZG2", amount=3000)
        assert exc.value.code == "INSUFFICIENT_CASH"
        assert "XP_ZG2" in exc.value.message

    def test_confirm_checks_cash_on_source_platform(self, test_db):
        """确认时按 CASH 腿平台校验现金（基金平台无现金也可确认）"""
        _seed_cash(test_db, "XP_P3", "XP_ZG3", 5000)
        create_platform(test_db, code="XP_TT3")
        create_price_record(test_db, "000300.OF", "CN_OTC", T, unit_price=1.0)

        fund = self._create_cross_buy(test_db, "XP_P3", "XP_TT3", "XP_ZG3")
        test_db.flush()
        product = _get_product(test_db, "000300.OF", "CN_OTC")
        confirm_single_trade(test_db, fund, product)
        test_db.flush()

        assert fund.status == "confirmed"
        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == fund.transfer_group,
            Trade.product_code == "CASH",
        ).first()
        assert cash_leg.status == "confirmed"
        assert cash_leg.platform_code == "XP_ZG3"
        assert cash_leg.confirm_date == T  # #93: 买入扣款 T 日即扣，不与基金确认日一致

    def test_sell_cash_leg_on_destination_platform(self, test_db):
        """卖出：#493 到账平台在确认时录入，CASH buy 腿落在到账平台"""
        create_portfolio(test_db, code="XP_P4", status="active")
        create_platform(test_db, code="XP_TT4")
        create_platform(test_db, code="XP_ZG4")
        create_value_snapshot(test_db, "XP_P4", SNAP,
                              total_value=1500, total_shares=1500, unit_price=1.0)
        create_position_snapshot(
            test_db, "XP_P4", "000300.OF", "CN_OTC", SNAP,
            shares=1000, platform_code="XP_TT4",
        )
        create_price_record(test_db, "000300.OF", "CN_OTC", T, unit_price=1.0)

        # #493：创建期不接受到账平台（到账信息在确认时录入）
        with pytest.raises(BusinessError) as exc:
            create_trade_service(
                test_db,
                portfolio_code="XP_P4", product_code="000300.OF", market="CN_OTC",
                trade_type="sell", trade_date=T,
                shares=Decimal("500"), actual_amount=Decimal("500"),
                platform_code="XP_TT4", cash_platform_code="XP_ZG4",
            )
        assert exc.value.code == "CASH_PLATFORM_NOT_ALLOWED"

        fund = create_trade_service(
            test_db,
            portfolio_code="XP_P4", product_code="000300.OF", market="CN_OTC",
            trade_type="sell", trade_date=T,
            shares=Decimal("500"), actual_amount=Decimal("500"),
            platform_code="XP_TT4",
        )
        test_db.flush()
        # 创建期无 CASH 腿（半成品组，只有基金腿）
        assert test_db.query(Trade).filter(
            Trade.transfer_group == fund.transfer_group,
            Trade.product_code == "CASH",
        ).first() is None

        confirm_single_trade(
            test_db, fund, _get_product(test_db, "000300.OF", "CN_OTC"),
            cash_platform_code="XP_ZG4",
        )
        test_db.flush()

        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == fund.transfer_group,
            Trade.product_code == "CASH",
        ).first()
        assert cash_leg.trade_type == "buy"
        assert cash_leg.platform_code == "XP_ZG4"
        assert cash_leg.status == "confirmed"

    def test_cancel_syncs_cross_platform_leg(self, test_db):
        """取消基金腿时跨平台 CASH 腿同步 cancelled"""
        _seed_cash(test_db, "XP_P5", "XP_ZG5", 5000)
        create_platform(test_db, code="XP_TT5")

        fund = self._create_cross_buy(test_db, "XP_P5", "XP_TT5", "XP_ZG5")
        test_db.flush()
        cancel_trade_service(test_db, fund)
        test_db.flush()

        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == fund.transfer_group,
            Trade.product_code == "CASH",
        ).first()
        assert fund.status == "cancelled"
        assert cash_leg.status == "cancelled"

    def test_cash_platform_not_found(self, test_db):
        """现金平台不存在 → PLATFORM_NOT_FOUND"""
        _seed_cash(test_db, "XP_P6", "XP_ZG6", 5000)
        with pytest.raises(NotFoundError) as exc:
            self._create_cross_buy(test_db, "XP_P6", "XP_ZG6", "NO_SUCH_PLAT")
        assert exc.value.code == "PLATFORM_NOT_FOUND"

    def test_same_platform_equivalent_to_default(self, test_db):
        """cash_platform_code 与基金腿相同时等价于不传"""
        _seed_cash(test_db, "XP_P7", "XP_ZG7", 5000)
        fund = self._create_cross_buy(test_db, "XP_P7", "XP_ZG7", "XP_ZG7")
        test_db.flush()
        cash_leg = test_db.query(Trade).filter(
            Trade.transfer_group == fund.transfer_group,
            Trade.product_code == "CASH",
        ).first()
        assert cash_leg.platform_code == "XP_ZG7"
