"""现金流水聚合读侧服务（issue #725）。

把四类现金来源合并成「一行 = 某平台现金账本的一笔变动」的只读视图：

1. 调仓 / 申赎的配对 CASH 腿（`trade`，`product_code="CASH"`）；
2. 平台间现金转移的两条 CASH 腿（同表，按腿拆两行）；
3. 平台级现金事件（`share_change_event.cash_change`，白名单
   `CASH_EFFECT_EVENT_TYPES`；基金级父记录不出行，否则与其平台级子记录双计）；
4. 手动现金重估（`manual_market_value`，`product_code="CASH"`）——**绝对替换**语义，
   金额是绝对值不是增量，不参与任何求和。

口径正文见 docs/reference/business-constraints.md#rule-cash 的「现金流水读侧口径」：
锚点日（卖出锚 `trade_date`、买入锚 `confirm_date`，与
`position_service.calculate_available_cash` 逐字同源）、已生效/在途判据、
流入流出的两级排除规则。本服务只读、不量化、不落库、不 commit。
"""
from collections import Counter
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.constants.share_change_events import CASH_EFFECT_EVENT_TYPES
from app.models.investor import Investor
from app.models.manual_market_value import ManualMarketValue
from app.models.platform import Platform
from app.models.portfolio import Portfolio
from app.models.portfolio_position import PortfolioPosition
from app.models.share_change_event import ShareChangeEvent
from app.models.subscription import Subscription
from app.models.trade import Trade
from app.services.exceptions import BusinessError, NotFoundError
from app.services.position_service import (
    _classify_transfer_group,
    calculate_available_cash,
)
from app.services.product_service import build_product_label_maps
from app.services.trading_utils import (
    get_latest_snapshot_date,
    try_get_prev_trading_day,
)

# 流水类型取值域（= 前端筛选下拉，与设计文档 §3 契约草图同源）。
# label 为账本口径文案：申购的 CASH 腿是 buy（投资人注资入账 → 现金增加）、
# 赎回的 CASH 腿是 sell（组合向投资人支付 → 现金减少），与 issue #725 取舍点 T1 一致。
FLOW_TYPE_LABELS = {
    "rebal_buy": "调仓 · 买入扣款",
    "rebal_sell": "调仓 · 卖出到账",
    "sub_buy": "申购 · 注资入账",
    "redeem": "赎回 · 支付",
    "dividend": "现金分红",
    "transfer": "平台间转移",
    "revalue": "现金重估",
}
FLOW_TYPES = tuple(FLOW_TYPE_LABELS)

# status 筛选取值域：映射到派生的 settled 两值，不透出 raw `trade.status`
# （confirmed + 未来到账日在余额语义上仍是「在途」，raw 值会误导）。
STATUS_SETTLED = "settled"
STATUS_IN_TRANSIT = "in_transit"
STATUS_VALUES = (STATUS_SETTLED, STATUS_IN_TRANSIT)

MAX_PAGE_SIZE = 100

# 同日多来源的排序次序：重估排最后——它是**日终绝对值**，吸收当日增量
# （与快照/前端引擎口径一致）。
_SOURCE_ORDER = {"trade_leg": 0, "transfer": 1, "dividend": 2, "revalue": 3}

_ZERO = Decimal("0")


def _dec(value) -> Decimal:
    return Decimal(str(value or 0))


def _signed(amount: Decimal, is_buy: bool) -> Decimal:
    """DB `amount` 恒正、方向由 `trade_type` 表达 → 响应带符号（契约调整 C1）。"""
    return amount if is_buy else -amount


