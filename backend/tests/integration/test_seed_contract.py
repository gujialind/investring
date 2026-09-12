# ============================================================================
# 集成测试：种子契约 (test_seed_contract.py)
# ============================================================================
# 锁死 seed_base.py 中 E2E_ACTIVE / E2E_PORT 的数据形态（issue #354）：
# 前端 E2E spec 经 e2e/helpers.ts 按 code 直达组合并对种子契约做硬断言
# （缺数据不再优雅 skip），种子无声退化必须先在本层红，而不是在 E2E 层
# 表现为静默 skip 或莫名失败。纯查询断言，不写任何业务数据。
#
# 注意：E2E_ACTIVE 只在 E2E 消费方种子（seed_e2e_active），不进 pytest session
# 种子（避免业务交易数据泄漏进按全局账本精确断言的存量测试）。故本文件用
# seeded_active_db fixture 在 function 事务内现造 E2E_ACTIVE 再断言。
# ============================================================================

from datetime import date, timedelta

import pytest
from sqlalchemy import func

from app.models import (
    InvestorHolding, Portfolio, PortfolioPosition, PortfolioValueSnapshot,
    PriceRecord, Subscription, Trade, TradingCalendar,
)
from app.services.trading_utils import get_next_trading_day
from tests import seed_base
from tests.seed_base import seed_e2e_active


@pytest.fixture
def seeded_active_db(test_db):
    """在 function 级事务内于基础种子之上造 E2E_ACTIVE（与 E2E 消费方同一实现）。"""
    seed_e2e_active(test_db)
    return test_db


class TestE2EPortContract:
    """E2E_PORT 契约：draft、零交易/申赎/快照（多个存量 spec 依赖此空态）"""

    def test_stays_draft_and_empty(self, test_db):
        port = test_db.query(Portfolio).filter(Portfolio.code == "E2E_PORT").first()
        assert port is not None, "种子必须包含 E2E_PORT"
        assert port.status == "draft"
        assert test_db.query(Trade).filter(
            Trade.portfolio_code == "E2E_PORT").count() == 0
        assert test_db.query(Subscription).filter(
            Subscription.portfolio_code == "E2E_PORT").count() == 0
        assert test_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == "E2E_PORT").count() == 0


