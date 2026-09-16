# ============================================================================
# 单元测试：持仓服务现金计算 (test_position_service.py)
# ============================================================================
# 覆盖 app/services/position_service.py 中的现金计算函数：
# - get_cash_value（含 manual_market_value 绝对替换，辅助审计场景）
# - calculate_available_cash（基线读快照表，回归 issue #52；
#   无快照降级路径每笔只计一次，回归 issue #515）
# ============================================================================

from datetime import date
from decimal import Decimal

from app.services.position_service import (
    compute_cash_balance,
    get_cash_value,
    calculate_available_cash,
    calculate_available_shares,
    calculate_investor_available_shares,
)
from tests.factories import (
    create_portfolio, create_platform, create_trade,
    create_value_snapshot, create_manual_market_value,
    create_position_snapshot, create_investor_holding, create_subscription,
    create_investor, create_product, create_share_change_event,
)


SNAP_DATE = date(2025, 1, 6)  # 周一，交易日


def _seed_cash_baseline(db, portfolio_code="GV_P", platform_code="GV_PLAT", amount=6000):
    """构造基线：组合 + 平台 + 价值快照 + CASH 持仓快照 + 一笔 confirmed CASH buy（amount）"""
    create_portfolio(db, code=portfolio_code, status="active")
    create_platform(db, code=platform_code)
    create_value_snapshot(db, portfolio_code, SNAP_DATE,
                          total_value=amount, total_shares=amount, unit_price=1.0)
    create_position_snapshot(
        db, portfolio_code, "CASH", "", SNAP_DATE,
        cash_amount=amount, platform_code=platform_code,
    )
    create_trade(
        db, portfolio_code, "CASH", "",
        trade_type="buy", amount=amount, status="confirmed",
        trade_date=SNAP_DATE, confirm_date=SNAP_DATE,
        platform_code=platform_code,
    )


class TestGetCashValue:
    """get_cash_value 单元测试（迁移至 position_service 后）"""

    def test_without_override_returns_computed(self, test_db):
        """无 manual 覆盖时，返回 compute_cash_balance 结果"""
        _seed_cash_baseline(test_db)
        v = get_cash_value(test_db, "GV_P", "GV_PLAT", SNAP_DATE)
        assert v == Decimal("6000")

    def test_with_override_replaces_computed(self, test_db):
        """存在 manual 覆盖时，绝对替换计算值"""
        _seed_cash_baseline(test_db)
        create_manual_market_value(
            test_db, "GV_P", "GV_PLAT", "CASH",
            record_date=SNAP_DATE, market_value=6001.39,
        )
        v = get_cash_value(test_db, "GV_P", "GV_PLAT", SNAP_DATE)
        assert v == Decimal("6001.39")

    def test_override_non_matching_date_ignored(self, test_db):
        """覆盖日期 != target_date 时，不应用覆盖"""
        _seed_cash_baseline(test_db)
        create_manual_market_value(
            test_db, "GV_P", "GV_PLAT", "CASH",
            record_date=date(2025, 1, 10), market_value=9999,
        )
        v = get_cash_value(test_db, "GV_P", "GV_PLAT", SNAP_DATE)
        assert v == Decimal("6000")

    def test_target_date_none_skips_override(self, test_db):
        """target_date 为 None 时，跳过覆盖查询"""
        _seed_cash_baseline(test_db)
        create_manual_market_value(
            test_db, "GV_P", "GV_PLAT", "CASH",
            record_date=date.today(), market_value=9999,
        )
        v = get_cash_value(test_db, "GV_P", "GV_PLAT", None)
        assert v == Decimal("6000")