def _status_of(flow_type: str, direction: str, settled: bool, note: Optional[str]) -> dict:
    """行状态派生（variant/label/note）。direction: in | out | neutral。"""
    if flow_type == "revalue":
        return {
            "variant": "neutral" if settled else "warning",
            "label": "已生效" if settled else "未生效",
            "note": note,
        }
    if not settled:
        labels = {
            "rebal_buy": "待扣款",
            "rebal_sell": "现金待到账",
            "sub_buy": "待确认",
            "redeem": "待支付",
            "dividend": "在途",
            "transfer": "待到账" if direction == "in" else "待扣款",
        }
        return {"variant": "warning", "label": labels[flow_type], "note": note}
    labels = {
        "rebal_buy": "已扣款",
        "rebal_sell": "已到账",
        "sub_buy": "已入账",
        "redeem": "已支付",
        "dividend": "已到账",
        "transfer": "已到账" if direction == "in" else "已扣款",
    }
    return {"variant": "success", "label": labels[flow_type], "note": note}


# ---------------------------------------------------------------------------
# 行集构建
# ---------------------------------------------------------------------------


def _trade_leg_rows(db: Session, portfolio_code: str, today: date) -> tuple[list, dict]:
    """CASH 腿 → 流水行；返回 (rows, leg_index)。

    `leg_index` = {(transfer_group, trade_type): Trade}，供转移行取对方腿
    （两腿同为 CASH 腿，无需二次查询）。

    - cancelled 腿一律排除：回退不是现金流水（与快照在途口径一致）。
    - `platform_code IS NULL` 的 CASH 腿不出行：现金按平台分账（rule-cash），
      无平台归属的行既无法算平台日终余额、也无法归入构成弹窗任一行。
      现有写入路径（申赎确认 / #493 配对腿 / 现金转移）均强制平台，故为防守而非活口。
    """
    legs = (
        db.query(Trade)
        .filter(
            Trade.portfolio_code == portfolio_code,
            Trade.product_code == "CASH",
            Trade.platform_code.isnot(None),
            Trade.status.in_(["pending", "confirmed"]),
        )
        .order_by(Trade.id)
        .all()
    )
    leg_index = {(leg.transfer_group, leg.trade_type): leg for leg in legs}

    rows: list = []
    for leg in legs:
        is_buy = leg.trade_type == "buy"
        amount = _dec(leg.amount)
        signed = _signed(amount, is_buy)
        confirmed = leg.status == "confirmed"
        kind = _classify_transfer_group(leg.transfer_group)
        # 锚点日：卖出锚 trade_date、买入锚 confirm_date（与 calculate_available_cash
        # 的时点口径 #70/#78 逐字同源，保证「余额列 = 该函数单次调用」可对账闭合）
        anchor = leg.trade_date if not is_buy else (leg.confirm_date or leg.trade_date)
        settled = confirmed and anchor <= today

        row = {
            "source_kind": "trade_leg",
            "cash_date": anchor,
            "platform_code": leg.platform_code,
            "amount": signed,
            "is_override": False,
            "settled": settled,
            "direction": "in" if signed > 0 else "out",
            "origin_kind": "trade",
            "origin_id": leg.id,
            "origin_group": leg.transfer_group,
            "trade_status": leg.status,
            "cash_date_sub": None,
            "cash_date_label": "",
            "source_main": "",
            "source_sub": None,
            "pair": None,
            "_note": None,
        }

        if kind == "rebal":
            if is_buy:
                # 调仓卖出的到账腿：trade_date = 基金腿确认日 C、confirm_date = 到账日 A
                row.update(
                    flow_type="rebal_sell",
                    cash_date_label="到账日 A · 预计" if not settled else "到账日 A",
                    cash_date_sub=f"基金确认日 C {leg.trade_date}",
                )
                if not settled:
                    row["_note"] = f"预计到账 {anchor}"
            else:
                # 调仓买入的扣款腿：trade_date == confirm_date == 下单日 T（#493）
                row.update(flow_type="rebal_buy", cash_date_label="扣款日 T")
        elif kind == "sub":
            if is_buy:
                row.update(flow_type="sub_buy", cash_date_label="确认日 C")
            else:
                row.update(
                    flow_type="redeem",
                    cash_date_label="申请日",
                    cash_date_sub=(
                        f"到账日 {leg.confirm_date}" if leg.confirm_date else None
                    ),
                )
        else:
            row["source_kind"] = "transfer"
            opposite = leg_index.get(
                (leg.transfer_group, "buy" if not is_buy else "sell")
            )
            row.update(
                flow_type="transfer",
                cash_date_label=(
                    "到账日 · 预计" if (is_buy and not settled) else
                    ("到账日" if is_buy else "转移日")
                ),
                _pair_platform=opposite.platform_code if opposite else None,
                _pair_leg_id=opposite.id if opposite else None,
                _transfer_direction="转入" if is_buy else "转出",
            )
            if is_buy and not settled:
                row["_note"] = f"预计到账 {anchor}"
        rows.append(row)
    return rows, leg_index


