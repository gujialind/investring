"""数据源校验器单测（issue #880）：create/update 共用单一实现，统一 INVALID_DATA_SOURCE。"""
import pytest

from app.services import product_service as ps
from app.services.exceptions import BusinessError


def test_valid_combinations_pass():
    for market in ("CN_OTC", "CN_EXCHANGE"):
        for source in ("tushare", "akshare"):
            ps.validate_data_source(market, "OEF", source)
    ps.validate_data_source("HK_MUTUAL", "OEF", "akshare")


def test_invalid_enum_rejected():
    with pytest.raises(BusinessError) as e:
        ps.validate_data_source("CN_OTC", "OEF", "foo")
    assert e.value.code == "INVALID_DATA_SOURCE"


def test_none_rejected():
    with pytest.raises(BusinessError) as e:
        ps.validate_data_source("CN_OTC", "OEF", None)
    assert e.value.code == "INVALID_DATA_SOURCE"


def test_hk_tushare_rejected():
    with pytest.raises(BusinessError) as e:
        ps.validate_data_source("HK_MUTUAL", "OEF", "tushare")
    assert e.value.code == "INVALID_DATA_SOURCE"


@pytest.mark.parametrize("ptype", ["CASH", "IN_TRANSIT"])
def test_virtual_rejected(ptype):
    with pytest.raises(BusinessError) as e:
        ps.validate_data_source("CN_OTC", ptype, "tushare")
    assert e.value.code == "INVALID_DATA_SOURCE"


def test_empty_market_rejected():
    with pytest.raises(BusinessError) as e:
        ps.validate_data_source("", "OEF", "tushare")
    assert e.value.code == "INVALID_DATA_SOURCE"
    # #880 L2 S5：缺市场归因准确，不得误报成「现金/虚拟产品」
    assert "市场" in e.value.message
    assert "现金" not in e.value.message