class TestCalculateAvailableCashWithOverride:
    """基线读快照表（#52）：manual 覆盖已 baked in 快照，实时计算自然继承"""

    def test_baseline_reads_from_snapshot(self, test_db):
        """基线直接读快照表 CASH 行的 cash_amount（快照已含 manual 覆盖）"""
        # 模拟快照生成时已 bake in manual 覆盖值 6001.39
        create_portfolio(test_db, code="GV_P", status="active")
        create_platform(test_db, code="GV_PLAT")
        create_value_snapshot(test_db, "GV_P", SNAP_DATE,
                              total_value=6001.39, total_shares=6001.39, unit_price=1.0)
        create_position_snapshot(
            test_db, "GV_P", "CASH", "", SNAP_DATE,
            cash_amount=6001.39, platform_code="GV_PLAT",
        )
        cash = calculate_available_cash(test_db, "GV_P", "GV_PLAT")
        assert cash == Decimal("6001.39")

    def test_override_plus_post_snapshot_trade(self, test_db):
        """快照基线（含覆盖）+ 快照后 confirmed trade"""
        create_portfolio(test_db, code="GV_P", status="active")
        create_platform(test_db, code="GV_PLAT")
        create_value_snapshot(test_db, "GV_P", SNAP_DATE,
                              total_value=6001.39, total_shares=6001.39, unit_price=1.0)
        create_position_snapshot(
            test_db, "GV_P", "CASH", "", SNAP_DATE,
            cash_amount=6001.39, platform_code="GV_PLAT",
        )
        # 快照后 confirmed CASH buy
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="buy", amount=100, status="confirmed",
            trade_date=date(2025, 1, 7), confirm_date=date(2025, 1, 7),
            platform_code="GV_PLAT",
        )
        cash = calculate_available_cash(test_db, "GV_P", "GV_PLAT")
        assert cash == Decimal("6101.39")

    def test_override_minus_pending_sell(self, test_db):
        """快照基线（含覆盖）- pending sell 预留"""
        create_portfolio(test_db, code="GV_P", status="active")
        create_platform(test_db, code="GV_PLAT")
        create_value_snapshot(test_db, "GV_P", SNAP_DATE,
                              total_value=6001.39, total_shares=6001.39, unit_price=1.0)
        create_position_snapshot(
            test_db, "GV_P", "CASH", "", SNAP_DATE,
            cash_amount=6001.39, platform_code="GV_PLAT",
        )
        # pending CASH sell（已承诺未执行，需预留）
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="sell", amount=500, status="pending",
            trade_date=date(2025, 1, 7),
            platform_code="GV_PLAT",
        )
        cash = calculate_available_cash(test_db, "GV_P", "GV_PLAT")
        assert cash == Decimal("5501.39")

    def test_no_snapshot_ignores_override(self, test_db):
        """无快照时，降级为全量流水计算，不应用 manual 覆盖"""
        create_portfolio(test_db, code="GV_NOSNAP", status="active")
        create_platform(test_db, code="GV_PLAT2")
        # 无快照时写入 manual 覆盖
        create_manual_market_value(
            test_db, "GV_NOSNAP", "GV_PLAT2", "CASH",
            record_date=date.today(), market_value=9999,
        )
        # 无 CASH trade，覆盖值 9999 不应生效
        cash = calculate_available_cash(test_db, "GV_NOSNAP", "GV_PLAT2")
        assert cash == Decimal("0")

    def test_manual_override_inherited_from_snapshot(self, test_db):
        """核心场景（#52）：历史 manual 覆盖已 baked in 快照，实时计算自然继承"""
        # 模拟：D-2 设置了 manual 覆盖 10000，D-1 快照继承了该值 + 增量 200 = 10200
        # 当前最新快照 = D-1，无 manual 记录在 D-1
        create_portfolio(test_db, code="GV_P", status="active")
        create_platform(test_db, code="GV_PLAT")
        create_value_snapshot(test_db, "GV_P", SNAP_DATE,
                              total_value=10200, total_shares=10200, unit_price=1.0)
        create_position_snapshot(
            test_db, "GV_P", "CASH", "", SNAP_DATE,
            cash_amount=10200, platform_code="GV_PLAT",
        )
        # 无 manual_market_value 记录在 SNAP_DATE
        # 流水只有一笔原始 buy 6000（全量重算会得 6000，但快照基线应为 10200）
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="buy", amount=6000, status="confirmed",
            trade_date=date(2025, 1, 2), confirm_date=date(2025, 1, 2),
            platform_code="GV_PLAT",
        )
        cash = calculate_available_cash(test_db, "GV_P", "GV_PLAT")
        # 应读快照基线 10200，而非全量流水 6000
        assert cash == Decimal("10200")


