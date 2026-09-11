"""
精度量化工具（份额 / 金额 / 净值）

精度体系与舍入模式（#428 起显式承诺，此前 4 位口径只是走 Decimal 缺省的 HALF_EVEN）：

- 份额 2 位小数（`quantize_shares`）、金额 2 位小数（`quantize_amount`）、
  净值·市值·成本价 4 位小数（`quantize_nav`）——**三者舍入模式均为 ROUND_HALF_UP**
  （四舍五入，末位后一位 >= 5 进位）。
- 负数语义三者相同：与正数对称，按绝对值四舍五入、远离零进位
  （如 -1.235 → -1.24，-1.23445 → -1.2345）。
- 场外基金行业惯例即四舍五入，由此产生的微小误差计入基金财产
  （份额 issue #87、金额 issue #94、4 位口径统一 issue #428）。

量化职责在「产生点」（写入路径）：
- 份额：申购确认份额（amount / nav）、调仓买入份额（amount / price）、
  卖出/赎回的用户输入份额（先量化到 2 位再做精确校验）、
  份额变动事件的 shares_change / shares_after 计算
- 金额：卖出/赎回确认金额（shares × nav）、买入金额与手续费的用户输入、
  申赎金额、现金分红 cash_change（entitlement_shares × div_cash）、
  forced_adjustment 用户填写 cash_change、manual_market_value 写入、
  现金转移金额
- 净值/估值：组合快照三表的 `total_value` / `unit_price` /
  `unit_price_change_pct` / `in_transit_total` 与投资人 `cost_per_share`
  构造点（按列标度 4 位）、场外确认时传入价与 T 日净值的对账比较

**「金额 → 份额」的转换必须先量化金额到分**（#425）：`reinvest_dividend`（分红再投资）
是全系统唯一此类场景——先 `quantize_amount(权益份额 × div_cash)` 定出应得红利，再除以
`reinvest_nav` 并 `quantize_shares`。跳过中间金额量化会把金额量化损失（< 0.005 元）
带进份额，跨四舍五入边界时与基金公司台账差 0.01 份（5377.61 × 0.0119 / 1.0899：
两步得 58.71 = 基金公司实际，一步得 58.72）。现金分红本就走金额量化，两者同口径。
其余事件类型（share_split / share_merge / bonus_share）是纯「份额 × 比例」，不涉及金额。

读取/累加/校验路径不量化：2 位小数的份额/金额相加减仍是 2 位小数，
可用份额/可用现金比较保持精确（不引入容差）；4 位口径是估值/展示口径，
不进现金账本。
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Union

SHARES_QUANT = Decimal("0.01")
AMOUNT_QUANT = Decimal("0.01")
# 净值/市值/成本价的列标度（4 位）。抽成常量 + helper 的理由见 quantize_nav docstring。
NAV_QUANT = Decimal("0.0001")


def quantize_shares(value: Optional[Union[Decimal, float, int, str]]) -> Optional[Decimal]:
    """将份额量化为 2 位小数（ROUND_HALF_UP，四舍五入）。

    负数语义：与正数对称，按绝对值四舍五入、远离零进位
    （如 1.235 → 1.24，-1.235 → -1.24，-1.234 → -1.23）。

    None 原样返回，方便可空字段直接透传。
    """
    if value is None:
        return None
    return Decimal(str(value)).quantize(SHARES_QUANT, rounding=ROUND_HALF_UP)


def quantize_amount(value: Optional[Union[Decimal, float, int, str]]) -> Optional[Decimal]:
    """将金额量化为 2 位小数（ROUND_HALF_UP，四舍五入）。

    金额口径与真实交易平台一致（分），量化误差（< 0.005）计入基金财产。
    负数语义与 quantize_shares 相同：按绝对值四舍五入、远离零进位
    （如 7537.43952 → 7537.44，-1.235 → -1.24）。

    None 原样返回，方便可空字段直接透传。
    """
    if value is None:
        return None
    return Decimal(str(value)).quantize(AMOUNT_QUANT, rounding=ROUND_HALF_UP)


def quantize_nav(value: Optional[Union[Decimal, float, int, str]]) -> Optional[Decimal]:
    """将净值/市值/成本价量化为 4 位小数（ROUND_HALF_UP，四舍五入）。

    **为什么需要这个 helper**（#428）：4 位是估值/展示口径，此前 7 处调用点各自写
    `Decimal("0.0001")` 且不传 `rounding=`，实际走的是 Decimal 上下文缺省的
    ROUND_HALF_EVEN（银行家舍入）——与本模块声称的 HALF_UP 在「恰好第 5 位为 5」
    的边界值上结果不同（如 1.23445 → HALF_UP 1.2345 / HALF_EVEN 1.2344）。抽成与
    2 位 helper 对称的单一入口后，口径由本模块一处定义，第 8 个站点不会再凭缺省
    行为漂移。

    负数语义与 quantize_shares / quantize_amount 相同：按绝对值四舍五入、远离零进位
    （如 -1.23445 → -1.2345）。

    None 原样返回，方便可空字段直接透传。
    """
    if value is None:
        return None
    return Decimal(str(value)).quantize(NAV_QUANT, rounding=ROUND_HALF_UP)