class TestE2EActiveContract:
    """E2E_ACTIVE 契约：active + 首购 + 已确认场内交易 + 2 日快照 + 1 pending 交易"""

    def test_portfolio_active_with_started_at(self, seeded_active_db):
        active = seeded_active_db.query(Portfolio).filter(
            Portfolio.code == "E2E_ACTIVE").first()
        assert active is not None, "seed_e2e_active 必须造出 E2E_ACTIVE"
        assert active.status == "active"
        assert active.started_at is not None

    def test_first_subscription_confirmed_at_initial_nav(self, seeded_active_db):
        subs = seeded_active_db.query(Subscription).filter(
            Subscription.portfolio_code == "E2E_ACTIVE").all()
        assert len(subs) == 1
        sub = subs[0]
        assert sub.sub_type == "subscribe"
        assert sub.status == "confirmed"
        # 首窗净值固定 1.0000（份额 == 金额）
        assert float(sub.unit_price) == 1.0
        assert float(sub.shares) == float(sub.amount)

    def test_confirmed_exchange_trade_exists(self, seeded_active_db):
        confirmed = seeded_active_db.query(Trade).filter(
            Trade.portfolio_code == "E2E_ACTIVE",
            Trade.product_code == "510300.SH",
            Trade.market == "CN_EXCHANGE",
            Trade.trade_type == "buy",
            Trade.status == "confirmed",
        ).all()
        assert len(confirmed) == 1
        # 配对 CASH 腿必须同为 confirmed（验证 sync_transfer_group 生效，即
        # create_trade 后 db.flush() 的修复：id=None 时 CASH 腿会滞留 pending）
        cash_confirmed = seeded_active_db.query(Trade).filter(
            Trade.portfolio_code == "E2E_ACTIVE",
            Trade.product_code == "CASH",
            Trade.transfer_group == confirmed[0].transfer_group,
            Trade.status == "confirmed",
        ).count()
        assert cash_confirmed == 1

    def test_pending_trade_editable_window(self, seeded_active_db):
        """pending 基金腿交易：编辑类 E2E 用例的硬依赖。

        trade_date 须晚于最新快照日（领域约束）且落在前端交易列表
        默认「近1年」过滤窗内（#126），否则编辑按钮永不出现。
        """
        pending = seeded_active_db.query(Trade).filter(
            Trade.portfolio_code == "E2E_ACTIVE",
            Trade.status == "pending",
            Trade.product_code != "CASH",
        ).all()
        assert len(pending) >= 1, "种子须保留 >=1 笔 pending 交易供编辑用例"
        latest_snapshot = seeded_active_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == "E2E_ACTIVE",
        ).order_by(PortfolioValueSnapshot.snapshot_date.desc()).first()
        assert latest_snapshot is not None
        for t in pending:
            assert t.trade_date > latest_snapshot.snapshot_date
            assert t.trade_date >= date.today() - timedelta(days=365), (
                f"pending 交易 {t.trade_date} 已滑出前端「近1年」默认过滤窗"
            )

    def test_two_consecutive_snapshots_with_rows(self, seeded_active_db):
        snaps = seeded_active_db.query(PortfolioValueSnapshot).filter(
            PortfolioValueSnapshot.portfolio_code == "E2E_ACTIVE",
        ).order_by(PortfolioValueSnapshot.snapshot_date).all()
        assert len(snaps) == 2
        # 连续原则：第二张 == 第一张的次一交易日
        assert get_next_trading_day(seeded_active_db, snaps[0].snapshot_date) == snaps[1].snapshot_date
        for s in snaps:
            assert seeded_active_db.query(PortfolioPosition).filter(
                PortfolioPosition.portfolio_code == "E2E_ACTIVE",
                PortfolioPosition.snapshot_date == s.snapshot_date,
            ).count() >= 1
            assert seeded_active_db.query(InvestorHolding).filter(
                InvestorHolding.portfolio_code == "E2E_ACTIVE",
                InvestorHolding.snapshot_date == s.snapshot_date,
            ).count() >= 1
            assert float(s.unit_price) > 0

    def test_price_rows_cover_snapshot_dates(self, seeded_active_db):
        """快照严格取价（nav_lag_days=0 → 当日价）：每个快照日必须有价格行"""
        snap_dates = [
            row[0] for row in seeded_active_db.query(
                PortfolioValueSnapshot.snapshot_date).filter(
                PortfolioValueSnapshot.portfolio_code == "E2E_ACTIVE").all()
        ]
        for d in snap_dates:
            assert seeded_active_db.query(PriceRecord).filter(
                PriceRecord.product_code == "510300.SH",
                PriceRecord.market == "CN_EXCHANGE",
                PriceRecord.price_date == d,
            ).first() is not None, f"快照日 {d} 缺 510300.SH 价格行"


class TestCalendarContract:
    """交易日历契约（issue #468）：起点固定、终点随 today 滚动，消除日期时间炸弹"""

    def test_calendar_end_covers_one_year_ahead(self, test_db):
        """seed_base 段 4 的终点 = today + 365 天；若恰为周末则最后落库日为其前一个工作日"""
        max_date = test_db.query(func.max(TradingCalendar.calendar_date)).scalar()
        assert max_date is not None, "种子必须包含交易日历"
        assert max_date >= date.today() + timedelta(days=360), (
            f"日历终点 {max_date} 未覆盖 today + 1 年，滚动终点失效"
        )

    def test_seed_rolls_into_future_year(self, test_db, monkeypatch):
        """模拟 2027 场景（#468 首爆点 2027-01-04）：冻结 today 后日历延伸、种子可用。

        通过 monkeypatch seed_base 模块级 date 冻结 today；写入发生在 function 级
        事务内、teardown 整体回滚，不会污染 session 日历与哨兵测试。
        """

        class _FrozenDate(date):
            @classmethod
            def today(cls):
                return date(2027, 1, 4)  # 2027 年首个交易日（周一）

        monkeypatch.setattr(seed_base, "date", _FrozenDate)
        seed_base.seed_base_data(test_db)
        seed_base.seed_e2e_active(test_db)

        max_date = test_db.query(func.max(TradingCalendar.calendar_date)).scalar()
        assert max_date >= date(2028, 1, 4), f"冻结到 2027 后日历终点 {max_date} 未滚动"

        pending = test_db.query(Trade).filter(
            Trade.portfolio_code == "E2E_ACTIVE",
            Trade.status == "pending",
            Trade.product_code != "CASH",
        ).all()
        assert len(pending) == 1, "冻结到 2027 后 E2E_ACTIVE 种子须照常产出 pending 交易"
        # D4 = 冻结当日（交易日）；create_trade 的交易日校验通过即证明日历已覆盖 2027
        assert pending[0].trade_date == date(2027, 1, 4)
