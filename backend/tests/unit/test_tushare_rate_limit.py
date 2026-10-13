"""
Tushare 客户端限流增强测试（P5.1）

验证：
- per-API-call sleep（每次 pro.xxx() 调用前 sleep）
- 限频退避（10s→30s→60s 指数退避）
- 重试耗尽后抛 TushareAPIError
- fund_daily 行映射：平盘日 pct_chg=0.0 不被 truthiness 折成缺值（#692）
- fund_nav 行映射：unit_nav/accum_nav 的 0.0 同样不折成缺值，判正交给写入侧（#701）
"""
import math

import pytest
from unittest.mock import patch, MagicMock
from app.services import tushare_client
from app.services.tushare_client import TushareAPIError


class TestPerApiCallSleep:
    """验证每次 API 调用前都 sleep"""

    @patch("app.services.tushare_client.time.sleep")
    @patch("app.services.tushare_client._get_tushare_pro")
    def test_sleep_before_each_api_call(self, mock_get_pro, mock_sleep):
        """每次 pro.fund_daily() 调用前应 sleep（per-API-call 粒度）"""
        mock_pro = MagicMock()
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.iterrows.return_value = []
        mock_pro.fund_daily.return_value = mock_df
        mock_get_pro.return_value = mock_pro

        tushare_client.get_fund_daily("510300.SH", "20250101", "20250110")

        sleep_count = mock_sleep.call_count
        assert sleep_count >= 1, "至少调用一次 sleep（per-API-call）"

    @patch("app.services.tushare_client.time.sleep")
    @patch("app.services.tushare_client._get_tushare_pro")
    def test_sleep_count_equals_retry_count_on_success(self, mock_get_pro, mock_sleep):
        """成功路径：sleep 次数 == pro 调用次数（1次调用 1次 sleep）"""
        mock_pro = MagicMock()
        mock_df = MagicMock()
        mock_df.empty = False
        mock_df.iterrows.return_value = []
        mock_pro.fund_daily.return_value = mock_df
        mock_get_pro.return_value = mock_pro

        tushare_client.get_fund_daily("510300.SH", None, None)

        assert mock_sleep.call_count == 1, "成功路径 sleep 1次"


class TestRateLimitBackoff:
    """验证限频退避（10→30→60s）"""

    @patch("app.services.tushare_client.time.sleep")
    @patch("app.services.tushare_client._get_tushare_pro")
    def test_rate_limit_error_triggers_backoff(self, mock_get_pro, mock_sleep):
        """限频错误触发 10→30→60s 退避"""
        mock_pro = MagicMock()
        mock_pro.fund_daily.side_effect = Exception("操作频率超限，每分钟最多200次")
        mock_get_pro.return_value = mock_pro

        try:
            tushare_client.get_fund_daily("510300.SH", None, None)
            assert False, "应抛 TushareAPIError"
        except TushareAPIError:
            pass

        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        backoff_calls = [s for s in sleep_calls if s in (10, 30, 60)]
        assert len(backoff_calls) >= 1, f"应有限频退避调用，got: {sleep_calls}"

    @patch("app.services.tushare_client.time.sleep")
    @patch("app.services.tushare_client._get_tushare_pro")
    def test_network_error_uses_exponential_backoff(self, mock_get_pro, mock_sleep):
        """网络错误用 1→2→4s 退避（非限频退避 10→30→60）"""
        mock_pro = MagicMock()
        mock_pro.fund_daily.side_effect = ConnectionError("network timeout")
        mock_get_pro.return_value = mock_pro

        try:
            tushare_client.get_fund_daily("510300.SH", None, None)
            assert False, "应抛 TushareAPIError"
        except TushareAPIError:
            pass

        sleep_calls = [c.args[0] for c in mock_sleep.call_args_list]
        # 首次 sleep 是 rate_interval（0.5s），后续是退避
        backoff_calls = [s for s in sleep_calls if s in (1, 2)]
        assert len(backoff_calls) >= 1, f"应有网络退避调用（1s 或 2s），got: {sleep_calls}"


class TestMaxRetriesExhausted:
    """验证重试耗尽后抛 TushareAPIError"""

    @patch("app.services.tushare_client.time.sleep")
    @patch("app.services.tushare_client._get_tushare_pro")
    def test_all_retries_exhausted_raises_error(self, mock_get_pro, mock_sleep):
        """连续失败后抛 TushareAPIError"""
        mock_pro = MagicMock()
        mock_pro.fund_daily.side_effect = Exception("持续失败")
        mock_get_pro.return_value = mock_pro

        with pytest.raises(TushareAPIError, match="获取基金日线行情失败"):
            tushare_client.get_fund_daily("510300.SH", None, None)

    @patch("app.services.tushare_client.time.sleep")
    @patch("app.services.tushare_client._get_tushare_pro")
    def test_succeeds_after_retry(self, mock_get_pro, mock_sleep):
        """首次失败后重试成功"""
        from app.config import get_settings
        get_settings.cache_clear()

        mock_pro = MagicMock()
        mock_df = MagicMock()
        mock_df.empty = True
        mock_pro.fund_daily.side_effect = [ConnectionError("timeout"), mock_df]
        mock_get_pro.return_value = mock_pro

        result = tushare_client.get_fund_daily("510300.SH", None, None)
        assert result == [], "重试成功后返回空列表"

        get_settings.cache_clear()


