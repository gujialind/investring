# ============================================================================
# 集成测试：forced_adjustment 份额变动进持仓快照 (test_snapshot_forced_adjustment.py)
# ============================================================================
# issue #263：forced_adjustment 是唯一「份额由用户直填」的事件类型，但其
# shares_change 在快照生成的事件应用循环中被 `continue` 静默跳过，事件显示
# 「已确认」、快照却不含该变动。
#
# 覆盖验收断言：
# 1. 仅填份额（+1.00）→ ex_date 当日快照份额 = 前值 + 1.00
# 2. 份额 + 现金同填 → 份额行与 CASH 行双双按增量生效
# 3. 负份额 → 份额减少，total_value / unit_price 随之变化
# 4. cash_dividend 回归保护：基金行 shares 不变（shares_change=0 语义）
# 5. generate 与 recalculate 两条路径对同一事件结果一致（三表数值相同），
#    且重算的级联回退 + 自动重确认往返不丢失用户直填值
# 6. 手动 unconfirm 保留用户直填值，重确认后快照照常应用（与 5 的级联路径互补）
# ============================================================================

from datetime import date
from decimal import Decimal

from app.models import PortfolioPosition, PortfolioValueSnapshot, InvestorHolding
from app.models.price_record import PriceRecord
from app.models.share_change_event import ShareChangeEvent
from app.services.share_change_event_service import (
    create_share_change_event as svc_create_event,
    confirm_share_change_event,
    unconfirm_share_change_event,
)
from app.services.snapshot_service import generate_daily_snapshots, recalculate_snapshots
from app.services.subscription_service import (
    create_subscription,
    confirm_single_subscription,
)
from app.services.trade_service import (
    create_trade as create_trade_service,
    confirm_single_trade,
)
from tests.factories import (
    create_portfolio,
    create_product,
    create_price_record,
    create_position_snapshot,
    create_value_snapshot,
    create_investor,
    create_investor_holding,
)

D0 = date(2025, 6, 6)        # 周五（基线快照日 = 权益登记日）
EX_DAY = date(2025, 6, 9)    # 周一（除息日 = 生成目标日）
SUB_APPLY = date(2025, 6, 5)  # 申购申请日（T+1 确认，到账日 = D0）

FUND = "FA263.OF"


def _ensure_price(db, record_date: date, unit_price: float = 1.0):
    """价格记录按产品维度唯一，存在则跳过"""
    if not db.query(PriceRecord).filter(
        PriceRecord.product_code == FUND, PriceRecord.price_date == record_date,
    ).first():
        create_price_record(db, FUND, "CN_OTC", record_date, unit_price)


def _setup(db, port_code: str, fund_shares: float = 100.0, cash: float = 1000.0):
    """工厂直造基线三表快照（D0）：仅供单日 generate 用例使用"""
    create_portfolio(db, code=port_code, status="active")
    create_product(db, code=FUND, market="CN_OTC",
                   product_type="OEF", asset_class_code="ASSET_STOCK")
    create_position_snapshot(
        db, port_code, FUND, "CN_OTC", snapshot_date=D0,
        shares=fund_shares, unit_price=1.0, cost_price=1.0,
        market_value=fund_shares, platform_code="MYCF",
    )
    create_position_snapshot(
        db, port_code, "CASH", "", snapshot_date=D0,
        cash_amount=cash, unit_price=None, cost_price=None,
        market_value=cash, platform_code="MYCF",
    )
    total = fund_shares + cash
    create_value_snapshot(db, port_code, D0,
                          total_value=total, total_shares=total, unit_price=1.0)
    create_investor_holding(db, port_code, "VIEWER", D0, shares=total)
    _ensure_price(db, EX_DAY)