def _event_rows(db: Session, portfolio_code: str, today: date) -> list:
    """平台级现金事件 → 分红/现金调整行。

    只取 `status="confirmed"`：pending 事件既不进 `calculate_available_cash`、
    也不进快照的分红在途窗口（`_compute_in_transit_amounts` 同判据），
    读侧不得凭空多出一类在途。
    """
    events = (
        db.query(ShareChangeEvent)
        .filter(
            ShareChangeEvent.portfolio_code == portfolio_code,
            ShareChangeEvent.status == "confirmed",
            ShareChangeEvent.platform_code.isnot(None),
            ShareChangeEvent.event_type.in_(CASH_EFFECT_EVENT_TYPES),
            ShareChangeEvent.cash_change.isnot(None),
            ShareChangeEvent.cash_change != 0,
        )
        .order_by(ShareChangeEvent.id)
        .all()
    )
    rows: list = []
    for e in events:
        cash_date = e.cash_effective_date
        settled = cash_date <= today
        is_dividend = e.event_type == "cash_dividend"
        note = None
        if not settled:
            note = f"预计到账 {cash_date}" if is_dividend and e.cash_pay_date else (
                "cash_pay_date 未回填，按除息日计" if is_dividend else None
            )
        signed = _dec(e.cash_change)  # 事件金额自带符号（C1：不翻向）
        rows.append({
            "source_kind": "dividend",
            "flow_type": "dividend",
            "cash_date": cash_date,
            "cash_date_label": (
                "到账日 cash_pay_date" if is_dividend and e.cash_pay_date else "除息日 ex_date"
            ),
            "cash_date_sub": None,
            "platform_code": e.platform_code,
            "amount": signed,
            "is_override": False,
            "settled": settled,
            "direction": "in" if signed > 0 else "out",
            "source_main": "",
            "source_sub": None,
            "origin_kind": "share_change_event",
            "origin_id": e.id,
            "origin_group": None,
            "trade_status": e.status,
            "pair": None,
            "_note": note,
            "_product": (e.product_code, e.market),
            "_ex_date": e.ex_date,
            "_is_dividend": is_dividend,
            "_has_pay_date": bool(e.cash_pay_date),
        })
    return rows


def _revalue_rows(db: Session, portfolio_code: str, today: date) -> list:
    """手动现金重估 → 重估行（绝对值，不参与求和）。

    `settled` 只看覆盖日是否已到：重估经快照 baked-in 才被
    `calculate_available_cash` 读到，未重算时用 note 点明，不改判在途
    （它不是「钱在路上」，混进 in_transit 会把绝对值当增量）。
    """
    mmvs = (
        db.query(ManualMarketValue)
        .filter(
            ManualMarketValue.portfolio_code == portfolio_code,
            ManualMarketValue.product_code == "CASH",
        )
        .order_by(ManualMarketValue.id)
        .all()
    )
    if not mmvs:
        return []
    latest_snapshot = get_latest_snapshot_date(db, portfolio_code)
    rows: list = []
    for m in mmvs:
        settled = m.value_date <= today
        baked = latest_snapshot is not None and m.value_date <= latest_snapshot
        note = None if baked else "覆盖尚未烘焙进快照，需重算快照后生效"
        rows.append({
            "source_kind": "revalue",
            "flow_type": "revalue",
            "cash_date": m.value_date,
            "cash_date_label": "覆盖日",
            "cash_date_sub": None,
            "platform_code": m.platform_code,
            "amount": _dec(m.market_value),
            "is_override": True,
            "settled": settled,
            "direction": "neutral",
            "source_main": "手工更新现金市值",
            "source_sub": (
                f"覆盖前计算值 {m.computed_value}" if m.computed_value is not None else None
            ),
            "origin_kind": "manual_market_value",
            "origin_id": m.id,
            "origin_group": None,
            "trade_status": None,
            "pair": None,
            "_note": note,
        })
    return rows