class TestFundDailyRowMapping:
    """#692 附带项：同一字段的 `0.0` 与「上游没给」必须可区分。

    平盘日 `pct_chg` 恰为 0.0，被 truthiness（`if row.get(...)`）折成 None 之后，
    前端读成「无数据」而不是 0%——场内平盘日并不罕见，这是一列会长期显示错的展示字段。
    """

    @staticmethod
    def _frame(rows):
        df = MagicMock()
        df.empty = False
        df.iterrows.return_value = list(enumerate(rows))
        return df

    @patch("app.services.tushare_client.time.sleep")
    @patch("app.services.tushare_client._get_tushare_pro")
    def test_flat_day_zero_pct_is_preserved(self, mock_get_pro, mock_sleep):
        mock_pro = MagicMock()
        mock_pro.fund_daily.return_value = self._frame([
            {"trade_date": "20260915", "close": 4.6, "pre_close": 4.6, "pct_chg": 0.0},
        ])
        mock_get_pro.return_value = mock_pro

        assert tushare_client.get_fund_daily("510300.SH", "20260915", "20260915") == [
            {"trade_date": "20260915", "close": 4.6, "pre_close": 4.6, "pct_chg": 0.0}
        ]

    @patch("app.services.tushare_client.time.sleep")
    @patch("app.services.tushare_client._get_tushare_pro")
    def test_missing_and_zero_close_are_also_distinguished(self, mock_get_pro, mock_sleep):
        """同族三列一起收口：`close`/`pre_close` 的 0.0 同样不得被折成缺值。"""
        mock_pro = MagicMock()
        mock_pro.fund_daily.return_value = self._frame([
            {"trade_date": "20260915", "close": 0.0, "pre_close": None, "pct_chg": None},
        ])
        mock_get_pro.return_value = mock_pro

        assert tushare_client.get_fund_daily("510300.SH", "20260915", "20260915") == [
            {"trade_date": "20260915", "close": 0.0, "pre_close": None, "pct_chg": None}
        ]


class TestFundNavRowMapping:
    """#701：tushare 场外的 `unit_nav`/`accum_nav` 不再用 truthiness 折 0。

    这一度是全路径上唯一挡住 0 价的地方（`0.0` 静默变成「上游没给」）。判正收口移到
    写入侧 `_usable_nav` 之后，本层只回答「上游给没给值」——坏值于是能一路走到那道
    唯一判据并被 WARNING 点名，比在这里无声消失可查。

    本组用例只钉**取数层输出形态**，不钉终态：`0.0`/NaN 落库与否由
    `test_sync_product_prices.py::TestUnusablePriceFiltered` 断言，两层各钉各的，
    改判据时才会有一处变红并指向该改的那处。
    """

    @staticmethod
    def _frame(rows):
        df = MagicMock()
        df.empty = False
        df.iterrows.return_value = list(enumerate(rows))
        return df

    @patch("app.services.tushare_client.time.sleep")
    @patch("app.services.tushare_client._get_tushare_pro")
    def test_zero_nav_is_preserved_not_folded_into_missing(self, mock_get_pro, mock_sleep):
        mock_pro = MagicMock()
        mock_pro.fund_nav.return_value = self._frame([
            {"nav_date": "20260915", "unit_nav": 0.0, "accum_nav": 0.0},
        ])
        mock_get_pro.return_value = mock_pro

        assert tushare_client.get_fund_nav("000300.OF", "20260915", "20260915") == [
            {"trade_date": "20260915", "unit_nav": 0.0, "accum_nav": 0.0}
        ]

    @patch("app.services.tushare_client.time.sleep")
    @patch("app.services.tushare_client._get_tushare_pro")
    def test_missing_and_zero_nav_are_distinguished(self, mock_get_pro, mock_sleep):
        """pandas 缺值是 NaN 不是 None：`is not None` 让它以 NaN 下传，终态仍由写侧判定。"""
        nan = float("nan")
        mock_pro = MagicMock()
        mock_pro.fund_nav.return_value = self._frame([
            {"nav_date": "20260915", "unit_nav": nan, "accum_nav": None},
            {"nav_date": "20260916", "unit_nav": 1.5, "accum_nav": 0.0},
        ])
        mock_get_pro.return_value = mock_pro

        result = tushare_client.get_fund_nav("000300.OF", "20260915", "20260916")

        assert result[0]["trade_date"] == "20260915"
        assert math.isnan(result[0]["unit_nav"]), "缺值不再被折成 None，交给写侧判坏"
        assert result[0]["accum_nav"] is None
        assert result[1] == {"trade_date": "20260916", "unit_nav": 1.5, "accum_nav": 0.0}