def _setup_real_history(db, port_code: str, investor_code: str):
    """真实业务流构建基线（重算用例专用）：首次申购入金 1100 → 买入基金 100
    → 生成 D0 快照（现金 1000 + 基金 100 份）。重算从零重建时可复现。"""
    create_portfolio(db, code=port_code, status="active")
    product = create_product(db, code=FUND, market="CN_OTC",
                             product_type="OEF", asset_class_code="ASSET_STOCK",
                             confirm_days=0)
    create_investor(db, code=investor_code)
    _ensure_price(db, D0)
    _ensure_price(db, EX_DAY)

    sub = create_subscription(
        db, portfolio_code=port_code, investor_code=investor_code,
        platform_code="MYCF", sub_type="subscribe",
        amount=Decimal("1100.00"), apply_date=SUB_APPLY,
    )
    db.flush()
    confirm_single_subscription(db, sub)  # 首次申购净值 1.0000
    db.flush()

    buy = create_trade_service(
        db, portfolio_code=port_code, product_code=FUND, market="CN_OTC",
        trade_type="buy", trade_date=D0,
        actual_amount=Decimal("100.00"), platform_code="MYCF",
    )
    db.flush()
    confirm_single_trade(db, buy, product)  # confirm_days=0 → confirm_date=D0
    db.flush()

    result = generate_daily_snapshots(db, port_code, D0)
    assert result["success"] is True, result


def _create_confirmed_event(db, port_code: str, *, shares_change=None, cash_change=None,
                            div_cash=None, shares_after=None, event_type="forced_adjustment"):
    """走真实创建 + 确认流程（不直接造 confirmed 记录）"""
    event = svc_create_event(
        db,
        portfolio_code=port_code,
        event_type=event_type,
        product_code=FUND,
        market="CN_OTC",
        platform_code="MYCF",
        ex_date=EX_DAY,
        entitlement_date=D0,
        shares_change=shares_change,
        cash_change=cash_change,
        div_cash=div_cash,
        shares_after=shares_after,
    )
    db.flush()
    confirm_share_change_event(db, event)
    db.flush()
    return event


def _pos(db, port_code: str, product_code: str, snapshot_date: date):
    return db.query(PortfolioPosition).filter(
        PortfolioPosition.portfolio_code == port_code,
        PortfolioPosition.product_code == product_code,
        PortfolioPosition.snapshot_date == snapshot_date,
    ).first()


class TestForcedAdjustmentSharesIntoSnapshot:
    """issue #263：forced_adjustment 份额增量随 ex_date 当日入快照"""

    def test_shares_only_increment_applied(self, test_db):
        """验收 1：仅填 +1.00 份额（现金空）→ 当日快照份额 = 前值 + 1.00"""
        _setup(test_db, "FA_P1")
        _create_confirmed_event(test_db, "FA_P1", shares_change=Decimal("1.00"))

        result = generate_daily_snapshots(test_db, "FA_P1", EX_DAY)
        assert result["success"] is True

        pos = _pos(test_db, "FA_P1", FUND, EX_DAY)
        assert pos is not None
        assert Decimal(str(pos.shares)) == Decimal("101.00")

    def test_shares_and_cash_both_applied(self, test_db):
        """验收 2：份额与现金同填 → 份额行与 CASH 行双双按增量生效"""
        _setup(test_db, "FA_P2")
        _create_confirmed_event(
            test_db, "FA_P2",
            shares_change=Decimal("1.00"), cash_change=Decimal("50.00"),
        )

        result = generate_daily_snapshots(test_db, "FA_P2", EX_DAY)
        assert result["success"] is True

        pos = _pos(test_db, "FA_P2", FUND, EX_DAY)
        assert Decimal(str(pos.shares)) == Decimal("101.00")
        cash_row = _pos(test_db, "FA_P2", "CASH", EX_DAY)
        assert cash_row is not None
        assert Decimal(str(cash_row.cash_amount)) == Decimal("1050.00")

    def test_negative_shares_change_applied(self, test_db):
        """验收 3：负份额 → 份额减少，total_value / unit_price 随之变化"""
        _setup(test_db, "FA_P3")
        _create_confirmed_event(test_db, "FA_P3", shares_change=Decimal("-2.00"))

        result = generate_daily_snapshots(test_db, "FA_P3", EX_DAY)
        assert result["success"] is True

        pos = _pos(test_db, "FA_P3", FUND, EX_DAY)
        assert Decimal(str(pos.shares)) == Decimal("98.00")

        snap = test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == "FA_P3",
            PortfolioValueSnapshot.snapshot_date == EX_DAY,
        ).one()
        # 98 份 × 1.0 + 现金 1000 = 1098；组合总份额仅因申赎变化（仍为 1100），
        # 故 unit_price = 1098/1100 = 0.9982 —— 随份额调整而变
        assert Decimal(str(snap.total_value)) == Decimal("1098.00")
        assert Decimal(str(snap.unit_price)) == Decimal("0.9982")

    def test_cash_dividend_shares_unchanged(self, test_db):
        """验收 4（回归保护）：现金分红后基金行份额不变"""
        _setup(test_db, "FA_P4")
        _create_confirmed_event(
            test_db, "FA_P4", event_type="cash_dividend", div_cash=Decimal("0.05"),
        )

        result = generate_daily_snapshots(test_db, "FA_P4", EX_DAY)
        assert result["success"] is True

        pos = _pos(test_db, "FA_P4", FUND, EX_DAY)
        assert Decimal(str(pos.shares)) == Decimal("100.00")
        cash_row = _pos(test_db, "FA_P4", "CASH", EX_DAY)
        # 100 份 × 0.05 = 5.00 → 1005
        assert Decimal(str(cash_row.cash_amount)) == Decimal("1005.00")


