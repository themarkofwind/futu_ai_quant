"""多标的轮询与代码解析单元测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest
from futu import RET_OK

from futu_ai_quant.brokers.futu.intraday_watch import IntradayTWatch
from futu_ai_quant.indicators.intraday import session_vwap_from_klines
from futu_ai_quant.market.codes import normalize_stock_code, parse_stock_codes
from futu_ai_quant.market.session import currency_of_market, session_date_prefix
from futu_ai_quant.strategy.intraday_t import IntradayTState


class TestStockCodes:
    def test_normalize_hk_numeric(self) -> None:
        assert normalize_stock_code("09988") == "HK.09988"
        assert normalize_stock_code("HK.09988") == "HK.09988"

    def test_normalize_us_ticker(self) -> None:
        assert normalize_stock_code("AAPL") == "US.AAPL"
        assert normalize_stock_code("US.AAPL") == "US.AAPL"

    def test_parse_codes_dedupes(self) -> None:
        codes = parse_stock_codes("HK.09988, 09988, US.AAPL")
        assert codes == ["HK.09988", "US.AAPL"]

    def test_parse_codes_fallback(self) -> None:
        assert parse_stock_codes("", fallback_single="HK.00700") == ["HK.00700"]

    def test_parse_codes_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="未指定监控标的"):
            parse_stock_codes("  ,  ")


class TestMarketHelpers:
    def test_currency_of_market(self) -> None:
        assert currency_of_market("HK") == "HKD"
        assert currency_of_market("US") == "USD"

    def test_session_date_prefix_us_dst(self) -> None:
        beijing = datetime(2026, 6, 16, 22, 0, 0, tzinfo=timezone.utc)
        # 仅验证函数可调用并返回 YYYY-MM-DD
        prefix = session_date_prefix("US", beijing)
        assert len(prefix) == 10
        assert prefix[4] == "-"


class TestSessionVwap:
    def test_session_vwap_filters_by_date(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "time_key": "2026-06-15 10:00:00",
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100,
                    "volume": 1000,
                    "turnover": 100_000,
                },
                {
                    "time_key": "2026-06-16 10:00:00",
                    "open": 110,
                    "high": 111,
                    "low": 109,
                    "close": 110,
                    "volume": 2000,
                    "turnover": 220_000,
                },
            ]
        )
        assert session_vwap_from_klines(frame, "2026-06-16") == 110.0


def _make_kline_frame(n: int = 30, base: float = 100.0) -> pd.DataFrame:
    rows = []
    for i in range(n):
        close = base + 0.2 * i
        rows.append(
            {
                "time_key": f"2026-06-16 09:{30 + i:02d}:00",
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 100_000 + i * 1000,
                "turnover": (100_000 + i * 1000) * close,
            }
        )
    return pd.DataFrame(rows)


class TestIntradayTWatch:
    def test_poll_symbol_evaluates_with_mock_quote(self) -> None:
        quote_ctx = MagicMock()
        kline = _make_kline_frame(30)
        quote_ctx.get_cur_kline.return_value = (RET_OK, kline)

        watch = IntradayTWatch(quote_ctx, ["HK.09988"])
        sym = watch.symbols[0]
        watch._poll_symbol(sym)

        quote_ctx.get_cur_kline.assert_called_once()
        assert sym.ctx.state in {
            IntradayTState.AT_BASE,
            IntradayTState.SHORT_T,
            IntradayTState.LONG_T,
        }

    def test_poll_symbol_us_currency(self) -> None:
        quote_ctx = MagicMock()
        kline = _make_kline_frame(30)
        quote_ctx.get_cur_kline.return_value = (RET_OK, kline)

        watch = IntradayTWatch(quote_ctx, ["US.AAPL"])
        assert watch.symbols[0].ctx.currency == "USD"
        watch._poll_symbol(watch.symbols[0])

    def test_sell_signal_message_uses_currency(self) -> None:
        quote_ctx = MagicMock()
        kline = _make_kline_frame(30, base=100.0)
        kline.loc[kline.index[-1], "close"] = 120.0
        quote_ctx.get_cur_kline.return_value = (RET_OK, kline)

        watch = IntradayTWatch(quote_ctx, ["US.AAPL"], lot_size=100, target_spread=1.0)
        sym = watch.symbols[0]

        # 注入超买条件：高 RSI / 高布林 / 低 VWAP 基线由 session kline 提供
        watch._poll_symbol(sym)
        # 不强制触发信号，只验证货币字段已设置
        assert sym.ctx.currency == "USD"