class TestNoSnapshotCashCountedOnce:
    """无快照降级路径每笔只计一次（#515）

    无快照时基线为 0，统一由增量段计算：流入按 confirm_date、流出（confirmed 与
    pending sell）按 trade_date、事件按 ex_date。旧实现先用 compute_cash_balance
    取全量已确认余额，随后又把 1970 哨兵交给同一增量段，同一批 confirmed 流水与
    事件现金流被累计两次。
    """

    PLAT = "NS_PLAT"

    def _seed(self, db, portfolio_code: str):
        create_portfolio(db, code=portfolio_code, status="active")
        create_platform(db, code=self.PLAT)

    def _cash_buy(self, db, portfolio_code, amount, *, trade_date, confirm_date):
        return create_trade(
            db, portfolio_code, "CASH", "",
            trade_type="buy", amount=amount, status="confirmed",
            trade_date=trade_date, confirm_date=confirm_date,
            platform_code=self.PLAT,
        )

    def _cash_sell(self, db, portfolio_code, amount, *, trade_date,
                   confirm_date=None, status="confirmed"):
        return create_trade(
            db, portfolio_code, "CASH", "",
            trade_type="sell", amount=amount, status=status,
            trade_date=trade_date, confirm_date=confirm_date,
            platform_code=self.PLAT,
        )

    def test_confirmed_buy_counted_once(self, test_db):
        """单笔 confirmed CASH buy 100 → 100（旧实现 200）"""
        self._seed(test_db, "NS_P1")
        self._cash_buy(test_db, "NS_P1", 100,
                       trade_date=date(2025, 1, 6), confirm_date=date(2025, 1, 6))
        assert calculate_available_cash(
            test_db, "NS_P1", self.PLAT
        ) == Decimal("100")

    def test_confirmed_inflow_minus_outflow(self, test_db):
        """confirmed buy 100 / confirmed sell 40 → 60（旧实现 120，计划 §1 反例）"""
        self._seed(test_db, "NS_P2")
        self._cash_buy(test_db, "NS_P2", 100,
                       trade_date=date(2025, 1, 6), confirm_date=date(2025, 1, 6))
        self._cash_sell(test_db, "NS_P2", 40,
                        trade_date=date(2025, 1, 7), confirm_date=date(2025, 1, 7))
        assert calculate_available_cash(
            test_db, "NS_P2", self.PLAT
        ) == Decimal("60")

    def test_event_cash_change_counted_once(self, test_db):
        """单笔 confirmed 事件 cash_change=+50 → 50（旧实现 100）"""
        self._seed(test_db, "NS_P3")
        create_product(test_db, code="NS_EV", market="CN_OTC")
        create_share_change_event(
            test_db, "NS_P3", "NS_EV", "CN_OTC",
            event_type="cash_dividend",
            ex_date=date(2025, 1, 10), entitlement_date=date(2025, 1, 9),
            status="confirmed", platform_code=self.PLAT,
            cash_change=Decimal("50"),
        )
        assert calculate_available_cash(
            test_db, "NS_P3", self.PLAT
        ) == Decimal("50")

    def test_pending_sell_counted_once(self, test_db):
        """confirmed buy 100 / pending sell 40 → 60（pending 本就不在基线，只扣一次）"""
        self._seed(test_db, "NS_P4")
        self._cash_buy(test_db, "NS_P4", 100,
                       trade_date=date(2025, 1, 6), confirm_date=date(2025, 1, 6))
        self._cash_sell(test_db, "NS_P4", 40,
                        trade_date=date(2025, 1, 7), status="pending")
        assert calculate_available_cash(
            test_db, "NS_P4", self.PLAT
        ) == Decimal("60")

    def test_future_arrival_excluded_by_as_of(self, test_db):
        """as_of=T 时 confirm_date > T 的 confirmed buy 不计入（未来到账排除）"""
        self._seed(test_db, "NS_P5")
        self._cash_buy(test_db, "NS_P5", 30,
                       trade_date=date(2025, 1, 6), confirm_date=date(2025, 1, 7))
        self._cash_buy(test_db, "NS_P5", 100,
                       trade_date=date(2025, 1, 17), confirm_date=date(2025, 1, 20))
        assert calculate_available_cash(
            test_db, "NS_P5", self.PLAT, as_of_date=date(2025, 1, 10)
        ) == Decimal("30")

    def test_confirmed_sell_anchored_on_trade_date(self, test_db):
        """as_of=T：confirmed sell 按 trade_date 扣减（trade_date <= T < confirm_date 仍扣）"""
        self._seed(test_db, "NS_P6")
        self._cash_buy(test_db, "NS_P6", 100,
                       trade_date=date(2025, 1, 6), confirm_date=date(2025, 1, 6))
        self._cash_sell(test_db, "NS_P6", 40,
                        trade_date=date(2025, 1, 7), confirm_date=date(2025, 1, 20))
        assert calculate_available_cash(
            test_db, "NS_P6", self.PLAT, as_of_date=date(2025, 1, 10)
        ) == Decimal("60")

    def test_confirmed_sell_trade_date_after_as_of_excluded(self, test_db):
        """as_of=T：confirmed sell 的 trade_date > T 时不计提

        出账锚定 trade_date（#70/#78）。这同时是无快照路径「只计一次」相对旧值
        去重的唯一刻意差异点：旧基线按 confirm_date 收口，会在 T 日就扣掉这笔
        trade_date 尚未到来的卖单；新口径不扣，到 trade_date 才扣。
        """
        self._seed(test_db, "NS_P7")
        self._cash_buy(test_db, "NS_P7", 100,
                       trade_date=date(2025, 1, 6), confirm_date=date(2025, 1, 6))
        # 排序反转：confirm_date(1-7) < trade_date(1-15)，可经 cash_confirm_date 构造
        self._cash_sell(test_db, "NS_P7", 40,
                        trade_date=date(2025, 1, 15), confirm_date=date(2025, 1, 7))
        # as_of 早于 trade_date：承诺尚未发生，不扣
        assert calculate_available_cash(
            test_db, "NS_P7", self.PLAT, as_of_date=date(2025, 1, 10)
        ) == Decimal("100")
        # trade_date 到达后照常扣减
        assert calculate_available_cash(
            test_db, "NS_P7", self.PLAT, as_of_date=date(2025, 1, 16)
        ) == Decimal("60")


