# ============================================================================
# 集成测试：#493 单腿确认的调仓在途生命周期
# ============================================================================
# 覆盖计划 §4.1 必须行为：
# - 买入创建即扣款（CASH sell confirmed）、基金腿 pending；D 日快照成功且记
#   IN_TRANSIT_BUY（不再被 pending 校验阻断）；确认后份额入账、在途归零
# - 买入 confirm 的现金腿核验/校正边界（未消费可校正、已消费必须拒绝）
# - 卖出创建无 CASH 腿；确认时录入到账日并按确认净额建腿；C..A 窗口记
#   IN_TRANSIT_SELL；到账日修正、拒绝面与快照保护
# - 整组生命周期（cancel/delete/unconfirm/reconfirm 不换组、不重复建腿）
# - 调仓退出 auto_confirm（#471）：pending 调仓不被扫描确认、到期仍阻断快照
# - 自身扣款加回口径（pending/confirmed、快照基线、查询日之后）与 cancelled 排除
# - 读侧派生现金字段（list/get/confirm/preview）与 preview 零写入
# - #526：确认路径的验资平台与落账平台恒一致（含缺腿兜底与跨平台扣款两条路径）
# ============================================================================

from datetime import date, timedelta
from decimal import Decimal
import json

import pytest

from app.models.audit_log import AuditLog
from app.models.portfolio_position import PortfolioPosition
from app.models.portfolio_value_snapshot import PortfolioValueSnapshot
from app.models.trade import Trade
from app.services.exceptions import BusinessError
from app.services.snapshot_service import auto_confirm_after_snapshot
from app.services.trade_service import (
    _own_cash_sell_legs,
    confirm_single_trade,
    create_trade as create_trade_service,
    validate_buy_cash_with_addback,
)
from tests.factories import (
    create_investor_holding,
    create_platform,
    create_portfolio,
    create_position_snapshot,
    create_price_record,
    create_product,
    create_trade,
    create_value_snapshot,
    ensure_trading_day,
)


# 交易日历（conftest 种子：工作日为交易日）
D0 = date(2025, 6, 6)    # 周五：基线快照日
T = date(2025, 6, 9)     # 周一：下单日
T1 = date(2025, 6, 10)   # 周二：基金确认日 C（confirm_days=1）
T2 = date(2025, 6, 11)   # 周三：到账日 A
T3 = date(2025, 6, 12)   # 周四
SAT = date(2025, 6, 7)   # 周六：非交易日

FUND = "FUND_493"
FUND_MARKET = "CN_OTC"
PLAT = "P493"
NAV = 1.25


def _seed_portfolio(db, code, *, cash=50000.0, fund_shares=None, plat=PLAT):
    """组合 + 平台 + D0 三表快照（CASH 基线，可选基金持仓）"""
    create_portfolio(db, code=code, status="active")
    create_platform(db, code=plat)
    create_product(db, code=FUND, market=FUND_MARKET, product_type="OEF",
                   asset_class_code="ASSET_STOCK", confirm_days=1)
    for d in (D0, T, T1, T2, T3):
        ensure_trading_day(db, d, is_open=True)

    fund_value = 0.0
    create_position_snapshot(
        db, code, "CASH", "", D0, cash_amount=cash, platform_code=plat,
    )
    if fund_shares:
        fund_value = fund_shares * NAV
        create_position_snapshot(
            db, code, FUND, FUND_MARKET, D0,
            shares=fund_shares, unit_price=NAV, cost_price=NAV,
            market_value=fund_value, platform_code=plat,
        )
    create_value_snapshot(db, code, D0,
                          total_value=cash + fund_value,
                          total_shares=cash + fund_value, unit_price=1.0)
    create_investor_holding(db, code, "VIEWER", D0, shares=cash + fund_value)
    # T 日净值（场外确认恒取 T 日净值）
    create_price_record(db, FUND, FUND_MARKET, T, NAV)


def _product(db):
    from app.models.product import Product

    return db.query(Product).filter(
        Product.code == FUND, Product.market == FUND_MARKET
    ).first()


def _fund_leg(db, trade_id):
    return db.query(Trade).filter(Trade.id == trade_id).first()


def _cash_leg(db, fund_leg):
    return db.query(Trade).filter(
        Trade.transfer_group == fund_leg.transfer_group,
        Trade.product_code == "CASH",
    ).first()


def _group_legs(db, fund_leg):
    return db.query(Trade).filter(
        Trade.transfer_group == fund_leg.transfer_group
    ).all()


def _spy_buy_cash_check(monkeypatch) -> dict:
    """拦截确认路径的验资调用，返回记录实参的 dict（#526 平台一致性）。"""
    from app.services import trade_service

    seen: dict = {}
    real = trade_service.validate_buy_cash_with_addback

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(trade_service, "validate_buy_cash_with_addback", spy)
    return seen


def _gen(client, headers, code, target):
    return client.post(
        "/api/snapshots/generate",
        json={"portfolio_code": code, "target_date": target.isoformat()},
        headers=headers,
    )


def _positions(db, code, target):
    return db.query(PortfolioPosition).filter(
        PortfolioPosition.portfolio_code == code,
        PortfolioPosition.snapshot_date == target,
    ).all()


def _by_product(positions, product_code):
    return [p for p in positions if p.product_code == product_code]


# ============================================================================
# 买入：创建即扣款 + D 日在途
# ============================================================================