class TestGenerateRecalcConsistency:
    """验收 5：同一事件分别走 generate（重建最新日）与重算全区间 → 三表一致"""

    def _row_signature(self, db, port_code: str):
        positions = {
            (p.product_code, p.market, p.platform_code): (
                str(p.shares) if p.shares is not None else None,
                str(p.cash_amount) if p.cash_amount is not None else None,
                str(p.market_value),
            )
            for p in db.query(PortfolioPosition).filter(
                PortfolioPosition.portfolio_code == port_code,
                PortfolioPosition.snapshot_date == EX_DAY,
            ).all()
        }
        snap = db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == port_code,
            PortfolioValueSnapshot.snapshot_date == EX_DAY,
        ).one()
        holdings = {
            h.investor_code: str(h.shares)
            for h in db.query(InvestorHolding).filter(
                InvestorHolding.portfolio_code == port_code,
                InvestorHolding.snapshot_date == EX_DAY,
            ).all()
        }
        return positions, (str(snap.total_value), str(snap.total_shares), str(snap.unit_price)), holdings

    def test_generate_and_recalculate_agree(self, test_db):
        # 两个同构组合（真实申赎/交易历史支撑基线，共用同一投资人）：
        # FA_G 走 generate，FA_R 走重算
        _setup_real_history(test_db, "FA_G", "FA_INV")
        _setup_real_history(test_db, "FA_R", "FA_INV")
        for pc in ("FA_G", "FA_R"):
            _create_confirmed_event(
                test_db, pc,
                shares_change=Decimal("1.00"), cash_change=Decimal("50.00"),
            )

        gen = generate_daily_snapshots(test_db, "FA_G", EX_DAY)
        assert gen["success"] is True

        recalc = recalculate_snapshots(test_db, "FA_R", D0, EX_DAY)
        errors = [r["errors"] for r in recalc["results"] if r["errors"]]
        assert not errors, f"重算失败: {errors}"
        test_db.commit()  # recalculate 不 commit，事务边界归调用方

        assert self._row_signature(test_db, "FA_G") == self._row_signature(test_db, "FA_R")

        # 重算后事件的用户输入值不得丢失（级联回退 + 重确认往返保真）
        recalc_event = test_db.query(ShareChangeEvent).filter(
            ShareChangeEvent.portfolio_code == "FA_R",
            ShareChangeEvent.event_type == "forced_adjustment",
        ).one()
        assert recalc_event.status == "confirmed"
        assert Decimal(str(recalc_event.shares_change)) == Decimal("1.00")
        assert Decimal(str(recalc_event.cash_change)) == Decimal("50.00")