class TestCalculateAvailableCashAsOfDate:
    """calculate_available_cash 的 as_of_date 截止计算（#23）"""

    def test_as_of_date_baseline_le(self, test_db):
        """as_of_date 传入历史日时，基线取 <= as_of_date 的最新快照日"""
        _seed_cash_baseline(test_db)  # SNAP_DATE(2025-01-06) 现金 6000
        # 造一个更晚的快照（as_of_date 之后），不应被采用为基线
        create_value_snapshot(
            test_db, "GV_P", date(2025, 1, 13),
            total_value=20000, total_shares=20000, unit_price=1.0,
        )
        create_position_snapshot(
            test_db, "GV_P", "CASH", "", date(2025, 1, 13),
            cash_amount=20000, platform_code="GV_PLAT",
        )
        cash = calculate_available_cash(
            test_db, "GV_P", "GV_PLAT", as_of_date=date(2025, 1, 10)
        )
        # 基线落在 2025-01-06（<= as_of_date），无后续快照内 trade，应为 6000
        assert cash == Decimal("6000")

    def test_as_of_date_after_range_inclusive(self, test_db):
        """as_of_date 截止：快照后 confirmed trade 在 (latest, as_of] 内计入"""
        _seed_cash_baseline(test_db)
        # SNAP_DATE 之后、as_of 之前的 confirmed buy 应计入
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="buy", amount=300, status="confirmed",
            trade_date=date(2025, 1, 7), confirm_date=date(2025, 1, 8),
            platform_code="GV_PLAT",
        )
        # as_of 之后的 confirmed buy 不应计入
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="buy", amount=999, status="confirmed",
            trade_date=date(2025, 1, 13), confirm_date=date(2025, 1, 14),
            platform_code="GV_PLAT",
        )
        cash = calculate_available_cash(
            test_db, "GV_P", "GV_PLAT", as_of_date=date(2025, 1, 10)
        )
        assert cash == Decimal("6300")

    def test_as_of_date_pending_sell_after_as_of_excluded(self, test_db):
        """新口径（#70/#78）：pending sell 仅在 trade_date <= as_of_date 时计提"""
        _seed_cash_baseline(test_db)
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="sell", amount=500, status="pending",
            trade_date=date(2025, 1, 20),  # 下单日在 as_of 之后，尚未承诺，不计提
            platform_code="GV_PLAT",
        )
        cash = calculate_available_cash(
            test_db, "GV_P", "GV_PLAT", as_of_date=date(2025, 1, 10)
        )
        assert cash == Decimal("6000")

    def test_as_of_date_pending_sell_on_or_before_as_of_deducted(self, test_db):
        """新口径（#70/#78）：pending sell 的 trade_date <= as_of_date 时正常计提"""
        _seed_cash_baseline(test_db)
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="sell", amount=500, status="pending",
            trade_date=date(2025, 1, 8),
            platform_code="GV_PLAT",
        )
        cash = calculate_available_cash(
            test_db, "GV_P", "GV_PLAT", as_of_date=date(2025, 1, 10)
        )
        assert cash == Decimal("5500")


