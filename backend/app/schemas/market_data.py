from pydantic import BaseModel
from datetime import date
from typing import List, Literal, Optional


class PriceDataResponse(BaseModel):
    product_code: str
    market: str
    price_date: date
    unit_price: float

    class Config:
        from_attributes = True


class PriceDataSyncRequest(BaseModel):
    start_date: date
    end_date: date


# --- #595 产品详情页净值数据（§5.3）：历史净值分页 + 区间曲线/区间收益率 ---


class NavHistoryItem(BaseModel):
    """历史净值行：单位净值必有；累计净值与日涨跌可空，两者成因不同（#692 更正归因）。

    累计净值可空：场内行情源不提供该字段；场外源（tushare 与 akshare，#704）逐日有值；
    港互认上游 `ACCNAV` 逐基金分档（#703），缺值基金整列为空。净值曲线与区间收益率
    优先按累计净值算，整列缺值时回退单位净值（见 `ProductNavAnalysis.nav_basis`）。
    日涨跌可空：该产品首条记录无基准，或上游未提供且本地不满足推导条件（基准缺失/非正）。
    日涨跌为百分数（1.2195 即 1.2195%），在写侧产生点量化到 4 位。
    """

    price_date: date
    unit_price: float
    accumulated_nav: Optional[float] = None
    pct_change: Optional[float] = None


class NavHistoryPage(BaseModel):
    """历史净值分页（日期降序）。

    信封字段与全仓 10 个 Paginated*Response 对齐（items/total/page/page_size，
    #637 L2 评审 S1）：消费方读响应即可知道自己落在第几页，无需回记请求参数。
    """

    items: List[NavHistoryItem]
    total: int
    page: int
    page_size: int


class NavCurvePoint(BaseModel):
    """净值曲线点。

    字段名沿用 `accumulated_nav`（#703 取舍：改名 `nav` 更诚实，但会同时扩大前端与
    CLI 字段面的改动，契约收益不值）——`nav_basis="unit"` 时装的是单位净值，场内产品
    即收盘价（市价）。
    """

    date: date
    accumulated_nav: float


class ProductIntervalReturns(BaseModel):
    """区间收益率（百分数，4dp）。历史不足窗口期返回 None，前端显示占位。"""

    m1: Optional[float] = None
    m3: Optional[float] = None
    m6: Optional[float] = None
    y1: Optional[float] = None
    ytd: Optional[float] = None
    all: Optional[float] = None


class ProductNavAnalysis(BaseModel):
    """产品净值分析：所选区间净值曲线 + 六窗区间收益率（同一份序列算，口径一致）。

    `nav_basis`（#703）标明这批数字按哪一列算：`accumulated` = 累计净值（含分红再投资，
    原口径）；`unit` = 单位净值，即该产品整段历史都没有累计净值时的回退口径（港互认
    缺值基金、场内行情源），此时收益率**不含分红再投资**、场内为市价涨跌，前端须明示。
    两者皆空时曲线为空、六窗全 None，`nav_basis` 仍报 `accumulated`（空态与口径无关）。
    """

    curve: List[NavCurvePoint]
    interval_returns: ProductIntervalReturns
    nav_basis: Literal["accumulated", "unit"]
