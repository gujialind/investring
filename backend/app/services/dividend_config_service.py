"""分红方式配置服务（#887，SPEC v1.0 §1.2/§2/§6/§8.1）。

解析单点 resolve_dividend_method：键 (组合, 产品, 市场, 平台) + 权益登记日生效区间，
source 分级（丙）——检测器（PR2）以 source_filter='explicit' 消费，default 行对其不可见；
确认弹窗预选经 resolve 端点不滤 source（A12）。本服务不读事件、不改账务：确认/自动确认/
快照重放/应收派生一律不读配置（A3/A7/A10）。

写语义（SPEC §1.2）：
- 追加 POST → source='explicit'；允许补录过去日期（D3）。
- 同键同 effective_from 碰撞：default 行→提升替换为 explicit（不论日期，审计 update）；
  explicit 行同方式→幂等无操作；今日/未来不同方式→替换（审计 update）；历史不同方式→拒绝。
- DELETE 仅今日/未来行（default/explicit 同规则）；历史行不可删。
- 写侧市场恒 CN_OTC（R10/A14）；reinvest 另受 REINVEST_ALLOWED_MARKETS 纵深约束（A4）。

service 层只抛领域异常，不 commit。
"""
from datetime import date
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from app.constants.audit_actions import (
    ACTION_CREATE, ACTION_UPDATE, ACTION_DELETE, RESOURCE_DIVIDEND_CONFIG,
)
from app.models.dividend_method_config import DividendMethodConfig
from app.models.platform import Platform
from app.models.portfolio import Portfolio
from app.services.audit_service import record_audit
from app.services.exceptions import BusinessError, NotFoundError
from app.services.product_service import resolve_product_market
from app.services.share_change_event_service import (
    REINVEST_ALLOWED_MARKETS, MSG_REINVEST_ON_EXCHANGE,
)
from app.utils.business_time import business_today

# 写侧市场收口（R10/A14）：配置能力只覆盖 CN_OTC。与迁移 0027 回填白名单同步。
ALLOWED_CONFIG_MARKETS = {"CN_OTC"}
VALID_METHODS = {"cash_dividend", "reinvest_dividend"}
SOURCE_DEFAULT = "default"
SOURCE_EXPLICIT = "explicit"

MSG_MARKET_NOT_SUPPORTED = "该产品市场不支持分红方式配置"
MSG_METHOD_INVALID = "分红方式仅支持 cash_dividend 或 reinvest_dividend"
MSG_HISTORY_EXPLICIT_IMMUTABLE = "历史显式配置不可修改，请到配置页调整今日或未来生效的配置行"
MSG_HISTORY_ROW_UNDELETABLE = "历史配置行不可删除，仅可删除今日或未来生效的行"


def _audit_payload(row: DividendMethodConfig) -> dict:
    """审计载荷（SPEC §8.1）：键四元组 + old/new method + effective_from + source。"""
    return {
        "portfolio_code": row.portfolio_code,
        "product_code": row.product_code,
        "market": row.market,
        "platform_code": row.platform_code,
        "dividend_method": row.dividend_method,
        "effective_from": row.effective_from.isoformat(),
        "source": row.source,
    }


def resolve_dividend_method(
    db: Session,
    portfolio_code: str,
    product_code: str,
    market: str,
    platform_code: str,
    entitlement_date: date,
    *,
    source_filter: Optional[str] = None,
) -> Optional[DividendMethodConfig]:
    """解析单点（SPEC §2）：键 + effective_from <= 权益登记日，取最大生效日一行。

    source_filter='explicit' 供检测器（PR2）——default 行不可见（A3）；
    None 供确认弹窗预选（A12，default 预选是本职）。区间缺失→None。
    """
    query = db.query(DividendMethodConfig).filter(
        DividendMethodConfig.portfolio_code == portfolio_code,
        DividendMethodConfig.product_code == product_code,
        DividendMethodConfig.market == market,
        DividendMethodConfig.platform_code == platform_code,
        DividendMethodConfig.effective_from <= entitlement_date,
    )
    if source_filter is not None:
        query = query.filter(DividendMethodConfig.source == source_filter)
    return query.order_by(DividendMethodConfig.effective_from.desc()).first()


def resolve_config(
    db: Session, *, portfolio_code: str, product_code: str,
    market: Optional[str], platform_code: str, entitlement_date: date,
) -> Optional[DividendMethodConfig]:
    """resolve 端点入口（A12）：补全 market 后按权益登记日解析，不滤 source。"""
    _, resolved_market = resolve_product_market(db, product_code, market)
    return resolve_dividend_method(
        db, portfolio_code, product_code, resolved_market, platform_code, entitlement_date,
    )


