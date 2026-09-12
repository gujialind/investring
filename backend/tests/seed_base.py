# ============================================================================
# 基础数据种子（单一事实来源）
# ============================================================================
# pytest（conftest._seed_base_data）与 CI E2E（scripts/seed_e2e.py）、
# 本地 E2E（scripts/run_e2e_backend.py）共用本模块，替代已退役的
# scripts/init_data.py（issue #222）。全部写入带存在性检查，幂等。
#
# 注意：本模块是测试/E2E 固件（含测试口令与启发式日历），不是生产种子——
# 生产基础数据由 alembic 迁移落库。
# ============================================================================

from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Investor, Portfolio, Product, Platform,
    AssetClassification, AssetDimensionApplicability, AssetClassDimensionRule,
    TradingCalendar,
)
from app.constants.asset_dimensions import (
    ASSET_DIMENSIONS, PRODUCT_DIMENSIONS, DIMENSION_APPLICABILITY, DIMENSION_RULES,
)
from app.utils.security import get_password_hash


def seed_base_data(db: Session) -> None:
    """种子基础数据：维度字典、适用关系、平台、产品、日历、draft 组合 E2E_PORT、用户。

    调用方负责 commit/rollback/close 的事务外壳。
    E2E 活跃组合 E2E_ACTIVE 由 seed_e2e_active 单独承载（仅 E2E 消费方调用），
    不进 pytest session 种子——避免业务交易数据泄漏进按全局账本精确断言的存量测试。
    """
    # 1. 资产分类维度值字典（issue #128，与迁移 0008 同源）
    for code, dimension, name, sort_order, description in ASSET_DIMENSIONS:
        if not db.query(AssetClassification).filter(AssetClassification.code == code).first():
            db.add(AssetClassification(
                code=code, dimension=dimension, name=name,
                sort_order=sort_order, description=description,
            ))
    db.commit()

    # 1b. 适用关系（issue #135 矩阵落库）：值级关联 + 维度级规则，
    #     与迁移 0009 同源（测试环境跳过 alembic，须在此种子）
    for value, classes in DIMENSION_APPLICABILITY.items():
        for asset_class in classes:
            if not db.query(AssetDimensionApplicability).filter_by(
                dimension_value_code=value, asset_class_code=asset_class
            ).first():
                db.add(AssetDimensionApplicability(
                    dimension_value_code=value,
                    asset_class_code=asset_class,
                ))
    for asset_class, rules in DIMENSION_RULES.items():
        for dimension, rule in rules.items():
            if not db.query(AssetClassDimensionRule).filter_by(
                asset_class_code=asset_class, dimension=dimension
            ).first():
                db.add(AssetClassDimensionRule(
                    asset_class_code=asset_class, dimension=dimension, rule=rule,
                ))
    db.commit()

    # 2. 平台
    platforms = [
        {"code": "MYCF", "name": "蚂蚁财富", "platform_type": "第三方平台"},
        {"code": "HBZQ", "name": "华宝证券", "platform_type": "券商"},
        {"code": "TTJJ", "name": "天天基金", "platform_type": "第三方平台"},
        {"code": "ZB", "name": "纸币", "platform_type": "其他"},
    ]
    for p in platforms:
        if not db.query(Platform).filter(Platform.code == p["code"]).first():
            db.add(Platform(**p))
    db.commit()

    # 3. 示例产品（精简版，仅用于测试；五维度标签取自 PRODUCT_DIMENSIONS 同源判定）
    def _dims(code):
        d = PRODUCT_DIMENSIONS[code]
        return {"asset_class_code": d[0], "region_code": d[1],
                "style_code": d[2], "size_code": d[3], "segment_code": d[4]}

    products = [
        {"code": "CASH", "market": "", "name": "现金类资产", "product_type": "CASH",
         "confirm_days": 0, "is_qdii": False, "nav_lag_days": 0, **_dims("CASH")},
        # #93: 在途资金虚拟产品（与 CASH 同构：market='', 五维度全 NULL）
        {"code": "IN_TRANSIT_BUY", "market": "", "name": "买入在途资金", "product_type": "IN_TRANSIT",
         "asset_class_code": None, "region_code": None, "style_code": None,
         "size_code": None, "segment_code": None, "confirm_days": 0, "is_qdii": False,
         "nav_lag_days": 0},
        {"code": "IN_TRANSIT_SELL", "market": "", "name": "卖出在途资金", "product_type": "IN_TRANSIT",
         "asset_class_code": None, "region_code": None, "style_code": None,
         "size_code": None, "segment_code": None, "confirm_days": 0, "is_qdii": False,
         "nav_lag_days": 0},
        {"code": "510300.SH", "market": "CN_EXCHANGE", "name": "沪深300ETF", "product_type": "ETF",
         "confirm_days": 0, "is_qdii": False, "nav_lag_days": 0,
         "asset_class_code": "ASSET_STOCK", "region_code": "REGION_CN",
         "style_code": "STYLE_BALANCED", "size_code": "SIZE_LARGE", "segment_code": "SEG_COMPOSITE"},
        {"code": "000300.OF", "market": "CN_OTC", "name": "沪深300联接A", "product_type": "OEF",
         "confirm_days": 1, "is_qdii": False, "nav_lag_days": 0,
         "asset_class_code": "ASSET_STOCK", "region_code": "REGION_CN",
         "style_code": "STYLE_BALANCED", "size_code": "SIZE_LARGE", "segment_code": "SEG_COMPOSITE"},
        # #259: LOF 一码双市场（产品选择器市场标识 E2E 种子；长名稳定触发截断）
        {"code": "161017.SZ", "market": "CN_EXCHANGE", "name": "富国中证500指数增强(LOF)A", "product_type": "LOF",
         "confirm_days": 0, "is_qdii": False, "nav_lag_days": 0,
         "asset_class_code": "ASSET_STOCK", "region_code": "REGION_CN",
         "style_code": "STYLE_BALANCED", "size_code": "SIZE_LARGE", "segment_code": "SEG_COMPOSITE"},
        {"code": "161017.OF", "market": "CN_OTC", "name": "富国中证500指数增强(LOF)A", "product_type": "LOF",
         "confirm_days": 1, "is_qdii": False, "nav_lag_days": 0,
         "asset_class_code": "ASSET_STOCK", "region_code": "REGION_CN",
         "style_code": "STYLE_BALANCED", "size_code": "SIZE_LARGE", "segment_code": "SEG_COMPOSITE"},
        # 场外 QDII：快照估值取 T-1 交易日净值（issue #228 nav_lag_days=1）
        {"code": "270042.OF", "market": "CN_OTC", "name": "广发纳指100(QDII)A", "product_type": "OEF",
         "confirm_days": 2, "is_qdii": True, "nav_lag_days": 1, **_dims("270042.OF")},
        {"code": "1001767344", "market": "HK_MUTUAL", "name": "摩根国际债券人民币对冲", "product_type": "OEF",
         "confirm_days": 1, "is_qdii": False, "nav_lag_days": 0,
         "data_source": "akshare", **_dims("1001767344")},
    ]
    for p in products:
        if not db.query(Product).filter(
            Product.code == p["code"], Product.market == p["market"]
        ).first():
            db.add(Product(**p))
    db.commit()

    # 4. 交易日历（2025-01-01 起，终点滚动到 today + 1 年；工作日为交易日）
    #    起点固定：存量测试大量依赖 2025 年固定日期（如 conftest 的 sample_trading_day）。
    #    终点随 date.today() 滚动（issue #468）：固定终点会让以 today 锚定的 E2E / 契约
    #    测试随时间集体失效（首爆点 2027-01-04 阻断 CI OK 与 CD）。
    #    幂等守卫从「表空才写」改为增量补尾：本地复用库已有旧终点时继续向后延伸，
    #    而不是被旧数据挡住（CI 空库起跑与旧守卫行为等价）。
    start = date(2025, 1, 1)
    end = date.today() + timedelta(days=365)
    last_seeded = db.query(func.max(TradingCalendar.calendar_date)).scalar()
    current = max(start, last_seeded + timedelta(days=1)) if last_seeded else start
    while current <= end:
        is_weekday = current.weekday() < 5  # Mon-Fri
        db.add(TradingCalendar(
            calendar_date=current,
            is_open=is_weekday,
            exchange="SSE",
        ))
        current += timedelta(days=1)
    db.commit()

    # 5. draft 组合（前端 E2E 冒烟依赖：无组合时业务 spec 优雅 skip，
    #    缺少它会让 datepicker/platform-select/regression 用例在 CI 静默跳过）
    if not db.query(Portfolio).filter(Portfolio.code == "E2E_PORT").first():
        db.add(Portfolio(
            code="E2E_PORT",
            name="E2E 冒烟组合",
            description="测试/E2E 种子 draft 组合（issue #222）",
        ))
        db.commit()

    # 6. 管理员用户（密码：admin@2026）
    if not db.query(Investor).filter(Investor.code == "ADMIN").first():
        db.add(Investor(
            code="ADMIN",
            name="测试管理员",
            role="admin",
            password_hash=get_password_hash("admin@2026"),
        ))
        db.commit()

    # 7. 普通用户（viewer，密码：viewer123）
    if not db.query(Investor).filter(Investor.code == "VIEWER").first():
        db.add(Investor(
            code="VIEWER",
            name="测试投资人",
            role="viewer",
            password_hash=get_password_hash("viewer123"),
        ))
        db.commit()


