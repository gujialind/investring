# ============================================================================
# 日历耗尽写路径测试 helper (calendar_exhaustion_helpers.py)
# ============================================================================
# 溯源：issue #591——交易日 helper 改为日历不足即抛 CALENDAR_NOT_SYNCED 后，
#       各写入口（申赎 / 调仓 / 现金转移）需要「拒绝 + 零业务残留」的回归用例。
# 现状：供 test_subscriptions_calendar_exhaustion / test_trades_calendar_exhaustion /
#       test_cash_transfers_calendar_exhaustion / test_trading_day 共用（≥2 文件，
#       按 backend/AGENTS.md §2 helper 规则提取到 tests/integration/）。
#       日历改动全部在 test_db 的 SAVEPOINT 事务内，用例结束自动复原 session 级种子；
#       不使用 factories.ensure_trading_day（它会 commit、逃逸隔离）。
# ============================================================================
from datetime import date

from app.models import ShareChangeEvent, Subscription, Trade, TradingCalendar


def make_last_open_day(db, last_day: date) -> date:
    """把 last_day 变成交易日历里**最后一个**开市日（其后不再有任何行）。

    于是 `is_trading_day(last_day)` 仍为真，而 `get_next_trading_day(last_day, days=1)`
    覆盖不足 → 抛 CALENDAR_NOT_SYNCED。这正是「日历滚动耗尽」的受控形态。
    """
    row = db.query(TradingCalendar).filter_by(calendar_date=last_day).first()
    if row is None:
        db.add(TradingCalendar(calendar_date=last_day, is_open=True, exchange="SSE"))
    else:
        row.is_open = True
    db.query(TradingCalendar).filter(
        TradingCalendar.calendar_date > last_day
    ).delete(synchronize_session=False)
    db.flush()
    return last_day


def business_rows(db, portfolio_code):
    """组合名下全部业务行的快照（用于「拒绝即零残留」差集断言）。"""
    return {
        model.__tablename__: db.query(*model.__table__.columns).filter(
            model.portfolio_code == portfolio_code,
        ).order_by(model.id).all()
        for model in (Subscription, Trade, ShareChangeEvent)
    }


def assert_rejected_without_residual(
    request, url, payload, headers, db, portfolio_code, error_code,
):
    """请求被以 error_code 拒绝，且组合名下业务行**逐字段不变**（零残留）。

    沿用 test_trading_day._assert_closed_date_rejected 的形状，只把错误码参数化。
    """
    before = business_rows(db, portfolio_code)
    resp = request(url, json=payload, headers=headers)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == error_code, detail
    db.flush()
    assert business_rows(db, portfolio_code) == before, (
        f"{error_code} 拒绝后仍有业务行残留：{url}"
    )
    return resp