def _sort_key(row: dict):
    return (
        -row["cash_date"].toordinal(),
        row["platform_code"],
        _SOURCE_ORDER[row["source_kind"]],
        row["origin_id"],
    )


def _build_rows(db: Session, portfolio_code: str, today: date) -> tuple[list, dict]:
    trade_rows, leg_index = _trade_leg_rows(db, portfolio_code, today)
    rows = trade_rows + _event_rows(db, portfolio_code, today) + _revalue_rows(
        db, portfolio_code, today
    )
    rows.sort(key=_sort_key)
    return rows, leg_index


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------


def _sum_flows(rows: list, *, include_transfer: bool) -> tuple[Decimal, Decimal]:
    """已生效行的流入/流出（outflow 为负值）。重估恒排除（绝对值非流量）。"""
    inflow = _ZERO
    outflow = _ZERO
    for r in rows:
        if r["is_override"] or not r["settled"]:
            continue
        if not include_transfer and r["flow_type"] == "transfer":
            continue
        if r["amount"] > 0:
            inflow += r["amount"]
        elif r["amount"] < 0:
            outflow += r["amount"]
    return inflow, outflow


def _sum_in_transit(rows: list) -> Decimal:
    """未生效行的带符号金额（按行所属平台归属）；重估排除（见 _revalue_rows）。"""
    total = _ZERO
    for r in rows:
        if r["is_override"] or r["settled"]:
            continue
        total += r["amount"]
    return total


def _platform_codes(db: Session, portfolio_code: str, rows: list) -> list:
    """构成弹窗的平台集合：出现过流水行的平台 ∪ 最新快照里有 CASH 持仓的平台。

    只用行集会让「区间内无流水但有钱」的平台在弹窗里消失，可用现金行和对不上合计。
    """
    codes = {r["platform_code"] for r in rows}
    latest_snapshot = get_latest_snapshot_date(db, portfolio_code)
    if latest_snapshot is not None:
        codes |= {
            p
            for (p,) in db.query(PortfolioPosition.platform_code)
            .filter(
                PortfolioPosition.portfolio_code == portfolio_code,
                PortfolioPosition.product_code == "CASH",
                PortfolioPosition.snapshot_date == latest_snapshot,
                PortfolioPosition.platform_code.isnot(None),
            )
            .distinct()
            .all()
        }
    return sorted(codes)


def _balance_maps(db: Session, portfolio_code: str, rows: list) -> tuple[dict, dict, dict]:
    """按 (平台, 现金日期) **去重**后取余额（契约调整 C7）。

    返回 (日终余额表, 前一交易日表, 前日日终余额表)。前一交易日走
    `try_get_prev_trading_day`：日历覆盖不足时返回 None 而不是抛
    CALENDAR_NOT_SYNCED——只读列表不该因为区间贴近日历起点而整体 500。
    """
    keys = {(r["platform_code"], r["cash_date"]) for r in rows}
    end_of_day = {
        key: calculate_available_cash(db, portfolio_code, key[0], as_of_date=key[1])
        for key in keys
    }
    prev_of = {key: try_get_prev_trading_day(db, key[1]) for key in keys}
    prev_keys = {
        (platform, prev) for (platform, _), prev in prev_of.items() if prev is not None
    }
    prev_balance = {
        key: end_of_day[key]
        if key in end_of_day
        else calculate_available_cash(db, portfolio_code, key[0], as_of_date=key[1])
        for key in prev_keys
    }
    return end_of_day, prev_of, prev_balance