class TestManualUnconfirmPreservesUserInput:
    """手动 unconfirm 路径：用户直填值保留（验收 5 只覆盖重算的级联回退路径）"""

    def test_manual_unconfirm_keeps_user_input_and_reconfirm_applies(self, test_db):
        """手动 unconfirm → 直填三字段保留、确认回写字段清空 → 重确认后快照仍含增量"""
        _setup(test_db, "FA_U1")
        event = _create_confirmed_event(
            test_db, "FA_U1",
            shares_change=Decimal("1.00"),
            cash_change=Decimal("50.00"),
            shares_after=Decimal("101.00"),
        )

        unconfirm_share_change_event(test_db, event)
        test_db.flush()

        assert event.status == "pending"
        # 用户直填值（唯一存处）不得清空
        assert Decimal(str(event.shares_change)) == Decimal("1.00")
        assert Decimal(str(event.cash_change)) == Decimal("50.00")
        assert Decimal(str(event.shares_after)) == Decimal("101.00")
        # 确认时回写的计算字段照常清空
        assert event.entitlement_shares is None
        assert event.shares_before is None

        # 重确认不丢失调整量，且快照照常应用
        confirm_share_change_event(test_db, event)
        test_db.flush()
        assert event.status == "confirmed"
        assert Decimal(str(event.shares_change)) == Decimal("1.00")
        assert Decimal(str(event.cash_change)) == Decimal("50.00")

        result = generate_daily_snapshots(test_db, "FA_U1", EX_DAY)
        assert result["success"] is True

        pos = _pos(test_db, "FA_U1", FUND, EX_DAY)
        assert Decimal(str(pos.shares)) == Decimal("101.00")
        cash_row = _pos(test_db, "FA_U1", "CASH", EX_DAY)
        assert Decimal(str(cash_row.cash_amount)) == Decimal("1050.00")