def seed_e2e_active(db: Session) -> None:
    """种 E2E 活跃组合 E2E_ACTIVE（issue #354）——仅 E2E 消费方调用。

    承载前端 E2E 业务数据用例，E2E_PORT 保持 draft 不动。**不进 pytest session
    种子**（conftest 只调 seed_base_data）——其业务交易/申购/价格数据会泄漏进按
    「全局账本」精确断言的存量后端测试（test_filter_by_status /
    test_sort_apply_date_desc / test_trades 系列 / 价格 upsert 计数等），且后端
    pytest 本就不需要 E2E_ACTIVE。E2E 消费方（scripts/seed_e2e.py、
    scripts/run_e2e_backend.py）在 seed_base_data 之后调用本函数。

    业务数据一律走 service 层造（复用 CASH 配对腿 / transfer_group / 激活状态机 /
    快照三表顺序等全部不变量），仅 Portfolio/PriceRecord 行直接 ORM。整段单事务、
    末尾一次 commit——service 均不 commit，失败即整体回滚无部分写入，故幂等守卫
    「组合已存在则跳过」是充分的（两处 E2E 消费方均从空库起跑）。

    日期锚定 date.today() 动态回溯 4 个交易日 D1<D2<D3<D4：pending 交易须落在
    前端交易列表默认「近1年」过滤窗内（#126），固定日期会随时间失效。依赖
    seed_base_data 的日历（2025-01-01 起、终点滚动到 today+1 年，issue #468）
    覆盖 today 及前 4 个交易日。下方 RuntimeError 守卫是防御性校验（防未来
    回溯逻辑改动引入真实越界）：终点越界时 get_prev_trading_day 向过去回退、
    取到日历末段交易日，四种日期仍互异，守卫不会触发（#468 订正原 docstring
    「越界响亮报错」的失实描述）。

    **禁止对 E2E_ACTIVE 跑 recalculate/catch-up/generate-next**：其 auto_confirm
    会确认 D4 pending 交易，破坏「存在可编辑 pending 交易」的 E2E 契约。
    """
    if db.query(Portfolio).filter(Portfolio.code == "E2E_ACTIVE").first():
        return

    # 局部导入：seed_base_data 的导入面保持轻量，这些重模块仅 E2E 路径需要
    from decimal import Decimal

    from app.models import PriceRecord
    from app.services.snapshot_service import generate_daily_snapshots
    from app.services.subscription_service import (
        confirm_single_subscription, create_subscription,
    )
    from app.services.trade_service import confirm_single_trade, create_trade
    from app.services.trading_utils import get_prev_trading_day, is_trading_day

    today = date.today()
    d4 = today if is_trading_day(db, today) else get_prev_trading_day(db, today)
    d3 = get_prev_trading_day(db, d4)
    d2 = get_prev_trading_day(db, d3)
    d1 = get_prev_trading_day(db, d2)
    if not all((d1, d2, d3, d4)) or len({d1, d2, d3, d4}) < 4:
        raise RuntimeError(
            "E2E_ACTIVE 种子日期回溯失败：交易日历须覆盖 today 及其前至少 4 个"
            f"交易日（today={today}）；日历终点不足时先扩展 seed_base_data 段 4"
        )

    db.add(Portfolio(
        code="E2E_ACTIVE",
        name="E2E 活跃组合",
        description="测试/E2E 种子活跃组合（issue #354）：1 申购 + 1 已确认场内交易 + 2 日快照 + 1 pending 交易",
    ))
    db.flush()

    # 价格行：快照严格取价（nav_lag_days=0 → 当日价），D2/D3 各一行；
    # 行情同步 service 会打真实外部 API，种子直接 ORM 插入（factories 同先例）
    for nav_date, px in ((d2, "4.0000"), (d3, "4.2000")):
        db.add(PriceRecord(
            product_code="510300.SH", market="CN_EXCHANGE",
            price_date=nav_date, unit_price=Decimal(px), source="seed",
        ))
    db.flush()

    # 首次申购：apply=D1、confirm=D2（T+1 自动），首窗净值 1.0000 无需行情；
    # 确认后组合转 active、started_at=D2、生成配对 CASH buy 腿
    sub = create_subscription(
        db, portfolio_code="E2E_ACTIVE", investor_code="ADMIN",
        platform_code="HBZQ", sub_type="subscribe",
        apply_date=d1, amount=Decimal("100000"),
        notes="E2E 种子首次申购",
    )
    db.flush()  # 配对 CASH 腿 transfer_group=f"sub_{id}" 依赖已分配的 id
    confirm_single_subscription(db, sub)
    db.flush()

    # 已确认场内买入：成交价录入即确认，不依赖行情同步；trade=confirm=D2（confirm_days=0）
    product_510300 = db.query(Product).filter(
        Product.code == "510300.SH", Product.market == "CN_EXCHANGE",
    ).first()
    trade_confirmed = create_trade(
        db, portfolio_code="E2E_ACTIVE", product_code="510300.SH",
        market="CN_EXCHANGE", trade_type="buy", trade_date=d2,
        price=Decimal("4.0000"), amount=Decimal("60000"), fee=Decimal("0"),
        platform_code="HBZQ", notes="E2E 种子场内买入（已确认）",
    )
    # 必须先 flush 让基金腿拿到 id：confirm 内 sync_transfer_group 以
    # (transfer_group, id != 自身) 在库中定位配对 CASH 腿，id=None 时查不到，
    # CASH 腿滞留 pending → 快照预校验 _check_pending_transactions 阻断
    db.flush()
    confirm_single_trade(db, trade_confirmed, product_510300)
    db.flush()

    # 两日快照（三表固定顺序生成）：首快照日 D2 == 最早 confirm_date（#180 首快照
    # 约束）；D3 = D2 次一交易日（连续原则）。D2 总值 100,000/nav 1.0000，
    # D3 总值 103,000/nav 1.0300
    generate_daily_snapshots(db, "E2E_ACTIVE", d2)
    generate_daily_snapshots(db, "E2E_ACTIVE", d3)

    # pending 场内买入（供前端编辑类用例）：trade=confirm=D4 > 最新快照 D3
    # （validate_trade_date 与快照 pending 预校验双满足）；只建不确认——
    # generate_daily_snapshots 不触发 auto_confirm
    create_trade(
        db, portfolio_code="E2E_ACTIVE", product_code="510300.SH",
        market="CN_EXCHANGE", trade_type="buy", trade_date=d4,
        price=Decimal("4.1000"), amount=Decimal("8200"), fee=Decimal("0"),
        platform_code="HBZQ", notes="E2E 种子场内买入（pending，供编辑用例）",
    )
    db.commit()