# ---------------------------------------------------------------------------
# 当页行的展示信息补齐（批量查询，防 N+1）
# ---------------------------------------------------------------------------


def _platform_name_map(db: Session, codes: set) -> dict:
    if not codes:
        return {}
    return {
        p.code: p.name
        for p in db.query(Platform).filter(Platform.code.in_(codes)).all()
    }


def _enrich_rows(
    db: Session,
    rows: list,
    leg_index: dict,
    platform_names: dict,
    today: date,
) -> None:
    """就地补齐当页行的来源文案与关联业务（pair）。只对当页行调用。"""
    if not rows:
        return

    # 1) 调仓行 → 基金腿；申赎行 → 申赎记录
    rebal_groups = {
        r["origin_group"] for r in rows
        if r["source_kind"] == "trade_leg" and r["flow_type"] in ("rebal_buy", "rebal_sell")
    }
    fund_legs = {}
    if rebal_groups:
        for leg in db.query(Trade).filter(
            Trade.transfer_group.in_(rebal_groups),
            Trade.product_code != "CASH",
        ).all():
            fund_legs[leg.transfer_group] = leg

    sub_ids = set()
    for r in rows:
        if r["source_kind"] == "trade_leg" and r["flow_type"] in ("sub_buy", "redeem"):
            group = r["origin_group"] or ""
            if group.startswith("sub_") and group[4:].isdigit():
                sub_ids.add(int(group[4:]))
    subs = {}
    if sub_ids:
        for s in db.query(Subscription).filter(Subscription.id.in_(sub_ids)).all():
            subs[s.id] = s
    investor_names = {}
    investor_codes = {s.investor_code for s in subs.values()}
    if investor_codes:
        investor_names = {
            i.code: i.name
            for i in db.query(Investor).filter(Investor.code.in_(investor_codes)).all()
        }

    # 2) 产品名称与显示码：基金腿 + 分红行一次查完（#175/#705 同一助手）
    pairs = set()
    for leg in fund_legs.values():
        pairs.add((leg.product_code, leg.market))
    for r in rows:
        if r["source_kind"] == "dividend":
            pairs.add(r["_product"])
    name_map, display_map = build_product_label_maps(db, pairs)

    for r in rows:
        r["platform_name"] = platform_names.get(r["platform_code"])
        flow_type = r["flow_type"]

        if flow_type in ("rebal_buy", "rebal_sell"):
            fund = fund_legs.get(r["origin_group"])
            if fund is not None:
                key = (fund.product_code, fund.market)
                name = name_map.get(key) or fund.product_code
                r["source_main"] = name
                r["source_sub"] = display_map.get(key)
                fund_settled = fund.status == "confirmed" and (
                    fund.confirm_date is None or fund.confirm_date <= today
                )
                r["pair"] = {
                    "kind": "fund_leg",
                    "id": fund.id,
                    "label": (
                        f"{name} · {'买入' if fund.trade_type == 'buy' else '卖出'} · "
                        f"{'已确认' if fund.status == 'confirmed' else '待确认'}"
                    ),
                    "platform_code": fund.platform_code,
                    "platform_name": platform_names.get(fund.platform_code),
                    "ref_date": fund.confirm_date,
                    "amount": _dec(fund.amount),
                    "settled": fund_settled,
                }
                if flow_type == "rebal_buy" and fund.status != "confirmed" and r["settled"]:
                    r["_note"] = "基金腿待确认（现金已扣款）"
            else:
                r["source_main"] = r["source_main"] or "调仓交易"

        elif flow_type in ("sub_buy", "redeem"):
            sub_id = next(
                (i for i in sub_ids if r["origin_group"] == f"sub_{i}"), None
            )
            sub = subs.get(sub_id) if sub_id is not None else None
            if sub is not None:
                investor = investor_names.get(sub.investor_code, sub.investor_code)
                sub_label = "申购" if sub.sub_type == "subscribe" else "赎回"
                r["source_main"] = investor
                r["source_sub"] = (
                    f"申赎 #{sub.id} · {sub_label} · 申请日 {sub.apply_date}"
                )
                r["pair"] = {
                    "kind": "subscription",
                    "id": sub.id,
                    "label": f"申赎 #{sub.id} · {investor} · {sub_label}",
                    "platform_code": sub.platform_code,
                    "platform_name": platform_names.get(sub.platform_code),
                    "ref_date": sub.confirm_date,
                    "amount": _dec(sub.amount),
                    "settled": sub.status == "confirmed",
                }
            else:
                r["source_main"] = r["source_main"] or "申购赎回"

        elif flow_type == "transfer":
            pair_platform = r.get("_pair_platform")
            r["source_main"] = platform_names.get(pair_platform, pair_platform) or "平台间转移"
            r["source_sub"] = (
                f"{r['_transfer_direction']} · 跨平台转移 {r['origin_group']}"
            )
            opposite = leg_index.get(
                (
                    r["origin_group"],
                    "sell" if r["direction"] == "in" else "buy",
                )
            )
            if opposite is not None:
                opp_in = opposite.trade_type == "buy"
                opp_anchor = (
                    opposite.trade_date if not opp_in
                    else (opposite.confirm_date or opposite.trade_date)
                )
                opp_settled = (
                    opposite.status == "confirmed" and opp_anchor <= today
                )
                r["pair"] = {
                    "kind": "transfer_leg",
                    "id": opposite.id,
                    "label": (
                        f"{'转入' if opp_in else '转出'} · "
                        f"{platform_names.get(opposite.platform_code, opposite.platform_code)}"
                    ),
                    "platform_code": opposite.platform_code,
                    "platform_name": platform_names.get(opposite.platform_code),
                    "ref_date": opp_anchor,
                    "amount": _signed(_dec(opposite.amount), opp_in),
                    "settled": opp_settled,
                }

        elif flow_type == "dividend":
            key = r["_product"]
            name = name_map.get(key) or key[0]
            r["source_main"] = name
            sub = display_map.get(key) or key[0]
            sub = f"{sub} · 除息日 {r['_ex_date']}"
            if r["_is_dividend"] and not r["_has_pay_date"]:
                sub += " · cash_pay_date 未回填"
            r["source_sub"] = sub

        elif flow_type == "revalue":
            r["platform_name"] = platform_names.get(r["platform_code"])

        r["status"] = _status_of(
            flow_type, r["direction"], r["settled"], r.get("_note")
        )
        if flow_type == "revalue":
            r["action"] = {"label": "撤销", "kind": "revoke"}
        elif r["trade_status"] == "pending" and flow_type == "transfer":
            r["action"] = {"label": "确认到账", "kind": "confirm"}
        else:
            r["action"] = {"label": "详情", "kind": "detail"}
        r["flow_type_label"] = (
            "现金调整" if (flow_type == "dividend" and not r.get("_is_dividend", True))
            else FLOW_TYPE_LABELS[flow_type]
        )