class TestBuyCreateDeductsImmediately:
    CODE = "IT493_BUY"

    def _create_buy(self, client, admin_headers, amount=10000.0):
        return client.post(
            "/api/trades",
            json={
                "portfolio_code": self.CODE,
                "product_code": FUND,
                "market": FUND_MARKET,
                "trade_type": "buy",
                "amount": amount,
                "platform_code": PLAT,
                "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )

    def test_create_deducts_cash_and_snapshot_records_in_transit(
        self, client, admin_headers, test_db
    ):
        """创建即扣款：CASH sell confirmed；T 日快照成功并记等额 IN_TRANSIT_BUY"""
        _seed_portfolio(test_db, self.CODE, cash=50000.0)
        resp = self._create_buy(client, admin_headers)
        assert resp.status_code in (200, 201), resp.json()
        data = resp.json()
        fund_leg = _fund_leg(test_db, data["id"])
        cash_leg = _cash_leg(test_db, fund_leg)

        # 基金腿 pending；扣款腿 confirmed、现金日 = 下单日 T
        assert fund_leg.status == "pending"
        assert fund_leg.confirm_date == T1
        assert cash_leg is not None
        assert cash_leg.trade_type == "sell" and cash_leg.status == "confirmed"
        assert cash_leg.trade_date == T and cash_leg.confirm_date == T
        assert Decimal(str(cash_leg.amount)) == Decimal("10000")
        # 派生字段（读侧）：买入的扣款平台与扣款日
        assert data["cash_platform_code"] == PLAT
        assert data["cash_confirm_date"] == T.isoformat()

        # T 日快照：pending 买入不再阻断生成
        gen = _gen(client, admin_headers, self.CODE, T)
        assert gen.status_code == 200, gen.json()
        assert gen.json()["success"] is True

        test_db.expire_all()
        positions = _positions(test_db, self.CODE, T)
        cash = _by_product(positions, "CASH")
        assert len(cash) == 1
        assert Decimal(str(cash[0].cash_amount)) == Decimal("40000")
        transit = _by_product(positions, "IN_TRANSIT_BUY")
        assert len(transit) == 1
        assert Decimal(str(transit[0].cash_amount)) == Decimal("10000")
        assert _by_product(positions, FUND) == []

        snap = test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == self.CODE,
            PortfolioValueSnapshot.snapshot_date == T,
        ).first()
        assert Decimal(str(snap.total_value)) == Decimal("50000")
        assert Decimal(str(snap.in_transit_total)) == Decimal("10000")

    def test_confirm_moves_in_transit_into_fund_position(self, client, admin_headers, test_db):
        """T+1 确认：基金份额入账、在途归零、总资产连续"""
        _seed_portfolio(test_db, self.CODE, cash=50000.0)
        trade_id = self._create_buy(client, admin_headers).json()["id"]
        assert _gen(client, admin_headers, self.CODE, T).status_code == 200
        create_price_record(test_db, FUND, FUND_MARKET, T1, NAV)

        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, conf.json()
        assert conf.json()["trade"]["status"] == "confirmed"
        # 确认响应内层 trade 带派生现金字段（扣款腿不变）
        assert conf.json()["trade"]["cash_platform_code"] == PLAT
        assert conf.json()["trade"]["cash_confirm_date"] == T.isoformat()

        gen = _gen(client, admin_headers, self.CODE, T1)
        assert gen.status_code == 200, gen.json()
        test_db.expire_all()
        positions = _positions(test_db, self.CODE, T1)
        assert _by_product(positions, "IN_TRANSIT_BUY") == []
        fund = _by_product(positions, FUND)
        assert len(fund) == 1
        assert Decimal(str(fund[0].shares)) == Decimal("8000")
        cash = _by_product(positions, "CASH")
        assert Decimal(str(cash[0].cash_amount)) == Decimal("40000")
        snap = test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == self.CODE,
            PortfolioValueSnapshot.snapshot_date == T1,
        ).first()
        assert Decimal(str(snap.total_value)) == Decimal("50000")
        assert Decimal(str(snap.in_transit_total)) == Decimal("0")

    def test_buy_create_rejects_foreign_cash_confirm_date(self, client, admin_headers, test_db):
        """买入创建：cash_confirm_date 只接受等于下单日 T"""
        _seed_portfolio(test_db, self.CODE, cash=50000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": self.CODE, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "buy", "amount": 1000.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
                "cash_confirm_date": T1.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "CASH_CONFIRM_DATE_NOT_ALLOWED"

    def test_buy_accepts_cash_platform_equal_to_fund_platform(
        self, client, admin_headers, test_db
    ):
        """买入传「等于基金腿平台」的 cash_platform_code 仍被接受（归一化为不传）

        第 3 项修复只改判定时机，不动买入侧语义：同平台值照旧归一化，
        CASH 腿仍落在基金腿平台、不发生跨平台扣款。
        """
        _seed_portfolio(test_db, self.CODE, cash=50000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": self.CODE, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "buy", "amount": 10000.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
                "cash_platform_code": PLAT,
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        cash_leg = _cash_leg(test_db, _fund_leg(test_db, resp.json()["id"]))
        assert cash_leg is not None
        assert cash_leg.platform_code == PLAT


class TestBuyConfirmCashLegGuards:
    CODE = "IT493_BUY2"

    def _create(self, client, admin_headers, test_db, amount=10000.0):
        _seed_portfolio(test_db, self.CODE, cash=50000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": self.CODE, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "buy", "amount": amount,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        return _fund_leg(test_db, resp.json()["id"])

    def test_confirm_corrects_cash_leg_when_not_consumed(self, client, admin_headers, test_db):
        """现金腿金额不一致且未被快照消费 → 确认时校正到基金腿支出口径"""
        fund_leg = self._create(client, admin_headers, test_db)
        cash_leg = _cash_leg(test_db, fund_leg)
        cash_leg.actual_amount = Decimal("9000")
        cash_leg.amount = Decimal("9000")
        cash_leg.status = "pending"
        test_db.flush()

        conf = client.post(f"/api/trades/{fund_leg.id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, conf.json()
        test_db.expire_all()
        fund_leg = _fund_leg(test_db, fund_leg.id)
        cash_leg = _cash_leg(test_db, fund_leg)
        assert cash_leg.status == "confirmed"
        assert Decimal(str(cash_leg.actual_amount)) == Decimal("10000")
        assert Decimal(str(cash_leg.amount)) == Decimal("10000")

    def test_buy_confirm_rebuilds_missing_deduction_leg_with_audit_id(
        self, client, admin_headers, test_db
    ):
        """扣款腿缺失时确认兜底建腿：审计 trade_id 同样非 null

        覆盖 `_apply_confirm_cash_leg` 的**第二条**新建分支（#518 评审：两处
        `attach_paired_cash_leg` 都曾在 flush 前读 `leg.id`，审计恒为 null）。
        库态：买入组在确认前丢了扣款腿（存量异常数据，见 CASH_LEG_MISSING 同族）。
        """
        fund_leg = self._create(client, admin_headers, test_db)
        test_db.delete(_cash_leg(test_db, fund_leg))
        test_db.flush()
        assert _cash_leg(test_db, fund_leg) is None

        conf = client.post(f"/api/trades/{fund_leg.id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, conf.json()
        test_db.expire_all()
        fund_leg = _fund_leg(test_db, fund_leg.id)
        rebuilt = _cash_leg(test_db, fund_leg)
        assert rebuilt is not None and rebuilt.status == "confirmed"

        audit = test_db.query(AuditLog).filter(
            AuditLog.resource_type == "trade",
            AuditLog.resource_id == str(fund_leg.id),
            AuditLog.action == "confirm",
        ).order_by(AuditLog.id.desc()).first()
        assert audit is not None and audit.new_value is not None
        payload = json.loads(audit.new_value)
        assert payload["cash_leg"]["action"] == "created"
        assert payload["cash_leg"]["trade_id"] is not None
        assert payload["cash_leg"]["trade_id"] == rebuilt.id

    def test_confirm_rejects_correction_when_deduction_consumed(
        self, client, admin_headers, test_db
    ):
        """扣款日已被快照消费 → 拒绝校正（提示先删快照），基金腿保持 pending"""
        fund_leg = self._create(client, admin_headers, test_db)
        assert _gen(client, admin_headers, self.CODE, T).status_code == 200
        create_price_record(test_db, FUND, FUND_MARKET, T1, NAV)
        cash_leg = _cash_leg(test_db, fund_leg)
        cash_leg.actual_amount = Decimal("9000")
        cash_leg.amount = Decimal("9000")
        test_db.flush()

        conf = client.post(f"/api/trades/{fund_leg.id}/confirm", headers=admin_headers)
        assert conf.status_code == 422
        assert conf.json()["detail"]["error"] == "SNAPSHOT_DEPENDENCY"
        test_db.expire_all()
        assert _fund_leg(test_db, fund_leg.id).status == "pending"

    def test_confirm_rejects_confirm_date_not_after_snapshot(
        self, client, admin_headers, test_db
    ):
        """有效基金确认日不晚于最新快照 → SNAPSHOT_DEPENDENCY，零写入"""
        _seed_portfolio(test_db, self.CODE, cash=50000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": self.CODE, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "buy", "amount": 1000.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]
        fund_leg = _fund_leg(test_db, trade_id)
        assert fund_leg.confirm_date == T1  # confirm_days=1
        # 直接构造「C 日已有快照」的库态（级联回退/重算后可能出现的窗口）
        create_position_snapshot(
            test_db, self.CODE, "CASH", "", T1, cash_amount=40000, platform_code=PLAT,
        )
        create_value_snapshot(test_db, self.CODE, T1,
                              total_value=40000, total_shares=40000, unit_price=1.0)
        test_db.flush()

        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 422
        assert conf.json()["detail"]["error"] == "SNAPSHOT_DEPENDENCY"
        test_db.expire_all()
        fund_leg = _fund_leg(test_db, trade_id)
        assert fund_leg.status == "pending"
        assert fund_leg.price is None
        assert _cash_leg(test_db, fund_leg).status == "confirmed"

    def test_buy_confirm_rejects_platform_and_date_override(
        self, client, admin_headers, test_db
    ):
        """买入不允许借 confirm 改扣款平台/日期"""
        fund_leg = self._create(client, admin_headers, test_db)
        create_platform(test_db, code="P493_OTHER")

        bad_platform = client.post(
            f"/api/trades/{fund_leg.id}/confirm",
            params={"cash_platform_code": "P493_OTHER"},
            headers=admin_headers,
        )
        assert bad_platform.status_code == 422
        assert bad_platform.json()["detail"]["error"] == "CASH_PLATFORM_NOT_ALLOWED"

        bad_date = client.post(
            f"/api/trades/{fund_leg.id}/confirm",
            params={"cash_confirm_date": T1.isoformat()},
            headers=admin_headers,
        )
        assert bad_date.status_code == 422
        assert bad_date.json()["detail"]["error"] == "CASH_CONFIRM_DATE_NOT_ALLOWED"

    def test_missing_deduction_leg_confirm_rejects_other_platform(
        self, client, admin_headers, test_db
    ):
        """#526：缺扣款腿时，闸门仍须成立——既不能建腿，也不能改写扣款平台"""
        fund_leg = self._create(client, admin_headers, test_db)
        test_db.delete(_cash_leg(test_db, fund_leg))
        test_db.flush()
        create_platform(test_db, code="P493_OTHER")

        bad = client.post(
            f"/api/trades/{fund_leg.id}/confirm",
            params={"cash_platform_code": "P493_OTHER"},
            headers=admin_headers,
        )
        assert bad.status_code == 422
        assert bad.json()["detail"]["error"] == "CASH_PLATFORM_NOT_ALLOWED"
        test_db.expire_all()
        assert _cash_leg(test_db, _fund_leg(test_db, fund_leg.id)) is None
        assert _fund_leg(test_db, fund_leg.id).status == "pending"

    def test_missing_deduction_leg_confirm_uses_fund_leg_platform(
        self, client, admin_headers, test_db, monkeypatch
    ):
        """#526：缺腿兜底建腿落基金腿平台，且验资按**同一**平台

        「验资平台 == 落账平台」由确认计划单点提供：断言 `cash_platform` 实参
        而非只断言建腿结果——旧写法靠两处独立 fallback 恰好一致，任一侧改动都
        会让钱在 A 平台被验过、在 B 平台被扣掉。

        spy 在 create 之后才装：创建路径同样调 `validate_buy_cash_with_addback`，
        先装会让断言读到 create 那次实参而假绿。
        """
        fund_leg = self._create(client, admin_headers, test_db)
        test_db.delete(_cash_leg(test_db, fund_leg))
        test_db.flush()

        seen = _spy_buy_cash_check(monkeypatch)
        conf = client.post(f"/api/trades/{fund_leg.id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, conf.json()
        assert seen.get("cash_platform") == PLAT
        test_db.expire_all()
        rebuilt = _cash_leg(test_db, _fund_leg(test_db, fund_leg.id))
        assert rebuilt is not None
        assert rebuilt.platform_code == PLAT and rebuilt.status == "confirmed"

    def test_confirm_cash_check_platform_follows_existing_deduction_leg(
        self, client, admin_headers, test_db, monkeypatch
    ):
        """#526：有腿路径行为不变，验资平台仍取扣款腿平台（跨平台买入）"""
        _seed_portfolio(test_db, self.CODE, cash=50000.0)
        create_platform(test_db, code="P493_OTHER")
        # 扣款平台必须自己有钱：验资按平台分账，否则创建即被 INSUFFICIENT_CASH 拒
        create_position_snapshot(
            test_db, self.CODE, "CASH", "", D0,
            cash_amount=50000.0, platform_code="P493_OTHER",
        )
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": self.CODE, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "buy", "amount": 10000.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
                "cash_platform_code": "P493_OTHER",
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        fund_leg = _fund_leg(test_db, resp.json()["id"])

        seen = _spy_buy_cash_check(monkeypatch)
        conf = client.post(f"/api/trades/{fund_leg.id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, conf.json()
        assert seen.get("cash_platform") == "P493_OTHER"

    def test_reconfirm_after_unconfirm_keeps_single_cash_leg(
        self, client, admin_headers, test_db
    ):
        """买入 unconfirm → 再确认：扣款腿保持 confirmed，不追加第二条现金腿"""
        fund_leg = self._create(client, admin_headers, test_db)
        group = fund_leg.transfer_group
        assert client.post(f"/api/trades/{fund_leg.id}/confirm",
                           headers=admin_headers).status_code == 200
        assert client.post(f"/api/trades/{fund_leg.id}/unconfirm",
                           headers=admin_headers).status_code == 200
        test_db.expire_all()
        cash_leg = _cash_leg(test_db, _fund_leg(test_db, fund_leg.id))
        assert cash_leg.status == "confirmed"
        assert cash_leg.trade_date == T

        assert client.post(f"/api/trades/{fund_leg.id}/confirm",
                           headers=admin_headers).status_code == 200
        test_db.expire_all()
        legs = _group_legs(test_db, _fund_leg(test_db, fund_leg.id))
        assert len(legs) == 2
        assert all(leg.transfer_group == group for leg in legs)


# ============================================================================
# 卖出：确认时建腿 + 到账窗口在途
# ============================================================================

class TestSellConfirmCreatesArrivalLeg:
    CODE = "IT493_SELL"

    def _create_sell(self, client, admin_headers, shares=400.0):
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": self.CODE, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "sell", "shares": shares,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        return resp.json()

    def test_create_has_no_cash_leg_then_confirm_builds_it(self, client, admin_headers, test_db):
        """创建期无 CASH 腿（组号已分配）；确认录入到账日后按净额建腿"""
        _seed_portfolio(test_db, self.CODE, cash=10000.0, fund_shares=1000.0)
        data = self._create_sell(client, admin_headers)
        fund_leg = _fund_leg(test_db, data["id"])
        assert fund_leg.transfer_group.startswith("rebal_")
        assert _cash_leg(test_db, fund_leg) is None
        # 无现金腿 → 派生字段为 null
        assert data["cash_platform_code"] is None
        assert data["cash_confirm_date"] is None

        conf = client.post(
            f"/api/trades/{fund_leg.id}/confirm",
            params={"cash_confirm_date": T2.isoformat()},
            headers=admin_headers,
        )
        assert conf.status_code == 200, conf.json()
        test_db.expire_all()
        fund_leg = _fund_leg(test_db, fund_leg.id)
        cash_leg = _cash_leg(test_db, fund_leg)
        assert cash_leg is not None
        assert cash_leg.trade_type == "buy" and cash_leg.status == "confirmed"
        # trade_date = 基金确认日 C（= T+1）；confirm_date = 到账日 A
        assert cash_leg.trade_date == T1
        assert cash_leg.confirm_date == T2
        assert Decimal(str(cash_leg.amount)) == Decimal("500")
        assert conf.json()["trade"]["cash_confirm_date"] == T2.isoformat()

    def test_create_rejects_cash_platform_equal_to_fund_platform(
        self, client, admin_headers, test_db
    ):
        """卖出创建期传「等于基金腿平台」的 cash_platform_code 必须拒绝（#518 评审）

        回归面：该值曾被「同平台等价于不传」的归一化静默折成 None，于是同一个
        输入 REST 放行、CLI 前置拒绝，两端行为相反。判据要求按**原始入参**判定。
        """
        _seed_portfolio(test_db, self.CODE, cash=10000.0, fund_shares=1000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": self.CODE, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "sell", "shares": 400.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
                "cash_platform_code": PLAT,
            },
            headers=admin_headers,
        )
        assert resp.status_code == 422, resp.json()
        assert resp.json()["detail"]["error"] == "CASH_PLATFORM_NOT_ALLOWED"
        test_db.expire_all()
        assert test_db.query(Trade).filter(Trade.portfolio_code == self.CODE).count() == 0

    def test_confirm_audit_payload_carries_cash_leg_trade_id(
        self, client, admin_headers, test_db
    ):
        """确认新建到账腿：confirm 审计的 cash_leg.trade_id = 实际腿 id（非 null）

        回归面：`attach_paired_cash_leg` 只 `db.add`，自增 `Trade.id` 在 flush 前
        恒为 None，紧接着取审计载荷就会把 `trade_id: null` 写进审计（#518 评审）。
        """
        _seed_portfolio(test_db, self.CODE, cash=10000.0, fund_shares=1000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": self.CODE, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "sell", "shares": 400.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        conf = client.post(
            f"/api/trades/{trade_id}/confirm",
            params={"cash_confirm_date": T2.isoformat()},
            headers=admin_headers,
        )
        assert conf.status_code == 200, conf.json()
        test_db.expire_all()
        cash_leg = _cash_leg(test_db, _fund_leg(test_db, trade_id))
        assert cash_leg is not None

        audit = test_db.query(AuditLog).filter(
            AuditLog.resource_type == "trade",
            AuditLog.resource_id == str(trade_id),
            AuditLog.action == "confirm",
        ).order_by(AuditLog.id.desc()).first()
        assert audit is not None and audit.new_value is not None
        payload = json.loads(audit.new_value)
        assert payload["cash_leg"]["action"] == "created"
        assert payload["cash_leg"]["trade_id"] is not None
        assert payload["cash_leg"]["trade_id"] == cash_leg.id

    def test_in_transit_window_between_confirm_and_arrival(self, client, admin_headers, test_db):
        """C ≤ D < A：等额 IN_TRANSIT_SELL，CASH 未增；D = A 转 CASH"""
        _seed_portfolio(test_db, self.CODE, cash=10000.0, fund_shares=1000.0)
        fund_leg = _fund_leg(test_db, self._create_sell(client, admin_headers)["id"])
        conf = client.post(
            f"/api/trades/{fund_leg.id}/confirm",
            params={"cash_confirm_date": T2.isoformat()},
            headers=admin_headers,
        )
        assert conf.status_code == 200, conf.json()

        create_price_record(test_db, FUND, FUND_MARKET, T1, NAV)
        # T 日快照（C = T1 > T）：卖出尚未生效，先记基线
        assert _gen(client, admin_headers, self.CODE, T).status_code == 200
        create_price_record(test_db, FUND, FUND_MARKET, T2, NAV)
        # C 日快照：份额已扣、资金在途
        assert _gen(client, admin_headers, self.CODE, T1).status_code == 200
        test_db.expire_all()
        positions = _positions(test_db, self.CODE, T1)
        transit = _by_product(positions, "IN_TRANSIT_SELL")
        assert len(transit) == 1
        assert Decimal(str(transit[0].cash_amount)) == Decimal("500")
        assert Decimal(str(_by_product(positions, "CASH")[0].cash_amount)) == Decimal("10000")
        assert Decimal(str(_by_product(positions, FUND)[0].shares)) == Decimal("600")
        snap_t1 = test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == self.CODE,
            PortfolioValueSnapshot.snapshot_date == T1,
        ).first()
        assert Decimal(str(snap_t1.total_value)) == Decimal("11250")
        assert Decimal(str(snap_t1.in_transit_total)) == Decimal("500")

        # 到账日：在途归零、CASH 增加
        assert _gen(client, admin_headers, self.CODE, T2).status_code == 200
        test_db.expire_all()
        positions = _positions(test_db, self.CODE, T2)
        assert _by_product(positions, "IN_TRANSIT_SELL") == []
        assert Decimal(str(_by_product(positions, "CASH")[0].cash_amount)) == Decimal("10500")
        snap_t2 = test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == self.CODE,
            PortfolioValueSnapshot.snapshot_date == T2,
        ).first()
        assert Decimal(str(snap_t2.total_value)) == Decimal("11250")
        assert Decimal(str(snap_t2.in_transit_total)) == Decimal("0")

    def test_default_arrival_equals_confirm_date_no_in_transit(
        self, client, admin_headers, test_db
    ):
        """到账日缺省 = C：不产生在途，确认当日 CASH 直接增加"""
        _seed_portfolio(test_db, self.CODE, cash=10000.0, fund_shares=1000.0)
        fund_leg = _fund_leg(test_db, self._create_sell(client, admin_headers)["id"])
        conf = client.post(f"/api/trades/{fund_leg.id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, conf.json()
        test_db.expire_all()
        cash_leg = _cash_leg(test_db, _fund_leg(test_db, fund_leg.id))
        assert cash_leg.confirm_date == cash_leg.trade_date == T1

        create_price_record(test_db, FUND, FUND_MARKET, T1, NAV)
        assert _gen(client, admin_headers, self.CODE, T).status_code == 200
        assert _gen(client, admin_headers, self.CODE, T1).status_code == 200
        test_db.expire_all()
        positions = _positions(test_db, self.CODE, T1)
        assert _by_product(positions, "IN_TRANSIT_SELL") == []
        assert Decimal(str(_by_product(positions, "CASH")[0].cash_amount)) == Decimal("10500")


class TestSellArrivalDateUpdate:
    CODE = "IT493_SELL2"

    def _confirmed_sell(self, client, admin_headers, test_db, arrival=T2):
        _seed_portfolio(test_db, self.CODE, cash=10000.0, fund_shares=1000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": self.CODE, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "sell", "shares": 400.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        conf = client.post(
            f"/api/trades/{trade_id}/confirm",
            params={"cash_confirm_date": arrival.isoformat()},
            headers=admin_headers,
        )
        assert conf.status_code == 200, conf.json()
        return trade_id

    def test_update_arrival_date_moves_cash_leg_only(self, client, admin_headers, test_db):
        """confirmed 卖出 PUT cash_confirm_date：只改配对腿到账日，不重算基金金额"""
        trade_id = self._confirmed_sell(client, admin_headers, test_db)
        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"cash_confirm_date": T3.isoformat()},
            headers=admin_headers,
        )
        assert upd.status_code == 200, upd.json()
        test_db.expire_all()
        fund_leg = _fund_leg(test_db, trade_id)
        cash_leg = _cash_leg(test_db, fund_leg)
        assert cash_leg.confirm_date == T3
        assert cash_leg.trade_date == T1
        # 基金腿财务字段未动
        assert Decimal(str(fund_leg.shares)) == Decimal("400")
        assert Decimal(str(fund_leg.actual_amount)) == Decimal("500")
        assert upd.json()["cash_confirm_date"] == T3.isoformat()

    def test_update_arrival_null_and_mixed_fields_rejected(
        self, client, admin_headers, test_db
    ):
        """显式 null 拒绝；混入其他字段整体拒绝（不部分应用）"""
        trade_id = self._confirmed_sell(client, admin_headers, test_db)
        null_resp = client.put(
            f"/api/trades/{trade_id}",
            json={"cash_confirm_date": None},
            headers=admin_headers,
        )
        assert null_resp.status_code == 422
        assert null_resp.json()["detail"]["error"] == "INVALID_PARAM"

        mixed = client.put(
            f"/api/trades/{trade_id}",
            json={"cash_confirm_date": T3.isoformat(), "amount": 1.0},
            headers=admin_headers,
        )
        assert mixed.status_code == 422
        assert mixed.json()["detail"]["error"] == "CANNOT_MODIFY_CONFIRMED"
        test_db.expire_all()
        assert _cash_leg(test_db, _fund_leg(test_db, trade_id)).confirm_date == T2

    def test_update_arrival_order_and_trading_day_guards(
        self, client, admin_headers, test_db
    ):
        """A < C → INVALID_DATE_ORDER；A 非交易日 → NON_TRADING_DAY"""
        trade_id = self._confirmed_sell(client, admin_headers, test_db)
        earlier = client.put(
            f"/api/trades/{trade_id}",
            json={"cash_confirm_date": D0.isoformat()},
            headers=admin_headers,
        )
        assert earlier.status_code == 422
        assert earlier.json()["detail"]["error"] == "INVALID_DATE_ORDER"

        weekend = client.put(
            f"/api/trades/{trade_id}",
            json={"cash_confirm_date": SAT.isoformat()},
            headers=admin_headers,
        )
        assert weekend.status_code == 422
        assert weekend.json()["detail"]["error"] == "NON_TRADING_DAY"

    def test_update_arrival_blocked_by_snapshot(self, client, admin_headers, test_db):
        """组内任一腿确认日及之后已有快照 → 拒绝改到账日"""
        trade_id = self._confirmed_sell(client, admin_headers, test_db)
        create_price_record(test_db, FUND, FUND_MARKET, T1, NAV)
        assert _gen(client, admin_headers, self.CODE, T).status_code == 200
        assert _gen(client, admin_headers, self.CODE, T1).status_code == 200
        upd = client.put(
            f"/api/trades/{trade_id}",
            json={"cash_confirm_date": T3.isoformat()},
            headers=admin_headers,
        )
        assert upd.status_code == 422
        assert upd.json()["detail"]["error"] == "SNAPSHOT_DEPENDENCY"

    def test_notes_only_allowed_on_confirmed_sell(self, client, admin_headers, test_db):
        """confirmed 卖出的 notes-only 更新放行（非会计更新）"""
        trade_id = self._confirmed_sell(client, admin_headers, test_db)
        resp = client.put(
            f"/api/trades/{trade_id}", json={"notes": "到账备注"}, headers=admin_headers,
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["notes"] == "到账备注"
        test_db.expire_all()
        assert _fund_leg(test_db, trade_id).status == "confirmed"


# ============================================================================
# 整组生命周期
# ============================================================================

class TestGroupLifecycle:
    def _confirmed_sell(self, client, admin_headers, test_db, code, arrival=T2):
        """半成品卖出组：创建（无 CASH 腿）→ 确认（建到账腿）"""
        _seed_portfolio(test_db, code, cash=10000.0, fund_shares=1000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": code, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "sell", "shares": 400.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        assert client.post(
            f"/api/trades/{trade_id}/confirm",
            params={"cash_confirm_date": arrival.isoformat()},
            headers=admin_headers,
        ).status_code == 200
        return trade_id

    def test_sell_unconfirm_deletes_cash_leg_and_reconfirm_reuses_group(
        self, client, admin_headers, test_db
    ):
        """卖出 unconfirm 删除 CASH 腿且保留组号；再次确认重建（不换组、不重复）"""
        trade_id = self._confirmed_sell(client, admin_headers, test_db, "IT493_LC1")
        fund_leg = _fund_leg(test_db, trade_id)
        group = fund_leg.transfer_group

        assert client.post(f"/api/trades/{trade_id}/unconfirm",
                           headers=admin_headers).status_code == 200
        test_db.expire_all()
        fund_leg = _fund_leg(test_db, trade_id)
        assert fund_leg.status == "pending"
        assert fund_leg.transfer_group == group
        assert _cash_leg(test_db, fund_leg) is None

        conf = client.post(
            f"/api/trades/{trade_id}/confirm",
            params={"cash_confirm_date": T3.isoformat()},
            headers=admin_headers,
        )
        assert conf.status_code == 200, conf.json()
        test_db.expire_all()
        fund_leg = _fund_leg(test_db, trade_id)
        assert fund_leg.transfer_group == group
        legs = _group_legs(test_db, fund_leg)
        assert len(legs) == 2
        assert _cash_leg(test_db, fund_leg).confirm_date == T3

    def test_cancel_group_reverts_buy_deduction(self, client, admin_headers, test_db):
        """取消买入组：基金腿与扣款腿一并 cancelled（现金回退）"""
        _seed_portfolio(test_db, "IT493_LC2", cash=50000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "IT493_LC2", "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "buy", "amount": 10000.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        assert client.post(f"/api/trades/{trade_id}/cancel",
                           headers=admin_headers).status_code == 200
        test_db.expire_all()
        fund_leg = _fund_leg(test_db, trade_id)
        cash_leg = _cash_leg(test_db, fund_leg)
        assert fund_leg.status == "cancelled"
        assert cash_leg.status == "cancelled"

    def test_cancel_blocked_after_snapshot_consumed(self, client, admin_headers, test_db):
        """扣款腿已进快照 → 取消整组被拒（先删快照）"""
        _seed_portfolio(test_db, "IT493_LC3", cash=50000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "IT493_LC3", "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "buy", "amount": 10000.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        assert _gen(client, admin_headers, "IT493_LC3", T).status_code == 200
        cancel = client.post(f"/api/trades/{trade_id}/cancel", headers=admin_headers)
        assert cancel.status_code == 422
        assert cancel.json()["detail"]["error"] == "SNAPSHOT_DEPENDENCY"
        test_db.expire_all()
        assert _fund_leg(test_db, trade_id).status == "pending"

    def test_delete_cancelled_group_allowed_after_snapshot(
        self, client, admin_headers, test_db
    ):
        """买入 → 当天取消 → 当天快照 → 整组仍可删除（cancelled 腿无会计效力）

        回归面（#518 评审的功能回退）：cancelled 腿不进入任何快照聚合，删除它对
        历史零影响；把它算作「组内任一腿」会让这条常规路径上的整组永久删不掉，
        而拒绝文案还要求用户为清理一个无会计影响的对象销毁快照链。
        """
        code = "IT493_LC7"
        _seed_portfolio(test_db, code, cash=50000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": code, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "buy", "amount": 10000.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        group = _fund_leg(test_db, trade_id).transfer_group
        # 取消时无快照（放行）；整组（基金腿 + 扣款腿）一并 cancelled
        assert client.post(f"/api/trades/{trade_id}/cancel",
                           headers=admin_headers).status_code == 200
        test_db.expire_all()
        assert _fund_leg(test_db, trade_id).status == "cancelled"
        assert _cash_leg(test_db, _fund_leg(test_db, trade_id)).status == "cancelled"

        # T 日快照：cancelled 腿不阻断生成，也不进现金账（基线原样）
        assert _gen(client, admin_headers, code, T).status_code == 200
        test_db.expire_all()
        positions = _positions(test_db, code, T)
        assert Decimal(str(_by_product(positions, "CASH")[0].cash_amount)) == Decimal("50000")
        assert _by_product(positions, "IN_TRANSIT_BUY") == []

        dele = client.delete(f"/api/trades/{trade_id}", headers=admin_headers)
        assert dele.status_code == 200, dele.json()
        test_db.expire_all()
        assert test_db.query(Trade).filter(Trade.transfer_group == group).count() == 0

    def test_delete_blocked_when_confirmed_deduction_snapshotted(
        self, client, admin_headers, test_db
    ):
        """守卫强度不回退：买入扣款腿 confirmed 且已进快照 → 删除整组仍被拒

        与上一条同批（#518 评审）：修功能回退不得把守卫修没了——删掉整组会让
        快照失去对应的现金事实（pending/confirmed 腿照旧参与保护）。
        """
        code = "IT493_LC8"
        _seed_portfolio(test_db, code, cash=50000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": code, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "buy", "amount": 10000.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        fund_leg = _fund_leg(test_db, trade_id)
        assert fund_leg.status == "pending"
        assert _cash_leg(test_db, fund_leg).status == "confirmed"
        assert _gen(client, admin_headers, code, T).status_code == 200

        dele = client.delete(f"/api/trades/{trade_id}", headers=admin_headers)
        assert dele.status_code == 422, dele.json()
        assert dele.json()["detail"]["error"] == "SNAPSHOT_DEPENDENCY"
        assert dele.json()["detail"]["details"]["from_date"] == T.isoformat()
        test_db.expire_all()
        assert _fund_leg(test_db, trade_id) is not None
        assert _cash_leg(test_db, _fund_leg(test_db, trade_id)) is not None

    def test_update_new_trade_date_inside_snapshot_range_blocked(
        self, client, admin_headers, test_db, monkeypatch
    ):
        """改日期时**新值**一并纳入组级保护（#493 承诺 / #518 评审补实现）

        `validate_trade_date` 强制新 trade_date 晚于最新快照日，使「新值落在已
        快照区间」在 REST 上不可达；此处临时放行那道闸门，直接检验组级保护自身
        是否真把新值算进去——去掉 `extra_dates`（或只传联动 confirm_date）本用例
        即转绿，是「承诺 > 实现」唯一的守门（不是「函数被调用」式空壳断言）。
        旧值（确认日 T2）远晚于唯一快照（T），故只有新值 T 能触发拒绝。
        """
        import app.services.trade_service as trade_service_module

        code = "IT493_LC9"
        _seed_portfolio(test_db, code, cash=10000.0, fund_shares=1000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": code, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "sell", "shares": 400.0,
                "platform_code": PLAT, "trade_date": T1.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]
        fund_leg = _fund_leg(test_db, trade_id)
        assert fund_leg.trade_date == T1 and fund_leg.confirm_date == T2
        # 唯一快照在 T 日：组内旧值（T1/T2）都晚于它，不构成保护
        assert _gen(client, admin_headers, code, T).status_code == 200
        test_db.expire_all()

        monkeypatch.setattr(
            trade_service_module, "validate_trade_date", lambda *a, **k: None
        )
        upd = client.put(
            f"/api/trades/{trade_id}", json={"trade_date": T.isoformat()},
            headers=admin_headers,
        )
        assert upd.status_code == 422, upd.json()
        assert upd.json()["detail"]["error"] == "SNAPSHOT_DEPENDENCY"
        assert upd.json()["detail"]["details"]["from_date"] == T.isoformat()
        test_db.expire_all()
        assert _fund_leg(test_db, trade_id).trade_date == T1  # 零写入

    def test_delete_removes_whole_group(self, client, admin_headers, test_db):
        """删除 pending 卖出组：组内腿全部删除"""
        _seed_portfolio(test_db, "IT493_LC4", cash=10000.0, fund_shares=1000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "IT493_LC4", "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "sell", "shares": 400.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        group = _fund_leg(test_db, trade_id).transfer_group
        assert client.delete(f"/api/trades/{trade_id}",
                             headers=admin_headers).status_code == 200
        test_db.expire_all()
        assert test_db.query(Trade).filter(Trade.transfer_group == group).count() == 0

    def test_rebal_cash_leg_direct_operations_rejected(
        self, client, admin_headers, test_db
    ):
        """调仓 CASH 腿只能由基金腿驱动：confirm/unconfirm/cancel/delete/PUT 全拒"""
        _seed_portfolio(test_db, "IT493_LC5", cash=50000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "IT493_LC5", "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "buy", "amount": 10000.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        cash_leg = _cash_leg(test_db, _fund_leg(test_db, resp.json()["id"]))

        # confirm 先撞 router 的 pending 状态门（扣款腿创建即 confirmed）→ INVALID_STATUS；
        # 服务层的同码守卫由下面的直接调用锁定
        r = client.post(f"/api/trades/{cash_leg.id}/confirm", headers=admin_headers)
        assert r.status_code == 422
        assert r.json()["detail"]["error"] == "INVALID_STATUS"
        with pytest.raises(BusinessError) as exc:
            confirm_single_trade(test_db, cash_leg, _product(test_db))
        assert exc.value.code == "CASH_TRADE_FORBIDDEN"

        for path in ("unconfirm", "cancel"):
            r = client.post(f"/api/trades/{cash_leg.id}/{path}", headers=admin_headers)
            assert r.status_code == 422, path
            assert r.json()["detail"]["error"] == "CASH_TRADE_FORBIDDEN", path

        dele = client.delete(f"/api/trades/{cash_leg.id}", headers=admin_headers)
        assert dele.status_code == 422
        assert dele.json()["detail"]["error"] == "CASH_TRADE_FORBIDDEN"

        put = client.put(
            f"/api/trades/{cash_leg.id}", json={"amount": 1.0}, headers=admin_headers,
        )
        assert put.status_code == 422
        assert put.json()["detail"]["error"] == "CASH_TRADE_FORBIDDEN"

        notes = client.put(
            f"/api/trades/{cash_leg.id}", json={"notes": "现金腿备注"}, headers=admin_headers,
        )
        assert notes.status_code == 200, notes.json()

    def test_pending_update_rejects_cash_confirm_date(self, client, admin_headers, test_db):
        """pending 交易传 cash_confirm_date → INVALID_PARAM（到账日只在确认时录入）"""
        _seed_portfolio(test_db, "IT493_LC6", cash=10000.0, fund_shares=1000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": "IT493_LC6", "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "sell", "shares": 400.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        upd = client.put(
            f"/api/trades/{resp.json()['id']}",
            json={"cash_confirm_date": T2.isoformat()},
            headers=admin_headers,
        )
        assert upd.status_code == 422
        assert upd.json()["detail"]["error"] == "INVALID_PARAM"


# ============================================================================
# 调仓退出 auto_confirm（#471 / #493 决策 6）
# ============================================================================

class TestRebalNotAutoConfirmed:
    def test_pending_rebal_trade_not_scanned_and_blocks_snapshot(
        self, client, admin_headers, test_db
    ):
        """到期 pending 调仓不被 auto_confirm 扫描；T 日快照仍被 pending 阻断"""
        code = "IT493_AC1"
        _seed_portfolio(test_db, code, cash=50000.0)
        create_product(test_db, code="ETF493AC", market="CN_EXCHANGE",
                       product_type="ETF", asset_class_code="ASSET_STOCK",
                       confirm_days=0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": code, "product_code": "ETF493AC",
                "market": "CN_EXCHANGE", "trade_type": "buy", "amount": 1000.0,
                "price": 1.5, "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]

        # auto_confirm(D0) 的 next_confirm_date = T：调仓腿不在扫描范围内
        results = auto_confirm_after_snapshot(test_db, code, D0)
        assert results == []
        test_db.expire_all()
        assert _fund_leg(test_db, trade_id).status == "pending"

        # 场内 confirm_date=T 的 pending 单仍阻断 T 日快照（口径不变）
        gen = _gen(client, admin_headers, code, T)
        assert gen.status_code == 422, gen.json()
        assert gen.json()["detail"]["error"] == "VALIDATION_FAILED"
        assert "待确认交易" in gen.json()["detail"]["message"]
        test_db.expire_all()
        assert _fund_leg(test_db, trade_id).status == "pending"

    def test_cross_day_transfer_branch_rejects_non_cash_leg(self, test_db):
        """跨天转移分支显式拒绝非 CASH 腿（不依赖可被 -O 关闭的 assert）"""
        code = "IT493_AC2"
        _seed_portfolio(test_db, code, cash=50000.0)
        group = "test_mixed_group_493"
        create_trade(
            test_db, code, "CASH", "", trade_type="buy", amount=1000.0,
            platform_code=PLAT, transfer_group=group,
            trade_date=T, confirm_date=T, status="pending",
        )
        create_trade(
            test_db, code, FUND, FUND_MARKET, trade_type="buy", amount=1000.0,
            shares=800.0, price=NAV, platform_code=PLAT, transfer_group=group,
            trade_date=T, confirm_date=T, status="pending",
        )
        test_db.flush()

        results = auto_confirm_after_snapshot(test_db, code, D0)
        entries = [r for r in results if r.get("transfer_group") == group]
        assert len(entries) == 1
        assert entries[0]["action"] == "auto_confirm_failed"
        assert entries[0]["code"] == "CASH_TRANSFER_NON_CASH_LEG"
        test_db.expire_all()
        assert test_db.query(Trade).filter(
            Trade.transfer_group == group, Trade.status == "pending"
        ).count() == 2

    def test_rebal_like_prefix_not_treated_as_rebal_group(self, test_db):
        """`rebalX…` 不是调仓组：SQL LIKE 的 `_` 是单字符通配，须转义（#518 评审）

        旧写法 `like("rebal_%")` 会把 `rebalX…` 一并排除，跨天转移分支因此静默
        漏掉一个合法组（组号非 `rebal_` 前缀时不该被调仓判据吞掉）。这里直接验证
        该组照常被自动确认——判据精确等价于 Python 侧 `startswith("rebal_")`。
        """
        code = "IT493_AC4"
        _seed_portfolio(test_db, code, cash=50000.0)
        group = "rebalX493"
        create_trade(
            test_db, code, "CASH", "", trade_type="sell", amount=1000.0,
            platform_code=PLAT, transfer_group=group,
            trade_date=T, confirm_date=T, status="confirmed",
        )
        create_trade(
            test_db, code, "CASH", "", trade_type="buy", amount=1000.0,
            platform_code=PLAT, transfer_group=group,
            trade_date=T, confirm_date=T, status="pending",
        )
        test_db.flush()

        results = auto_confirm_after_snapshot(test_db, code, D0)
        entries = [r for r in results if r.get("transfer_group") == group]
        assert len(entries) == 1, results
        assert entries[0]["action"] == "auto_confirmed"
        test_db.expire_all()
        assert test_db.query(Trade).filter(
            Trade.transfer_group == group, Trade.status == "pending"
        ).count() == 0

    def test_cancelled_arrival_leg_not_counted_as_in_transit(self, client, admin_headers, test_db):
        """跨天转移转入腿 cancelled 不计入在途（#493 收紧 buy pending）"""
        code = "IT493_AC3"
        _seed_portfolio(test_db, code, cash=50000.0)
        group = "test_cancelled_buy_493"
        create_trade(
            test_db, code, "CASH", "", trade_type="sell", amount=1000.0,
            platform_code=PLAT, transfer_group=group,
            trade_date=T, confirm_date=T, status="confirmed",
        )
        create_trade(
            test_db, code, "CASH", "", trade_type="buy", amount=1000.0,
            platform_code=PLAT, transfer_group=group,
            trade_date=T, confirm_date=T1, status="cancelled",
        )
        test_db.flush()
        assert _gen(client, admin_headers, code, T).status_code == 200
        test_db.expire_all()
        positions = _positions(test_db, code, T)
        assert _by_product(positions, "IN_TRANSIT_BUY") == []
        assert Decimal(str(_by_product(positions, "CASH")[0].cash_amount)) == Decimal("49000")


# ============================================================================
# 自身扣款加回口径
# ============================================================================

class TestOwnDeductionAddback:
    def test_deduction_in_snapshot_baseline_is_added_back(self, test_db):
        """扣款已进快照基线：确认仍可放行（否则合法确认被误拒）"""
        code = "IT493_AB1"
        _seed_portfolio(test_db, code, cash=10000.0)
        fund = create_trade_service(
            test_db, portfolio_code=code, product_code=FUND, market=FUND_MARKET,
            trade_type="buy", trade_date=T, actual_amount=Decimal("8000"),
            platform_code=PLAT,
        )
        test_db.flush()
        # T 日快照已消费扣款：现金 2000（基线），在途 8000
        create_position_snapshot(
            test_db, code, "CASH", "", T, cash_amount=2000, platform_code=PLAT,
        )
        create_value_snapshot(test_db, code, T,
                              total_value=2000, total_shares=2000, unit_price=1.0)
        test_db.flush()

        # 基线已扣过 8000；不加回会把本该放行的确认误拒
        validate_buy_cash_with_addback(
            test_db, code, Decimal("8000"), as_of=T1, self_trade=fund,
        )
        confirm_single_trade(test_db, fund, _product(test_db), confirm_date=T1)
        test_db.flush()
        assert fund.status == "confirmed"

    def test_deduction_after_query_date_not_added_back(self, test_db):
        """查询日早于扣款日：该扣款尚未计提，不得加回（available 报告为真实值）"""
        code = "IT493_AB2"
        _seed_portfolio(test_db, code, cash=10000.0)
        fund = create_trade_service(
            test_db, portfolio_code=code, product_code=FUND, market=FUND_MARKET,
            trade_type="buy", trade_date=T, actual_amount=Decimal("8000"),
            platform_code=PLAT,
        )
        test_db.flush()
        # as_of = D0（< 扣款日 T）：可用现金 = 10000（基线），自身 8000 尚未扣、不加回
        with pytest.raises(BusinessError) as exc:
            validate_buy_cash_with_addback(
                test_db, code, Decimal("10001"), as_of=D0, self_trade=fund,
            )
        assert exc.value.code == "INSUFFICIENT_CASH"
        assert Decimal(exc.value.details["available"]) == Decimal("10000.00")

        # as_of = T（= 扣款日）：加回后 10000，可承受到 10000
        validate_buy_cash_with_addback(
            test_db, code, Decimal("10000"), as_of=T, self_trade=fund,
        )

    def test_cancelled_deduction_leg_excluded_from_addback(self, test_db):
        """cancelled 扣款腿不参与加回（金额已回退，不再占用可用现金）"""
        code = "IT493_AB3"
        _seed_portfolio(test_db, code, cash=10000.0)
        fund = create_trade_service(
            test_db, portfolio_code=code, product_code=FUND, market=FUND_MARKET,
            trade_type="buy", trade_date=T, actual_amount=Decimal("8000"),
            platform_code=PLAT,
        )
        test_db.flush()
        assert len(_own_cash_sell_legs(test_db, fund)) == 1
        cash_leg = _cash_leg(test_db, fund)
        cash_leg.status = "cancelled"
        test_db.flush()
        assert _own_cash_sell_legs(test_db, fund) == []
        # 行为面（评审 follow-up）：只断言私有 filter 会在
        # `validate_buy_cash_with_addback` 绕开该 helper 时仍绿，而 cancelled 腿一旦
        # 被加回就等于虚增可用现金、放行超预算买入——故直接钉住加回未发生：
        # 可用现金仍是 10000，10001 必拒。
        with pytest.raises(BusinessError) as exc:
            validate_buy_cash_with_addback(
                test_db, code, Decimal("10001"), as_of=T, self_trade=fund,
            )
        assert exc.value.code == "INSUFFICIENT_CASH"
        assert Decimal(exc.value.details["available"]) == Decimal("10000.00")


# ============================================================================
# 读侧 / 预览
# ============================================================================

class TestReadSideAndPreview:
    def test_derived_cash_fields_on_list_and_get(self, client, admin_headers, test_db):
        """列表/详情基金腿回填现金平台与现金日；CASH 腿自身为 null"""
        code = "IT493_RD1"
        _seed_portfolio(test_db, code, cash=50000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": code, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "buy", "amount": 10000.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        cash_leg_id = _cash_leg(test_db, _fund_leg(test_db, trade_id)).id

        listed = client.get(
            "/api/trades", params={"portfolio_code": code}, headers=admin_headers,
        ).json()["items"]
        by_id = {item["id"]: item for item in listed}
        assert by_id[trade_id]["cash_platform_code"] == PLAT
        assert by_id[trade_id]["cash_confirm_date"] == T.isoformat()
        assert by_id[cash_leg_id]["cash_platform_code"] is None
        assert by_id[cash_leg_id]["cash_confirm_date"] is None

        single = client.get(f"/api/trades/{trade_id}", headers=admin_headers).json()
        assert single["cash_platform_code"] == PLAT
        assert single["cash_confirm_date"] == T.isoformat()

    def test_derived_fields_survive_status_filter_truncation(
        self, client, admin_headers, test_db
    ):
        """现金腿被状态筛选截断时，基金腿仍能读到到账信息（批量查询不受筛选影响）"""
        code = "IT493_RD2"
        _seed_portfolio(test_db, code, cash=10000.0, fund_shares=1000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": code, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "sell", "shares": 400.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        assert client.post(
            f"/api/trades/{trade_id}/confirm",
            params={"cash_confirm_date": T2.isoformat()},
            headers=admin_headers,
        ).status_code == 200

        # 只筛 pending：组内两腿均非 pending，本页为空
        listed = client.get(
            "/api/trades",
            params={"portfolio_code": code, "status": "pending"},
            headers=admin_headers,
        ).json()["items"]
        assert [i["id"] for i in listed] == []
        # 筛 confirmed：基金腿仍在，且派生字段完整
        listed = client.get(
            "/api/trades",
            params={"portfolio_code": code, "status": "confirmed"},
            headers=admin_headers,
        ).json()["items"]
        fund_rows = [i for i in listed if i["id"] == trade_id]
        assert len(fund_rows) == 1
        assert fund_rows[0]["cash_confirm_date"] == T2.isoformat()

    def test_preview_matches_confirm_and_writes_nothing(self, client, admin_headers, test_db):
        """preview 与 confirm 的有效现金平台/日期一致，且 preview 零写入"""
        code = "IT493_PV1"
        _seed_portfolio(test_db, code, cash=10000.0, fund_shares=1000.0)
        create_platform(test_db, code="P493_DEST")
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": code, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "sell", "shares": 400.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        before_trades = test_db.query(Trade).count()
        before_audits = test_db.query(AuditLog).count()

        prev = client.get(
            f"/api/trades/{trade_id}/preview",
            params={"cash_confirm_date": T2.isoformat(), "cash_platform_code": "P493_DEST"},
            headers=admin_headers,
        )
        assert prev.status_code == 200, prev.json()
        payload = prev.json()
        assert payload["preview"]["cash_confirm_date"] == T2.isoformat()
        assert payload["preview"]["cash_platform_code"] == "P493_DEST"
        assert payload["preview"]["amount"] == 500.0
        # preview 不写库：无新腿、无审计、trade 未改
        test_db.expire_all()
        assert test_db.query(Trade).count() == before_trades
        assert test_db.query(AuditLog).count() == before_audits
        fund_leg = _fund_leg(test_db, trade_id)
        assert fund_leg.status == "pending"
        assert _cash_leg(test_db, fund_leg) is None

        conf = client.post(
            f"/api/trades/{trade_id}/confirm",
            params={"cash_confirm_date": T2.isoformat(), "cash_platform_code": "P493_DEST"},
            headers=admin_headers,
        )
        assert conf.status_code == 200, conf.json()
        confirmed = conf.json()["trade"]
        assert confirmed["cash_confirm_date"] == payload["preview"]["cash_confirm_date"]
        assert confirmed["cash_platform_code"] == payload["preview"]["cash_platform_code"]
        assert confirmed["amount"] == payload["preview"]["amount"]

    def test_preview_rejects_same_as_confirm(self, client, admin_headers, test_db):
        """preview 与 confirm 共用校验：A < C 同样被拒且无写入"""
        code = "IT493_PV2"
        _seed_portfolio(test_db, code, cash=10000.0, fund_shares=1000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": code, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "sell", "shares": 400.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        prev = client.get(
            f"/api/trades/{trade_id}/preview",
            params={"cash_confirm_date": D0.isoformat()},
            headers=admin_headers,
        )
        assert prev.status_code == 422
        assert prev.json()["detail"]["error"] == "INVALID_DATE_ORDER"
        test_db.expire_all()
        assert _fund_leg(test_db, trade_id).status == "pending"


# ============================================================================
# 未来资金不可提前使用
# ============================================================================

class TestArrivalCashNotUsableBeforeArrival:
    def test_available_cash_endpoint_excludes_future_arrival(
        self, client, admin_headers, test_db
    ):
        """到账日之前 available-cash 不增加（接口按当天时点口径）"""
        code = "IT493_FU1"
        _seed_portfolio(test_db, code, cash=10000.0, fund_shares=1000.0)
        # 动态取一个未来交易日（日历随 today 滚动到 today+365，故必然覆盖）
        future = date.today() + timedelta(days=30)
        while future.weekday() >= 5:
            future += timedelta(days=1)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": code, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "sell", "shares": 400.0,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        trade_id = resp.json()["id"]
        assert client.post(
            f"/api/trades/{trade_id}/confirm",
            params={"cash_confirm_date": future.isoformat()},
            headers=admin_headers,
        ).status_code == 200

        cash = client.get(
            f"/api/positions/portfolio/{code}/available-cash",
            params={"platform_code": PLAT},
            headers=admin_headers,
        ).json()["available_cash"]
        # 到账日在未来 → 卖出回笼不计入今天可用现金
        assert float(cash) == 10000.0

    def test_cash_transfer_uses_transfer_date_as_of(self, test_db):
        """现金转移按 transfer_date 验资：到账日前的转入不可用于当天转出"""
        from app.services.cash_transfer_service import create_cash_transfer

        code = "IT493_FU2"
        _seed_portfolio(test_db, code, cash=10000.0, fund_shares=1000.0)
        create_platform(test_db, code="P493_TO")
        fund = create_trade_service(
            test_db, portfolio_code=code, product_code=FUND, market=FUND_MARKET,
            trade_type="sell", trade_date=T, shares=Decimal("400"), platform_code=PLAT,
        )
        test_db.flush()
        confirm_single_trade(test_db, fund, _product(test_db), cash_confirm_date=T2)
        test_db.flush()

        # transfer_date = T1（< 到账日 T2）：可用现金仍为 10000，转 10500 必拒
        with pytest.raises(BusinessError) as exc:
            create_cash_transfer(
                test_db, portfolio_code=code, from_platform=PLAT, to_platform="P493_TO",
                amount=Decimal("10500"), transfer_date=T1,
            )
        assert exc.value.code == "INSUFFICIENT_CASH"

        # transfer_date = T2（= 到账日）：10500 可用，放行
        result = create_cash_transfer(
            test_db, portfolio_code=code, from_platform=PLAT, to_platform="P493_TO",
            amount=Decimal("10500"), transfer_date=T2,
        )
        assert result["amount"] == 10500.0


# ============================================================================
# 记账守恒（计划 §4.1.9）
# ============================================================================

class TestConservationWithFee:
    """有费用时断言**预期**变化，不误要求 `total_value` 恒定。

    买入含费：费用是组合的对外支出，确认后不再有等额资产接住它，故总资产
    恰降一个手续费；此前 T 日只是「现金 → 等额在途」，总资产不动。
    """

    def test_buy_fee_reduces_total_value_by_exactly_fee(
        self, client, admin_headers, test_db
    ):
        code = "IT493_FEE1"
        _seed_portfolio(test_db, code, cash=50000.0)
        resp = client.post(
            "/api/trades",
            json={
                "portfolio_code": code, "product_code": FUND,
                "market": FUND_MARKET, "trade_type": "buy",
                "actual_amount": 10000.0, "fee": 5.0, "price": NAV,
                "platform_code": PLAT, "trade_date": T.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code in (200, 201), resp.json()
        trade_id = resp.json()["id"]
        # 含费现金支出 10000、净额 9995（费用不买份额）
        assert Decimal(str(resp.json()["amount"])) == Decimal("9995")
        assert Decimal(str(resp.json()["actual_amount"])) == Decimal("10000")

        # T 日：现金 -10000、等额买入在途 +10000 → 总资产不变（费用尚未落地）
        assert _gen(client, admin_headers, code, T).status_code == 200
        test_db.expire_all()
        snap_t = test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == code,
            PortfolioValueSnapshot.snapshot_date == T,
        ).first()
        assert Decimal(str(snap_t.total_value)) == Decimal("50000")
        assert Decimal(str(snap_t.in_transit_total)) == Decimal("10000")

        # T1 确认：基金按净额 9995 入账（7996 份 × 1.25）→ 总资产恰降 5
        create_price_record(test_db, FUND, FUND_MARKET, T1, NAV)
        conf = client.post(f"/api/trades/{trade_id}/confirm", headers=admin_headers)
        assert conf.status_code == 200, conf.json()
        assert _gen(client, admin_headers, code, T1).status_code == 200
        test_db.expire_all()

        positions = _positions(test_db, code, T1)
        cash = _by_product(positions, "CASH")
        fund = _by_product(positions, FUND)
        assert _by_product(positions, "IN_TRANSIT_BUY") == []
        assert Decimal(str(cash[0].cash_amount)) == Decimal("40000")
        assert Decimal(str(fund[0].shares)) == Decimal("7996")

        snap_t1 = test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == code,
            PortfolioValueSnapshot.snapshot_date == T1,
        ).first()
        assert Decimal(str(snap_t1.total_value)) == Decimal("49995")
        assert Decimal(str(snap_t1.in_transit_total)) == Decimal("0")
        # 差额恰为手续费，不把「无常量」错当成「必须与 T 日相等」
        assert Decimal(str(snap_t.total_value)) - Decimal(str(snap_t1.total_value)) == Decimal("5")
