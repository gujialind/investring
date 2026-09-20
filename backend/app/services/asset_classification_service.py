"""资产分类维度字典管理服务（issue #135）

字典查看/新建/编辑（无删除，is_active 软失效替代）。校验规则：
- code 前缀与 dimension 匹配（ASSET_/REGION_/STYLE_/SIZE_/SEG_，全大写）；
- 非 asset_class 维度必须 ≥1 适用大类；关联目标须为 asset_class 维度值，
  且该大类的维度规则允许该维度（禁止把 segment 值挂到全禁 segment 的大类）；
- 关联移除引用保护：存在产品（该大类）引用该值 → DIMENSION_VALUE_IN_USE；
- 规则收紧保护：→required 需该大类存量产品该维度全非空，→forbidden（删规则行）
  需全为空，否则 DIMENSION_RULE_CONFLICT；放宽自由；forbidden 化不级联删值关联
  （dormant，规则回转自动复活）；
- code/dimension 不可改（schema 层不收这两列）。

service 层只抛领域异常，不 commit。
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.constants.asset_dimensions import RULE_DIMENSIONS, RULES
from app.models.asset_classification import (
    AssetClassification,
    AssetClassDimensionRule,
    AssetDimensionApplicability,
)
from app.models.product import Product
from app.services.exceptions import BusinessError, NotFoundError
from app.services.null_guard import reject_explicit_nulls

DIMENSIONS = ("asset_class", "region", "style", "size", "segment")
_CODE_PREFIX_OF_DIMENSION = {
    "asset_class": "ASSET_",
    "region": "REGION_",
    "style": "STYLE_",
    "size": "SIZE_",
    "segment": "SEG_",
}
# 维度 → 产品字段（引用保护/规则收紧查询用）
_FIELD_OF_DIMENSION = {
    "region": "region_code",
    "style": "style_code",
    "size": "size_code",
    "segment": "segment_code",
}


def get_classification(db: Session, code: str) -> AssetClassification:
    ac = db.query(AssetClassification).filter(AssetClassification.code == code).first()
    if not ac:
        raise NotFoundError("NOT_FOUND", f"维度值 {code} 不存在")
    return ac


def _dedupe(classes: list[str]) -> list[str]:
    return list(dict.fromkeys(classes))


def _validate_applicable_classes(
    db: Session, *, dimension: str, classes: list[str]
) -> None:
    """关联目标校验：存在、是 asset_class 维度值、其维度规则允许该维度"""
    for asset_class in classes:
        target = db.query(AssetClassification).filter(
            AssetClassification.code == asset_class
        ).first()
        if not target or target.dimension != "asset_class":
            raise BusinessError(
                "INVALID_CLASSIFICATION",
                f"适用大类 {asset_class} 不存在或不是 asset_class 维度值",
                details={"asset_class_code": asset_class},
            )
        ruled = {
            row.dimension
            for row in db.query(AssetClassDimensionRule).filter(
                AssetClassDimensionRule.asset_class_code == asset_class
            )
        }
        if dimension not in ruled:
            raise BusinessError(
                "INVALID_CLASSIFICATION",
                f"大类 {asset_class} 禁止 {dimension} 维度，无法关联",
                details={"asset_class_code": asset_class, "dimension": dimension},
            )


def _validate_rules(rules: dict[str, str]) -> None:
    for dimension, rule in rules.items():
        if dimension not in RULE_DIMENSIONS or rule not in RULES:
            raise BusinessError(
                "INVALID_CLASSIFICATION",
                f"非法维度规则 {dimension}={rule}（维度限 {RULE_DIMENSIONS}，规则限 {RULES}）",
                details={"dimension": dimension, "rule": rule},
            )


def create_classification(
    db: Session,
    *,
    code: str,
    dimension: str,
    name: str,
    sort_order: int = 0,
    description: Optional[str] = None,
    applicable_asset_classes: Optional[list[str]] = None,
    dimension_rules: Optional[dict[str, str]] = None,
) -> AssetClassification:
    """新建维度值（含 asset_class 大类 + 规则）。不 commit。"""
    if dimension not in DIMENSIONS:
        raise BusinessError(
            "INVALID_CLASSIFICATION",
            f"非法 dimension {dimension}（限 {'/'.join(DIMENSIONS)}）",
            details={"dimension": dimension},
        )
    prefix = _CODE_PREFIX_OF_DIMENSION[dimension]
    if not code or code != code.upper() or not code.startswith(prefix):
        raise BusinessError(
            "INVALID_CLASSIFICATION",
            f"code 须为全大写且以 {prefix} 开头（dimension={dimension}）",
            details={"code": code, "dimension": dimension},
        )
    if db.query(AssetClassification).filter(AssetClassification.code == code).first():
        raise BusinessError("ALREADY_EXISTS", f"维度值 {code} 已存在", http_status=400)

    ac = AssetClassification(
        code=code, dimension=dimension, name=name,
        sort_order=sort_order, description=description, is_active=True,
    )
    db.add(ac)
    # 先落父行再插关联/规则子行（两表与 asset_classification 无 relationship，
    # UOW 不保证跨表 flush 顺序，FK RESTRICT 下须显式 flush）
    db.flush()

    if dimension == "asset_class":
        if applicable_asset_classes:
            raise BusinessError(
                "INVALID_CLASSIFICATION",
                "asset_class 维度值不参与适用关联",
                details={"code": code},
            )
        rules = dimension_rules or {}
        _validate_rules(rules)
        for dim, rule in rules.items():
            db.add(AssetClassDimensionRule(
                asset_class_code=code, dimension=dim, rule=rule,
            ))
    else:
        classes = _dedupe(applicable_asset_classes or [])
        if not classes:
            raise BusinessError(
                "INVALID_CLASSIFICATION",
                "非 asset_class 维度必须指定至少一个适用 asset_class",
                details={"code": code},
            )
        if dimension_rules is not None:
            raise BusinessError(
                "INVALID_CLASSIFICATION",
                "仅 asset_class 维度值可携带 dimension_rules",
                details={"code": code},
            )
        _validate_applicable_classes(db, dimension=dimension, classes=classes)
        for asset_class in classes:
            db.add(AssetDimensionApplicability(
                dimension_value_code=code, asset_class_code=asset_class,
            ))
    return ac


def _referencing_products(
    db: Session, *, value: AssetClassification, asset_class: str
) -> list[str]:
    """引用 (值, 大类) 组合的产品 code 列表（关联移除引用保护用）"""
    field = _FIELD_OF_DIMENSION[value.dimension]
    column = getattr(Product, field)
    rows = db.query(Product.code).filter(
        Product.asset_class_code == asset_class, column == value.code
    ).all()
    return sorted(r[0] for r in rows)


def _apply_rule_changes(
    db: Session, *, ac: AssetClassification, new_rules: dict[str, str]
) -> None:
    """规则全量替换（收紧保护：→required 需存量全非空，→forbidden 需存量全空）"""
    old_rules = {
        row.dimension: row.rule
        for row in db.query(AssetClassDimensionRule).filter(
            AssetClassDimensionRule.asset_class_code == ac.code
        )
    }
    for dimension in RULE_DIMENSIONS:
        old_rule, new_rule = old_rules.get(dimension), new_rules.get(dimension)
        if old_rule == new_rule:
            continue
        column = getattr(Product, _FIELD_OF_DIMENSION[dimension])
        if new_rule == "required" and old_rule != "required":
            refs = sorted(
                r[0] for r in db.query(Product.code).filter(
                    Product.asset_class_code == ac.code, column.is_(None)
                ).all()
            )
            if refs:
                raise BusinessError(
                    "DIMENSION_RULE_CONFLICT",
                    f"{ac.code}.{dimension} 收紧为必填失败：{len(refs)} 个存量产品该维度为空",
                    details={
                        "asset_class_code": ac.code, "dimension": dimension,
                        "rule": new_rule, "products": refs,
                    },
                )
        if new_rule is None:  # → forbidden（删规则行）
            refs = sorted(
                r[0] for r in db.query(Product.code).filter(
                    Product.asset_class_code == ac.code, column.isnot(None)
                ).all()
            )
            if refs:
                raise BusinessError(
                    "DIMENSION_RULE_CONFLICT",
                    f"{ac.code}.{dimension} 收紧为禁止失败：{len(refs)} 个存量产品该维度非空",
                    details={
                        "asset_class_code": ac.code, "dimension": dimension,
                        "rule": "forbidden", "products": refs,
                    },
                )
    # 校验通过，应用变更（forbidden 不级联删值关联，dormant 链接规则回转自动复活）
    for dimension in RULE_DIMENSIONS:
        old_rule, new_rule = old_rules.get(dimension), new_rules.get(dimension)
        if old_rule == new_rule:
            continue
        if old_rule is not None:
            db.query(AssetClassDimensionRule).filter_by(
                asset_class_code=ac.code, dimension=dimension
            ).delete()
        if new_rule is not None:
            db.add(AssetClassDimensionRule(
                asset_class_code=ac.code, dimension=dimension, rule=new_rule,
            ))


def update_classification(
    db: Session, *, code: str, updates: dict
) -> AssetClassification:
    """更新维度值（name/sort_order/description/is_active/适用关联/维度规则）。不 commit。

    updates 为 exclude_unset 后的字典；applicable_asset_classes / dimension_rules
    为全量替换语义。不传 = 不动；显式 null 一律拒绝（issue #573：sort_order/name/
    is_active 落 NULL 会让响应模型 500 且该行 GET 恒 500；dimension_rules 显式 null
    此前静默清空全部规则，与「不传 = 不动」矛盾）。description 列可空且响应
    Optional，null = 清除描述。
    """
    ac = get_classification(db, code)
    updates = dict(updates)
    reject_explicit_nulls(updates, allow={"description"})

    if "applicable_asset_classes" in updates:
        if ac.dimension == "asset_class":
            raise BusinessError(
                "INVALID_CLASSIFICATION",
                "asset_class 维度值不参与适用关联",
                details={"code": code},
            )
        new_classes = _dedupe(updates.pop("applicable_asset_classes") or [])
        if not new_classes:
            raise BusinessError(
                "INVALID_CLASSIFICATION",
                "非 asset_class 维度必须保留至少一个适用 asset_class",
                details={"code": code},
            )
        _validate_applicable_classes(db, dimension=ac.dimension, classes=new_classes)
        old_rows = db.query(AssetDimensionApplicability).filter(
            AssetDimensionApplicability.dimension_value_code == code
        ).all()
        old_classes = {row.asset_class_code for row in old_rows}
        removed = old_classes - set(new_classes)
        for asset_class in sorted(removed):
            refs = _referencing_products(db, value=ac, asset_class=asset_class)
            if refs:
                raise BusinessError(
                    "DIMENSION_VALUE_IN_USE",
                    f"维度值 {code} 仍被 {len(refs)} 个 {asset_class} 产品引用，无法移除该关联",
                    details={
                        "code": code, "asset_class_code": asset_class, "products": refs,
                    },
                )
        for row in old_rows:
            if row.asset_class_code in removed:
                db.delete(row)
        for asset_class in new_classes:
            if asset_class not in old_classes:
                db.add(AssetDimensionApplicability(
                    dimension_value_code=code, asset_class_code=asset_class,
                ))

    if "dimension_rules" in updates:
        if ac.dimension != "asset_class":
            raise BusinessError(
                "INVALID_CLASSIFICATION",
                "仅 asset_class 维度值可携带 dimension_rules",
                details={"code": code},
            )
        new_rules = updates.pop("dimension_rules") or {}
        _validate_rules(new_rules)
        _apply_rule_changes(db, ac=ac, new_rules=new_rules)

    for field in ("name", "sort_order", "description", "is_active"):
        if field in updates:
            setattr(ac, field, updates[field])
    return ac


def get_applicable_classes(db: Session, code: str) -> list[str]:
    """单条详情的适用大类（按大类 sort_order 排序，code 兜底）"""
    class_order = dict(
        db.query(AssetClassification.code, AssetClassification.sort_order)
        .filter(AssetClassification.dimension == "asset_class")
        .all()
    )
    classes = [
        row.asset_class_code
        for row in db.query(AssetDimensionApplicability).filter(
            AssetDimensionApplicability.dimension_value_code == code
        )
    ]
    return sorted(classes, key=lambda c: (class_order.get(c, 999), c))


def get_dimension_rules(db: Session, code: str) -> dict[str, str]:
    return {
        row.dimension: row.rule
        for row in db.query(AssetClassDimensionRule).filter(
            AssetClassDimensionRule.asset_class_code == code
        )
    }