class TestEventPositionGuards:
    """issue #278：事件指向不存在持仓的静默失败防线
    （确认侧精查 + 快照侧硬拒绝 + 清零告警）"""

    def test_event_pointing_to_nonexistent_position_hard_rejected(self, test_db):
        """验收 1：存量坏事件指向不存在持仓 → 快照生成失败，无幽灵行"""
        import pytest
        from app.services.exceptions import BusinessError
        from tests.factories import create_share_change_event

        _setup(test_db, "FA_G1")
        create_product(test_db, code="GHOST.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        # 直造存量坏事件（绕过创建/确认入口，模拟历史脏数据）
        create_share_change_event(
            test_db, "FA_G1", "GHOST.OF", "CN_OTC",
            event_type="forced_adjustment", ex_date=EX_DAY,
            entitlement_date=D0, status="confirmed",
            platform_code="MYCF", shares_change=Decimal("10.00"),
            entitlement_shares=Decimal("0"), shares_before=Decimal("0"),
        )

        with pytest.raises(BusinessError) as exc:
            generate_daily_snapshots(test_db, "FA_G1", EX_DAY)
        assert exc.value.code == "POSITION_NOT_FOUND"
        assert exc.value.details["product_code"] == "GHOST.OF"
        # 无幽灵持仓行（生成在任何写入前失败）
        assert test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == "FA_G1",
            PortfolioPosition.product_code == "GHOST.OF",
        ).count() == 0

    def test_cash_row_guard(self, test_db):
        """存量份额事件命中现金行 → 硬拒绝（份额不得静默丢弃）"""
        import pytest
        from app.services.exceptions import BusinessError
        from tests.factories import create_share_change_event

        _setup(test_db, "FA_G2")
        create_share_change_event(
            test_db, "FA_G2", "CASH", "",
            event_type="forced_adjustment", ex_date=EX_DAY,
            entitlement_date=D0, status="confirmed",
            platform_code="MYCF", shares_change=Decimal("1.00"),
        )

        with pytest.raises(BusinessError) as exc:
            generate_daily_snapshots(test_db, "FA_G2", EX_DAY)
        assert exc.value.code == "POSITION_NOT_FOUND"

    def test_lof_market_misfill_rejected_at_confirm(self, test_db):
        """验收 1（提前快失败）：LOF market 误填在确认侧即被精查拒绝"""
        import pytest
        from app.services.exceptions import BusinessError

        _setup(test_db, "FA_G3")
        create_product(test_db, code="LOF278", market="SZ",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_product(test_db, code="LOF278", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_position_snapshot(
            test_db, "FA_G3", "LOF278", "SZ", snapshot_date=D0,
            shares=50.0, unit_price=1.0, cost_price=1.0,
            market_value=50.0, platform_code="MYCF",
        )

        # market 误填为场外 → 确认拒绝
        event = svc_create_event(
            test_db, portfolio_code="FA_G3", event_type="forced_adjustment",
            product_code="LOF278", market="CN_OTC", platform_code="MYCF",
            ex_date=EX_DAY, entitlement_date=D0,
            shares_change=Decimal("1.00"),
        )
        test_db.flush()
        with pytest.raises(BusinessError) as exc:
            confirm_share_change_event(test_db, event)
        assert exc.value.code == "POSITION_NOT_FOUND"
        assert event.status == "pending"

        # 正向对照：market 正确则照常确认
        event_ok = svc_create_event(
            test_db, portfolio_code="FA_G3", event_type="forced_adjustment",
            product_code="LOF278", market="SZ", platform_code="MYCF",
            ex_date=EX_DAY, entitlement_date=D0,
            shares_change=Decimal("1.00"),
        )
        test_db.flush()
        confirm_share_change_event(test_db, event_ok)
        assert event_ok.status == "confirmed"

    def test_negative_adjustment_zeroed_position_warning(self, test_db):
        """验收 2：负向调整打空持仓行 → 生成成功但携带 event_zeroed_position 告警"""
        _setup(test_db, "FA_G4")
        event = _create_confirmed_event(
            test_db, "FA_G4", shares_change=Decimal("-100.00"))  # 100 − 100 = 0

        result = generate_daily_snapshots(test_db, "FA_G4", EX_DAY)
        assert result["success"] is True

        warnings = result["warnings"]
        assert warnings, "应携带清零告警"
        zeroed = [w for w in warnings if w["type"] == "event_zeroed_position"]
        assert len(zeroed) == 1
        assert zeroed[0]["product_code"] == FUND
        assert zeroed[0]["platform_code"] == "MYCF"
        assert zeroed[0]["event_id"] == event.id
        # 持仓行不写入快照（份额 ≤ 0 跳过）
        assert _pos(test_db, "FA_G4", FUND, EX_DAY) is None

    def test_recalculate_with_bad_event_fails_and_leaves_no_residue(self, test_db):
        """重算路径：坏事件致逐日失败 → errors 非空，调用方不 commit 则无残留"""
        from tests.factories import create_share_change_event

        _setup_real_history(test_db, "FA_BAD", "FA_BAD_INV")
        create_product(test_db, code="GHOST2.OF", market="CN_OTC",
                       product_type="OEF", asset_class_code="ASSET_STOCK")
        create_share_change_event(
            test_db, "FA_BAD", "GHOST2.OF", "CN_OTC",
            event_type="forced_adjustment", ex_date=EX_DAY,
            entitlement_date=D0, status="confirmed",
            platform_code="MYCF", shares_change=Decimal("10.00"),
            entitlement_shares=Decimal("0"), shares_before=Decimal("0"),
        )

        recalc = recalculate_snapshots(test_db, "FA_BAD", D0, EX_DAY)
        errors = [r["errors"] for r in recalc["results"] if r["errors"]]
        assert errors, "坏事件应使重算失败"

        test_db.rollback()  # 有 errors → 调用方整体回滚（「要么完整成功，要么无变化」）
        # 基线快照完好、无重算残留
        assert _pos(test_db, "FA_BAD", FUND, D0) is not None
        assert test_db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == "FA_BAD",
            PortfolioPosition.snapshot_date == EX_DAY,
        ).count() == 0

    def test_cash_only_adjustment_regression(self, test_db):
        """回归：只填 cash_change 的 forced_adjustment 照常生效（#279 校验不误伤）"""
        _setup(test_db, "FA_G6")
        _create_confirmed_event(test_db, "FA_G6", cash_change=Decimal("50.00"))

        result = generate_daily_snapshots(test_db, "FA_G6", EX_DAY)
        assert result["success"] is True

        pos = _pos(test_db, "FA_G6", FUND, EX_DAY)
        assert Decimal(str(pos.shares)) == Decimal("100.00")  # 基金份额不变
        cash_row = _pos(test_db, "FA_G6", "CASH", EX_DAY)
        assert Decimal(str(cash_row.cash_amount)) == Decimal("1050.00")

    def test_cash_product_cash_only_adjustment_snapshot_ok(self, test_db):
        """对 CASH 产品的纯现金调整（#279 放行）不得被现金行守卫误杀"""
        _setup(test_db, "FA_G7")
        event = svc_create_event(
            test_db, portfolio_code="FA_G7", event_type="forced_adjustment",
            product_code="CASH", market="", platform_code="MYCF",
            ex_date=EX_DAY, entitlement_date=D0,
            cash_change=Decimal("80.00"),
        )
        test_db.flush()
        confirm_share_change_event(test_db, event)
        test_db.flush()
        assert event.status == "confirmed"

        result = generate_daily_snapshots(test_db, "FA_G7", EX_DAY)
        assert result["success"] is True

        cash_row = _pos(test_db, "FA_G7", "CASH", EX_DAY)
        assert Decimal(str(cash_row.cash_amount)) == Decimal("1080.00")


class TestFundLevelEventMarketScopedSnapshot:
    """issue #461：LOF 双市场基金级拆分只作用于 event.market 的持仓行。

    修复前 all_positions 跨市场聚合、子记录 market 恒写 event.market——同平台时
    两条子记录落在同一 fund_key 上重复累加（OTC 50 → 200），跨平台时快照生成
    直接 POSITION_NOT_FOUND。应用侧按 (product_code, market, platform_code) 匹配。
    """

    LOF = "LOF461.SZ"
    PORT = "FA_LOF461"

    def _ensure_lof_price(self, db, market: str, d: date, price: float = 1.0):
        if not db.query(PriceRecord).filter(
            PriceRecord.product_code == self.LOF,
            PriceRecord.market == market,
            PriceRecord.price_date == d,
        ).first():
            create_price_record(db, self.LOF, market, d, price)

    def _pos_market(self, db, market: str, d: date):
        return db.query(PortfolioPosition).filter(
            PortfolioPosition.portfolio_code == self.PORT,
            PortfolioPosition.product_code == self.LOF,
            PortfolioPosition.market == market,
            PortfolioPosition.snapshot_date == d,
        ).one()

    def _setup(self, db):
        create_portfolio(db, code=self.PORT, status="active")
        create_product(db, code=self.LOF, market="CN_EXCHANGE",
                       product_type="LOF", asset_class_code="ASSET_STOCK", confirm_days=0)
        create_product(db, code=self.LOF, market="CN_OTC",
                       product_type="LOF", asset_class_code="ASSET_STOCK")
        create_position_snapshot(
            db, self.PORT, self.LOF, "CN_EXCHANGE", snapshot_date=D0,
            shares=100.0, unit_price=1.0, cost_price=1.0,
            market_value=100.0, platform_code="MYCF",
        )
        create_position_snapshot(
            db, self.PORT, self.LOF, "CN_OTC", snapshot_date=D0,
            shares=50.0, unit_price=1.0, cost_price=1.0,
            market_value=50.0, platform_code="MYCF",
        )
        create_position_snapshot(
            db, self.PORT, "CASH", "", snapshot_date=D0,
            cash_amount=1000.0, unit_price=None, cost_price=None,
            market_value=1000.0, platform_code="MYCF",
        )
        create_value_snapshot(db, self.PORT, D0,
                              total_value=1150, total_shares=1150, unit_price=1.0)
        create_investor_holding(db, self.PORT, "VIEWER", D0, shares=1150)
        self._ensure_lof_price(db, "CN_EXCHANGE", EX_DAY)
        self._ensure_lof_price(db, "CN_OTC", EX_DAY)

    def test_fund_level_split_scopes_market_in_snapshot(self, test_db):
        """验收：market=CN_OTC 拆分 ×2 → 快照日 OTC 50→100、EX 持仓恒 100"""
        self._setup(test_db)
        event = svc_create_event(
            test_db, portfolio_code=self.PORT, event_type="share_split",
            product_code=self.LOF, market="CN_OTC",
            ex_date=EX_DAY, entitlement_date=D0, ratio=Decimal("2"),
        )
        test_db.flush()
        confirm_share_change_event(test_db, event)
        test_db.flush()

        result = generate_daily_snapshots(test_db, self.PORT, EX_DAY)
        assert result["success"] is True, result

        otc = self._pos_market(test_db, "CN_OTC", EX_DAY)
        exch = self._pos_market(test_db, "CN_EXCHANGE", EX_DAY)
        assert Decimal(str(otc.shares)) == Decimal("100.00")     # 50 × 2
        assert Decimal(str(exch.shares)) == Decimal("100.00")    # 不受 OTC 事件影响


class TestAutoConfirmEventDelegation:
    """auto_confirm 事件段委托公共确认实现（#278/#279）：
    失败仅记录不阻断、重算路径不绕过校验"""

    def test_dirty_pending_event_records_failed_not_blocking(self, test_db):
        """存量脏事件（双空）被委托校验拒绝 → 记 auto_confirm_failed、
        保持 pending、不上抛；同批合法事件照常自动确认"""
        from app.services.snapshot_service import auto_confirm_after_snapshot
        from tests.factories import create_share_change_event

        _setup(test_db, "FA_AC1")
        # 直造存量脏数据（#279 校验前创建的双空 forced_adjustment）
        dirty = create_share_change_event(
            test_db, "FA_AC1", FUND, "CN_OTC",
            event_type="forced_adjustment", ex_date=EX_DAY,
            entitlement_date=D0, status="pending",
            platform_code="MYCF",
        )
        # 同批合法事件（走真实创建入口，未确认）
        valid = svc_create_event(
            test_db, portfolio_code="FA_AC1", event_type="forced_adjustment",
            product_code=FUND, market="CN_OTC", platform_code="MYCF",
            ex_date=EX_DAY, entitlement_date=D0,
            shares_change=Decimal("1.00"),
        )
        test_db.flush()

        results = auto_confirm_after_snapshot(test_db, "FA_AC1", D0)

        failed = [r for r in results if r.get("id") == dirty.id]
        assert failed and failed[0]["action"] == "auto_confirm_failed"
        assert "shares_change" in failed[0]["error"]  # EMPTY_ADJUSTMENT 文案
        confirmed = [r for r in results if r.get("id") == valid.id]
        assert confirmed and confirmed[0]["action"] == "auto_confirmed"

        test_db.refresh(dirty)
        test_db.refresh(valid)
        assert dirty.status == "pending"
        assert valid.status == "confirmed"