class TestAvailableCashTradeDateAnchor:
    """可用现金时点口径（#70/#78）：流出锚定 trade_date，流入锚定 confirm_date"""

    def test_confirmed_sell_trade_date_within_as_of_deducted(self, test_db):
        """confirmed sell：trade_date <= as_of < confirm_date 仍扣减（旧口径会隐身）"""
        _seed_cash_baseline(test_db)
        # 下单 1-7、确认 1-13：as_of=1-10 时旧口径（按 confirm_date）不扣，新口径必扣
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="sell", amount=2000, status="confirmed",
            trade_date=date(2025, 1, 7), confirm_date=date(2025, 1, 13),
            platform_code="GV_PLAT",
        )
        cash = calculate_available_cash(
            test_db, "GV_P", "GV_PLAT", as_of_date=date(2025, 1, 10)
        )
        assert cash == Decimal("4000")

    def test_confirmed_sell_trade_date_after_as_of_excluded(self, test_db):
        """confirmed sell：trade_date > as_of 时不扣减（承诺尚未发生）"""
        _seed_cash_baseline(test_db)
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="sell", amount=2000, status="confirmed",
            trade_date=date(2025, 1, 13), confirm_date=date(2025, 1, 14),
            platform_code="GV_PLAT",
        )
        cash = calculate_available_cash(
            test_db, "GV_P", "GV_PLAT", as_of_date=date(2025, 1, 10)
        )
        assert cash == Decimal("6000")

    def test_spec_scenario_t_plus_1(self, test_db):
        """事故口径复刻：T 日 4000，T+n 确认入金 2000，T+m(m>n) 下单卖出 2000，
        as_of=T+1 时：入金未确认不计、卖出未下单不扣 → 4000"""
        _seed_cash_baseline(test_db, amount=4000)  # T = 2025-01-06
        # T+3 确认的入金（在途 confirmed CASH buy，confirm_date 未到不计入）
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="buy", amount=2000, status="confirmed",
            trade_date=date(2025, 1, 8), confirm_date=date(2025, 1, 9),
            platform_code="GV_PLAT",
        )
        # T+4 下单的卖出（trade_date 晚于 as_of，不扣）
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="sell", amount=2000, status="confirmed",
            trade_date=date(2025, 1, 10), confirm_date=date(2025, 1, 13),
            platform_code="GV_PLAT",
        )
        cash = calculate_available_cash(
            test_db, "GV_P", "GV_PLAT", as_of_date=date(2025, 1, 7)
        )
        assert cash == Decimal("4000")

    def test_as_of_none_equivalent_to_legacy(self, test_db):
        """as_of_date=None 时选取集合与旧实现完全一致（不设上限）"""
        _seed_cash_baseline(test_db)  # 基线 6000
        # 快照后 confirmed buy +300
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="buy", amount=300, status="confirmed",
            trade_date=date(2025, 1, 13), confirm_date=date(2025, 1, 14),
            platform_code="GV_PLAT",
        )
        # 快照后 confirmed sell −200（无上限，无论 trade_date/confirm_date 多晚）
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="sell", amount=200, status="confirmed",
            trade_date=date(2025, 2, 10), confirm_date=date(2025, 2, 11),
            platform_code="GV_PLAT",
        )
        # pending sell −500（远期下单仍全额计提）
        create_trade(
            test_db, "GV_P", "CASH", "",
            trade_type="sell", amount=500, status="pending",
            trade_date=date(2025, 3, 3),
            platform_code="GV_PLAT",
        )
        cash = calculate_available_cash(test_db, "GV_P", "GV_PLAT")
        assert cash == Decimal("5600")