def _row_payload(row: dict, group_counts: Counter, balances: tuple) -> dict:
    end_of_day, prev_of, prev_balance = balances
    key = (row["platform_code"], row["cash_date"])
    prev_date = prev_of.get(key)
    return {
        "source_kind": row["source_kind"],
        "flow_type": row["flow_type"],
        "flow_type_label": row["flow_type_label"],
        "cash_date": row["cash_date"],
        "cash_date_label": row["cash_date_label"],
        "cash_date_sub": row["cash_date_sub"],
        "platform_code": row["platform_code"],
        "platform_name": row.get("platform_name"),
        "source_main": row["source_main"],
        "source_sub": row["source_sub"],
        "amount": row["amount"],
        "is_override": row["is_override"],
        "settled": row["settled"],
        "status": row["status"],
        "action": row["action"],
        "origin_ref": {
            "kind": row["origin_kind"],
            "id": row["origin_id"],
            "transfer_group": row["origin_group"],
        },
        "pair_ref": row["pair"],
        "group_total_count": group_counts.get(key, 1),
        "platform_end_of_day_balance": end_of_day.get(key),
        "prev_day_balance": (
            prev_balance.get((row["platform_code"], prev_date))
            if prev_date is not None else None
        ),
    }


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def list_cash_flows(
    db: Session,
    portfolio_code: str,
    *,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    status: Optional[str] = None,
    platform_code: Optional[str] = None,
    flow_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """现金流水聚合查询（只读）。

    筛选与聚合范围（契约调整 C6）：

    - `items` / `total`：区间 + 平台 + 状态 + 类型四重筛选后的行集，按现金变动笔数
      分页计数（一笔平台间转移 = 2 笔，与各平台账本同构）。
    - `group_total_count`：**解除类型筛选**、保留区间/平台/状态的同 (平台, 现金日期)
      总笔数——前端「当日另有 N 笔」= 该值 − 在屏同组行数。
    - `overview.available` / `in_transit`：存量卡，只随平台筛选切 scope（时点事实，
      「我只想看分红」不改变有多少钱）；`inflow` / `outflow`：流量卡，对筛选后**全集**
      聚合（不是当页）。组合级流量排除平台间转移（内部搬运，否则一笔被计两次），
      平台级含转移（对该平台是真进出）；两级均排除重估。
    - `platform_breakdown`：恒为全部平台的构成（不随平台筛选），rows[i] 四值 == 筛该
      平台时的 overview，total == 组合口径，recon = Σrows − total（= 本期已生效转移腿）。

    Returns:
        dict（可直接喂 `PaginatedCashFlowResponse`）。
    """
    if status is not None and status not in STATUS_VALUES:
        raise BusinessError(
            "INVALID_PARAM",
            f"status 仅支持 {' / '.join(STATUS_VALUES)}（收到 {status}）",
            http_status=422,
        )
    if flow_type is not None and flow_type not in FLOW_TYPES:
        raise BusinessError(
            "INVALID_PARAM",
            f"flow_type 仅支持 {' / '.join(FLOW_TYPES)}（收到 {flow_type}）",
            http_status=422,
        )
    if from_date and to_date and from_date > to_date:
        raise BusinessError(
            "INVALID_DATE_RANGE",
            f"from_date ({from_date}) 不能晚于 to_date ({to_date})",
            http_status=422,
        )
    if page < 1:
        raise BusinessError("INVALID_PARAM", "page 必须 >= 1", http_status=422)
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise BusinessError(
            "INVALID_PARAM",
            f"page_size 必须在 1-{MAX_PAGE_SIZE} 之间（收到 {page_size}）",
            http_status=422,
        )

    if not db.query(Portfolio).filter(Portfolio.code == portfolio_code).first():
        raise NotFoundError("PORTFOLIO_NOT_FOUND", f"组合 {portfolio_code} 不存在")

    today = date.today()
    all_rows, leg_index = _build_rows(db, portfolio_code, today)

    in_range = [
        r for r in all_rows
        if (from_date is None or r["cash_date"] >= from_date)
        and (to_date is None or r["cash_date"] <= to_date)
    ]
    scoped = [
        r for r in in_range
        if platform_code is None or r["platform_code"] == platform_code
    ]
    status_scoped = [
        r for r in scoped
        if status is None
        or (r["settled"] if status == STATUS_SETTLED else not r["settled"])
    ]
    display = [
        r for r in status_scoped
        if flow_type is None or r["flow_type"] == flow_type
    ]

    total = len(display)
    start = (page - 1) * page_size
    page_rows = display[start:start + page_size]

    group_counts = Counter(
        (r["platform_code"], r["cash_date"]) for r in status_scoped
    )

    # 概览：存量卡只随平台筛选；流量卡跟随全部筛选（对全集，不是当页）
    available = calculate_available_cash(
        db, portfolio_code, platform_code, as_of_date=today
    )
    in_transit = _sum_in_transit(
        [
            r for r in all_rows
            if platform_code is None or r["platform_code"] == platform_code
        ]
    )
    inflow, outflow = _sum_flows(display, include_transfer=platform_code is not None)
    overview = {
        "available": available,
        "in_transit": in_transit,
        "inflow": inflow,
        "outflow": outflow,
    }

    # 构成弹窗：不受平台筛选影响（rows = 各平台、total = 组合口径）
    type_scoped = [
        r for r in in_range
        if status is None
        or (r["settled"] if status == STATUS_SETTLED else not r["settled"])
    ]
    if flow_type is not None:
        type_scoped = [r for r in type_scoped if r["flow_type"] == flow_type]

    codes = _platform_codes(db, portfolio_code, all_rows)
    breakdown_rows = []
    for code in codes:
        mine = [r for r in all_rows if r["platform_code"] == code]
        mine_flows = [r for r in type_scoped if r["platform_code"] == code]
        b_inflow, b_outflow = _sum_flows(mine_flows, include_transfer=True)
        breakdown_rows.append({
            "platform_code": code,
            "platform_name": None,  # 下方统一按平台名补齐
            "available": calculate_available_cash(
                db, portfolio_code, code, as_of_date=today
            ),
            "in_transit": _sum_in_transit(mine),
            "inflow": b_inflow,
            "outflow": b_outflow,
        })
    total_inflow, total_outflow = _sum_flows(type_scoped, include_transfer=False)
    breakdown_total = {
        "available": calculate_available_cash(db, portfolio_code, None, as_of_date=today),
        "in_transit": _sum_in_transit(all_rows),
        "inflow": total_inflow,
        "outflow": total_outflow,
    }
    recon = {
        "inflow": sum((r["inflow"] for r in breakdown_rows), _ZERO) - total_inflow,
        "outflow": sum((r["outflow"] for r in breakdown_rows), _ZERO) - total_outflow,
    }

    # 平台名：当页行 + 转移对方平台 + 关联业务平台 + 构成弹窗行，一次查完
    name_codes = {r["platform_code"] for r in page_rows} | set(codes)
    for r in page_rows:
        if r.get("_pair_platform"):
            name_codes.add(r["_pair_platform"])
    platform_names = _platform_name_map(db, name_codes)
    for row in breakdown_rows:
        row["platform_name"] = platform_names.get(row["platform_code"])

    _enrich_rows(db, page_rows, leg_index, platform_names, today)
    # 关联业务可能落在当页行平台之外（转移对方腿、基金腿跨平台扣款 #91）
    extra_codes = {
        r["pair"]["platform_code"]
        for r in page_rows
        if r.get("pair") and r["pair"].get("platform_code")
    } - set(platform_names)
    if extra_codes:
        extra_names = _platform_name_map(db, extra_codes)
        platform_names.update(extra_names)
        for r in page_rows:
            pair = r.get("pair")
            if pair and pair.get("platform_code") in extra_names:
                pair["platform_name"] = extra_names[pair["platform_code"]]

    balances = _balance_maps(db, portfolio_code, page_rows)
    items = [_row_payload(r, group_counts, balances) for r in page_rows]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "overview": overview,
        "platform_breakdown": {
            "rows": breakdown_rows,
            "total": breakdown_total,
            "recon": recon,
        },
    }