def list_configs(
    db: Session, *, portfolio_code: str, product_code: str,
    market: Optional[str], platform_code: str, page: int, page_size: int,
) -> Tuple[List[DividendMethodConfig], int]:
    """历史列表（effective_from 降序，id 次级键保证确定性，#165）。"""
    _, resolved_market = resolve_product_market(db, product_code, market)
    query = db.query(DividendMethodConfig).filter(
        DividendMethodConfig.portfolio_code == portfolio_code,
        DividendMethodConfig.product_code == product_code,
        DividendMethodConfig.market == resolved_market,
        DividendMethodConfig.platform_code == platform_code,
    )
    total = query.count()
    items = (
        query.order_by(
            DividendMethodConfig.effective_from.desc(), DividendMethodConfig.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def get_config(db: Session, config_id: int) -> DividendMethodConfig:
    row = db.query(DividendMethodConfig).filter(
        DividendMethodConfig.id == config_id
    ).first()
    if not row:
        raise NotFoundError("NOT_FOUND", "分红方式配置不存在")
    return row


def _validate_market(market: str) -> None:
    if market not in ALLOWED_CONFIG_MARKETS:
        raise BusinessError(
            "INVALID_PARAM", MSG_MARKET_NOT_SUPPORTED,
            details={"field": "market", "value": market},
        )


def _validate_method(dividend_method: str, market: str) -> None:
    if dividend_method not in VALID_METHODS:
        raise BusinessError(
            "INVALID_PARAM", MSG_METHOD_INVALID,
            details={"field": "dividend_method", "value": dividend_method},
        )
    # A4 纵深：写侧已挡 CN_OTC 之外，reinvest 再受既有市场白名单约束（脏行防御）。
    if dividend_method == "reinvest_dividend" and market not in REINVEST_ALLOWED_MARKETS:
        raise BusinessError(
            "INVALID_PARAM", MSG_REINVEST_ON_EXCHANGE,
            details={"field": "market", "value": market},
        )


def _validate_key_exists(db: Session, portfolio_code: str, platform_code: str) -> None:
    if not db.query(Portfolio).filter(Portfolio.code == portfolio_code).first():
        raise NotFoundError("NOT_FOUND", f"组合 {portfolio_code} 不存在")
    if not db.query(Platform).filter(Platform.code == platform_code).first():
        raise NotFoundError("PLATFORM_NOT_FOUND", f"平台 {platform_code} 不存在")


def create_config(
    db: Session, *, portfolio_code: str, product_code: str, market: Optional[str],
    platform_code: str, dividend_method: str, effective_from: date,
) -> DividendMethodConfig:
    """追加/提升/替换（SPEC §1.2）。default 遇 explicit 写入即提升；历史 explicit 不可改。"""
    _, resolved_market = resolve_product_market(db, product_code, market)
    _validate_market(resolved_market)
    _validate_method(dividend_method, resolved_market)
    _validate_key_exists(db, portfolio_code, platform_code)

    existing = db.query(DividendMethodConfig).filter(
        DividendMethodConfig.portfolio_code == portfolio_code,
        DividendMethodConfig.product_code == product_code,
        DividendMethodConfig.market == resolved_market,
        DividendMethodConfig.platform_code == platform_code,
        DividendMethodConfig.effective_from == effective_from,
    ).first()

    if existing is not None:
        if existing.source == SOURCE_DEFAULT:
            # 提升替换：default 是系统预选值、非用户历史，不论日期直接提升（A13）。
            old = _audit_payload(existing)
            existing.dividend_method = dividend_method
            existing.source = SOURCE_EXPLICIT
            db.flush()
            record_audit(
                db, action=ACTION_UPDATE, resource_type=RESOURCE_DIVIDEND_CONFIG,
                resource_id=str(existing.id), old_value=old, new_value=_audit_payload(existing),
            )
            return existing
        # explicit 行：同方式幂等无操作（无实际变更不留痕，与事件 PUT 同口径）。
        if existing.dividend_method == dividend_method:
            return existing
        if existing.effective_from < business_today():
            raise BusinessError(
                "INVALID_PARAM", MSG_HISTORY_EXPLICIT_IMMUTABLE,
                details={"effective_from": existing.effective_from.isoformat()},
            )
        old = _audit_payload(existing)
        existing.dividend_method = dividend_method
        db.flush()
        record_audit(
            db, action=ACTION_UPDATE, resource_type=RESOURCE_DIVIDEND_CONFIG,
            resource_id=str(existing.id), old_value=old, new_value=_audit_payload(existing),
        )
        return existing

    row = DividendMethodConfig(
        portfolio_code=portfolio_code, product_code=product_code, market=resolved_market,
        platform_code=platform_code, dividend_method=dividend_method,
        effective_from=effective_from, source=SOURCE_EXPLICIT,
    )
    db.add(row)
    db.flush()
    record_audit(
        db, action=ACTION_CREATE, resource_type=RESOURCE_DIVIDEND_CONFIG,
        resource_id=str(row.id), new_value=_audit_payload(row),
    )
    return row


def delete_config(db: Session, config_id: int) -> DividendMethodConfig:
    """删除仅今日/未来行（SPEC §1.2）；历史行不可删。已建事件不回改（A7）。"""
    row = get_config(db, config_id)
    if row.effective_from < business_today():
        raise BusinessError(
            "INVALID_PARAM", MSG_HISTORY_ROW_UNDELETABLE,
            details={"effective_from": row.effective_from.isoformat()},
        )
    old = _audit_payload(row)
    db.delete(row)
    db.flush()
    record_audit(
        db, action=ACTION_DELETE, resource_type=RESOURCE_DIVIDEND_CONFIG,
        resource_id=str(row.id), old_value=old,
    )
    return row