class TestCalculateAvailableSharesAsOfDate:
    """calculate_available_shares 的 as_of_date 截止计算（#23）"""

    def test_as_of_date_latest_position_le(self, test_db):
        """as_of_date 截止：最新持仓取 <= as_of_date 的快照"""
        create_portfolio(test_db, code="SHR_P", status="active")
        create_platform(test_db, code="SHR_PLAT")
        create_product(test_db, code="FUND1", market="CN_OTC")
        # 1-6 快照 1000 份
        create_position_snapshot(
            test_db, "SHR_P", "FUND1", "CN_OTC", date(2025, 1, 6),
            shares=1000, platform_code="SHR_PLAT",
        )
        # 1-13 快照 800 份（as_of 之后，不应采用）
        create_position_snapshot(
            test_db, "SHR_P", "FUND1", "CN_OTC", date(2025, 1, 13),
            shares=800, platform_code="SHR_PLAT",
        )
        # as_of=1-10：基线取 1-6 的 1000
        shares = calculate_available_shares(
            test_db, "SHR_P", "FUND1", "CN_OTC", as_of_date=date(2025, 1, 10)
        )
        assert shares == Decimal("1000")

    def test_as_of_date_confirmed_sell_in_range(self, test_db):
        """as_of_date 截止：confirmed sell 在 (latest, as_of] 内扣减"""
        create_portfolio(test_db, code="SHR_P2", status="active")
        create_platform(test_db, code="SHR_PLAT2")
        create_product(test_db, code="FUND1", market="CN_OTC")
        create_value_snapshot(
            test_db, "SHR_P2", date(2025, 1, 6),
            total_value=1000, total_shares=1000, unit_price=1.0,
        )
        create_position_snapshot(
            test_db, "SHR_P2", "FUND1", "CN_OTC", date(2025, 1, 6),
            shares=1000, platform_code="SHR_PLAT2",
        )
        # 快照后、as_of 内 confirmed sell 100
        create_trade(
            test_db, "SHR_P2", "FUND1", "CN_OTC",
            trade_type="sell", shares=100, status="confirmed",
            trade_date=date(2025, 1, 7), confirm_date=date(2025, 1, 8),
            platform_code="SHR_PLAT2",
        )
        # as_of 之后的 confirmed sell 50 不扣减
        create_trade(
            test_db, "SHR_P2", "FUND1", "CN_OTC",
            trade_type="sell", shares=50, status="confirmed",
            trade_date=date(2025, 1, 13), confirm_date=date(2025, 1, 14),
            platform_code="SHR_PLAT2",
        )
        shares = calculate_available_shares(
            test_db, "SHR_P2", "FUND1", "CN_OTC", as_of_date=date(2025, 1, 10)
        )
        assert shares == Decimal("900")


