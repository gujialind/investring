"""产品显示码（issue #705）：人眼口径的标准代码，与库内机器码分离。

库内 `product.code` 是**取数/主键口径**：tushare 带交易所后缀（510300.SH）、
港互认存 10 位香港基金代码（1001767344）。用户认知里的代码是内地销售代码
（510300 / 968050），两者不一致会造成「界面上查不到自己买的基金」。

显示码只服务展示与检索提示，**不**进入主键、外键、取数入参与 CLI 入参
（CLI 入参归一化被 #705 明确否决：6 位码对 LOF 一码多市场有歧义）。
"""
from typing import Optional

# 港互认市场标识（与 product.market 取值同字面量，全仓无枚举常量）
HK_MUTUAL_MARKET = "HK_MUTUAL"


def display_code(
    code: str, market: Optional[str], hk_sale_code: Optional[str] = None
) -> str:
    """计算显示码（单一实现，读侧/契约/迁移共用）。

    - 港互认：取入库的 6 位内地销售代码 `hk_sale_code`；映射不可得（NULL）
      回退原始 10 位码——不报错、不返回空串（#705 验收：空码比原码更糟）
    - 其余市场：剥 tushare 交易所后缀（510300.SH → 510300）；CASH /
      IN_TRANSIT_* 等无点系统码原样透传（剥后缀对其是 no-op，无需特判）
    """
    if not code:
        return code
    if market == HK_MUTUAL_MARKET:
        return hk_sale_code or code
    return code.split(".")[0]
