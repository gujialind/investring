"""akshare 场外分红取数器 `get_fund_div_otc`（#881）。

上游全 mock（不触网）：列结构 / 每10份÷10 / 无效行归一 / 整段漂移响亮失败 /
pay_date 归一保留（#887 PR2 起检测器消费，取数器归一逻辑不变）。检测器侧集成（源路由、去重、同日、留痕、A11 回归）在
`test_detect_dividends.py::TestAkshareSource`。

实证依据（2026-11，只读实测）：000001/000005 列为 年份/权益登记日/除息日/
每10份分红/分红发放日（全字符串列）；金额形如「每10份派现金0.0180元」
（÷10 → 0.0018，与 tushare fund_div 逐条一致）；该接口只列已实施分红
（000001 含 2007 年分红预案公告历史，详情仍只有 25 行实施记录）。
"""
import pandas as pd
import pytest

from app.services import akshare_client
from app.services.akshare_client import AkshareAPIError, get_fund_div_otc

COLUMNS = ["年份", "权益登记日", "除息日", "每10份分红", "分红发放日"]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """限流与退避都是真 sleep，测试里没必要等（同 test_akshare_client）"""
    monkeypatch.setattr(akshare_client, "_rate_limit_sleep", lambda: None)
    monkeypatch.setattr(akshare_client.time, "sleep", lambda seconds: None)


def _row(rec="2026-10-19", ex="2026-10-20", amt="每10份派现金0.0180元",
         pay="2026-10-21", year="2026年"):
    return {"年份": year, "权益登记日": rec, "除息日": ex, "每10份分红": amt, "分红发放日": pay}


def _patch(monkeypatch, rows, columns=COLUMNS):
    df = pd.DataFrame(rows, columns=columns)
    calls = []

    def fake_em(**kwargs):
        calls.append(kwargs)
        return df

    monkeypatch.setattr("akshare.fund_open_fund_info_em", fake_em)
    return calls


class TestNormalization:
    def test_valid_row_normalized_and_per_ten_divided(self, monkeypatch):
        calls = _patch(monkeypatch, [_row()])
        rows = get_fund_div_otc("000001.OF")
        assert calls == [{"symbol": "000001", "indicator": "分红送配详情"}]
        assert rows == [{
            "ex_date": "20261020",
            "record_date": "20261019",
            "div_cash": pytest.approx(0.0018),
            "pay_date": "20261021",
        }]

    def test_pay_date_retained_not_consumed(self, monkeypatch):
        # 取数器归一键固定四个，不从中派生任何事件日期（取数器不消费 pay_date）；
        # 检测器消费 pay_date 属 #887 PR2（explicit 配置命中时），取数器归一逻辑不变。
        _patch(monkeypatch, [_row()])
        rows = get_fund_div_otc("000001")
        assert set(rows[0]) == {"ex_date", "record_date", "div_cash", "pay_date"}

    def test_compact_date_form_accepted(self, monkeypatch):
        _patch(monkeypatch, [_row(rec="20261019", ex="20261020", pay="20261021")])
        rows = get_fund_div_otc("000001")
        assert rows[0]["ex_date"] == "20261020"
        assert rows[0]["record_date"] == "20261019"
        assert rows[0]["pay_date"] == "20261021"

    def test_zero_amount_preserved_for_detector_gate(self, monkeypatch):
        # 0 原样保留：非正由检测器 #771 闸门判（与 tushare _div_cash_or_none 同分工）
        _patch(monkeypatch, [_row(amt="每10份派现金0.0000元")])
        assert get_fund_div_otc("000001")[0]["div_cash"] == 0.0

    @pytest.mark.parametrize("amt", [
        "每10份送3股",          # 非现金形态（送股/拆分）
        "每10份派现金abc元",     # 解析失败
        "",                     # 缺失
        "每10份派现金nan元",     # 非有限
    ], ids=["bonus_shares", "unparseable", "empty", "nan"])
    def test_invalid_amount_normalized_to_none(self, monkeypatch, amt):
        _patch(monkeypatch, [_row(amt=amt)])
        rows = get_fund_div_otc("000001")
        assert rows[0]["div_cash"] is None
        # 日期保留：检测器可按 (产品, 除息日) 留痕
        assert rows[0]["ex_date"] == "20261020"

    def test_missing_pay_date_invalidates_row(self, monkeypatch):
        # 实施分红必有发放日；缺失视为非实施/无效行（预案防线），
        # 金额归一 None 交检测器跳过留痕
        _patch(monkeypatch, [_row(pay="")])
        rows = get_fund_div_otc("000001")
        assert rows[0]["div_cash"] is None
        assert rows[0]["pay_date"] is None

    def test_missing_record_date_keeps_ex_for_trace(self, monkeypatch):
        _patch(monkeypatch, [_row(rec="")])
        rows = get_fund_div_otc("000001")
        assert rows[0]["record_date"] == ""
        assert rows[0]["div_cash"] is None
        assert rows[0]["ex_date"] == "20261020"


class TestRowScreening:
    def test_unparseable_ex_date_row_dropped_others_kept(self, monkeypatch):
        _patch(monkeypatch, [
            _row(ex="元"),
            _row(rec="2026-10-21", ex="2026-10-22", pay="2026-10-23"),
        ])
        rows = get_fund_div_otc("000001")
        assert len(rows) == 1
        assert rows[0]["ex_date"] == "20261022"

    def test_all_ex_dates_unparseable_raises(self, monkeypatch):
        _patch(monkeypatch, [_row(ex="元"), _row(ex="NaT")])
        with pytest.raises(AkshareAPIError, match="整段不可解析"):
            get_fund_div_otc("000001")

    def test_missing_required_column_raises(self, monkeypatch):
        _patch(monkeypatch, [_row()],
               columns=["年份", "权益登记日", "除息日", "每10份分红"])
        with pytest.raises(AkshareAPIError, match="缺少必需列"):
            get_fund_div_otc("000001")

    def test_empty_upstream_is_legal_empty(self, monkeypatch):
        _patch(monkeypatch, [])
        assert get_fund_div_otc("000001") == []

    def test_upstream_failure_retries_then_raises(self, monkeypatch):
        calls = []

        def fake_em(**kwargs):
            calls.append(kwargs)
            raise RuntimeError("connection reset")

        monkeypatch.setattr("akshare.fund_open_fund_info_em", fake_em)
        with pytest.raises(AkshareAPIError, match="获取场外基金分红失败"):
            get_fund_div_otc("000001")
        assert len(calls) == akshare_client.get_settings().akshare_max_retries