class TestCalculateInvestorAvailableSharesAsOfDate:
    """calculate_investor_available_shares 的 as_of_date 截止计算（#23）"""

    def test_as_of_date_latest_holding_le(self, test_db):
        """as_of_date 截止：最新投资人份额取 <= as_of_date 的快照"""
        create_portfolio(test_db, code="INV_P", status="active")
        create_investor(test_db, code="INV_I")
        create_investor_holding(test_db, "INV_P", "INV_I", date(2025, 1, 6), shares=500)
        create_investor_holding(test_db, "INV_P", "INV_I", date(2025, 1, 13), shares=300)
        shares = calculate_investor_available_shares(
            test_db, "INV_P", "INV_I", as_of_date=date(2025, 1, 10)
        )
        assert shares == Decimal("500")

    def test_as_of_date_confirmed_redeem_in_range(self, test_db):
        """as_of_date 截止：confirmed redeem 在 (latest, as_of] 内扣减"""
        create_portfolio(test_db, code="INV_P2", status="active")
        create_investor(test_db, code="INV_I2")
        create_platform(test_db, code="MYCF")
        create_value_snapshot(
            test_db, "INV_P2", date(2025, 1, 6),
            total_value=500, total_shares=500, unit_price=1.0,
        )
        create_investor_holding(test_db, "INV_P2", "INV_I2", date(2025, 1, 6), shares=500)
        create_subscription(
            test_db, "INV_P2", "INV_I2", sub_type="redeem",
            shares=100, apply_date=date(2025, 1, 7),
            confirm_date=date(2025, 1, 8), status="confirmed",
        )
        create_subscription(
            test_db, "INV_P2", "INV_I2", sub_type="redeem",
            shares=50, apply_date=date(2025, 1, 13),
            confirm_date=date(2025, 1, 14), status="confirmed",
        )
        shares = calculate_investor_available_shares(
            test_db, "INV_P2", "INV_I2", as_of_date=date(2025, 1, 10)
        )
        assert shares == Decimal("400")


