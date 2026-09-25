"""更新请求的显式 null 收口（issue #573；#579 起全仓统一为本方向）

部分更新语义约定：**不传 = 不动**。显式传 null 在多数字段上没有定义语义，放行后会沿
`exclude_unset` → 服务层 setattr → 可空列 落库（UPDATE 不触发列默认值），而响应模型
字段不接受 None → 序列化 500，且该行此后 GET 单条/列表恒 500，不可自愈；即便不 500，
也是「静默 no-op / 谎报成功 / 误清空」三种不可感知形态之一（#579）。

已接入的更新路径：investor / product / asset_classification / subscription /
trade / share_change_event 六个服务，与 platforms / data_sources / portfolios
三个路由（portfolios 的更新入口在 router 分解 kwargs，守卫随之放 router）。

各调用点用 `allow` 声明「显式 null 有明确语义或另有专用校验器」的字段：
- 可空列 + 响应 Optional 的字段：null = 清除（如 investor.phone/email、
  asset_classification.description、product 五个维度标签、portfolio.description、
  trade.notes、share_change_event 的八个可空数值/备注字段）；
- null = 清空属既定业务语义的字段：portfolio.display_config（#144，router 以
  哨兵区分「不传」）；
- 已有专用校验器收口的字段：product 的 product_type/confirm_days/nav_lag_days、
  trade 的 cash_confirm_date（#493）——保留各自专用错误码，不并入本码。

service 层只抛领域异常，不 commit。
"""
from typing import Collection

from app.services.exceptions import BusinessError


def reject_explicit_nulls(updates: dict, *, allow: Collection[str] = ()) -> None:
    """拒绝 updates（exclude_unset 后的字典）中除 allow 外的显式 null。

    与 #493 申赎侧先例同口径（#573 起申赎也改用本实现，全仓单一收口）：
    显式 null 会被静默忽略、静默改写或落库脏数据，故统一拒绝并提示改为不传该字段。
    """
    if isinstance(allow, str):
        # `f not in allow` 对 str 会退化成子串匹配（allow="notes" 时 "note"/"e"/"s"
        # 全被放行）——正是本收口要消灭的静默放行形态，且 AST 守门只认字面量集合挡不住
        raise TypeError("allow 必须是字段名集合；传 str 会退化为子串匹配")
    null_fields = sorted(f for f, v in updates.items() if f not in allow and v is None)
    if null_fields:
        raise BusinessError(
            "INVALID_PARAM",
            f"字段不可为空: {', '.join(null_fields)}",
        )