class TestCalculateAvailableSharesEventWindow:
    """calculate_available_shares 的快照后事件增量口径（#277）：
    只计平台级行、只计负向、窗口 (最新快照日, as_of]"""

    SNAP = date(2025, 1, 6)      # 基线快照日（周一）
    EX = date(2025, 1, 8)        # 事件除息日（窗口内）

    def _seed(self, db, port_code: str, plat: str, shares: float = 1000.0):
        create_portfolio(db, code=port_code, status="active")
        create_platform(db, code=plat)
        create_product(db, code="EWF", market="CN_OTC")
        create_value_snapshot(
            db, port_code, self.SNAP,
            total_value=shares, total_shares=shares, unit_price=1.0,
        )
        create_position_snapshot(
            db, port_code, "EWF", "CN_OTC", self.SNAP,
            shares=shares, platform_code=plat,
        )

    def _event(self, db, port_code: str, *, shares_change, status="confirmed",
               platform_code="EWF_PLAT", market="CN_OTC", ex_date=None,
               event_type="forced_adjustment", parent_event_id=None):
        from tests.factories import create_share_change_event
        return create_share_change_event(
            db, port_code, "EWF", market,
            event_type=event_type,
            ex_date=ex_date or self.EX,
            entitlement_date=date(2025, 1, 3),
            status=status, platform_code=platform_code,
            shares_change=shares_change, parent_event_id=parent_event_id,
        )

    def test_negative_event_deducted(self, test_db):
        """负向事件确认后、入快照前：可用份额扣减变动量"""
        self._seed(test_db, "EWF_P1", "EWF_PLAT")
        self._event(test_db, "EWF_P1", shares_change=Decimal("-100.00"))
        shares = calculate_available_shares(test_db, "EWF_P1", "EWF", "CN_OTC")
        assert shares == Decimal("900.00")

    def test_positive_event_not_counted(self, test_db):
        """正向事件窗口内保守低估：不加回（防撤销后事实超卖）"""
        self._seed(test_db, "EWF_P2", "EWF_PLAT")
        self._event(test_db, "EWF_P2", shares_change=Decimal("100.00"))
        shares = calculate_available_shares(test_db, "EWF_P2", "EWF", "CN_OTC")
        assert shares == Decimal("1000.00")

    def test_parent_child_no_double_count(self, test_db):
        """基金级父记录（持汇总值）不计入，只计平台级子记录，防父子双计"""
        self._seed(test_db, "EWF_P3", "EWF_PLAT")
        create_platform(test_db, code="EWF_PLAT2")
        father = self._event(test_db, "EWF_P3", shares_change=Decimal("-200.00"),
                             platform_code=None, event_type="share_merge")
        self._event(test_db, "EWF_P3", shares_change=Decimal("-150.00"),
                    parent_event_id=father.id, event_type="share_merge")
        self._event(test_db, "EWF_P3", shares_change=Decimal("-50.00"),
                    platform_code="EWF_PLAT2", parent_event_id=father.id,
                    event_type="share_merge")
        shares = calculate_available_shares(test_db, "EWF_P3", "EWF", "CN_OTC")
        # 1000 − 150 − 50 = 800（父记录 −200 不双计）
        assert shares == Decimal("800.00")

    def test_market_filter(self, test_db):
        """传 market 时只计同 market 事件"""
        self._seed(test_db, "EWF_P4", "EWF_PLAT")
        create_product(test_db, code="EWF", market="SZ")  # 事件复合外键要求产品存在
        self._event(test_db, "EWF_P4", shares_change=Decimal("-100.00"), market="SZ")
        assert calculate_available_shares(
            test_db, "EWF_P4", "EWF", "CN_OTC") == Decimal("1000.00")
        assert calculate_available_shares(
            test_db, "EWF_P4", "EWF") == Decimal("900.00")

    def test_as_of_date_upper_bound(self, test_db):
        """as_of_date 截止：ex_date > as_of 的事件不扣"""
        self._seed(test_db, "EWF_P5", "EWF_PLAT")
        self._event(test_db, "EWF_P5", shares_change=Decimal("-100.00"),
                    ex_date=date(2025, 1, 15))
        assert calculate_available_shares(
            test_db, "EWF_P5", "EWF", "CN_OTC",
            as_of_date=date(2025, 1, 10)) == Decimal("1000.00")
        assert calculate_available_shares(
            test_db, "EWF_P5", "EWF", "CN_OTC",
            as_of_date=date(2025, 1, 16)) == Decimal("900.00")

    def test_event_within_snapshot_not_counted(self, test_db):
        """ex_date <= 最新快照日的事件已入基线，不重复扣减"""
        self._seed(test_db, "EWF_P6", "EWF_PLAT")
        self._event(test_db, "EWF_P6", shares_change=Decimal("-100.00"),
                    ex_date=self.SNAP)
        shares = calculate_available_shares(test_db, "EWF_P6", "EWF", "CN_OTC")
        assert shares == Decimal("1000.00")

    def test_no_snapshot_counts_all(self, test_db):
        """无快照基线时全部负向事件计入（等价现金侧 1970 哨兵）"""
        create_portfolio(test_db, code="EWF_P7", status="active")
        create_platform(test_db, code="EWF_PLAT")
        create_product(test_db, code="EWF", market="CN_OTC")
        self._event(test_db, "EWF_P7", shares_change=Decimal("-30.00"))
        shares = calculate_available_shares(test_db, "EWF_P7", "EWF", "CN_OTC")
        assert shares == Decimal("-30.00")

    def test_pending_and_zero_events_ignored(self, test_db):
        """pending 事件与 shares_change=0（如现金分红）不影响可用份额"""
        self._seed(test_db, "EWF_P8", "EWF_PLAT")
        self._event(test_db, "EWF_P8", shares_change=Decimal("-100.00"), status="pending")
        self._event(test_db, "EWF_P8", shares_change=Decimal("0"),
                    event_type="cash_dividend")
        shares = calculate_available_shares(test_db, "EWF_P8", "EWF", "CN_OTC")
        assert shares == Decimal("1000.00")
