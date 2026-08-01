"""Futu 市场状态门禁单元测试。"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
from futu import RET_OK

from futu_ai_quant.brokers.futu.market_state import (
    FutuMarketSessionGate,
    is_continuous_market_state,
)


class TestMarketStateHelpers:
    def test_continuous_states(self) -> None:
        assert is_continuous_market_state("MORNING") is True
        assert is_continuous_market_state("AFTERNOON") is True
        assert is_continuous_market_state("CLOSED") is False
        assert is_continuous_market_state("REST") is False
        assert is_continuous_market_state("AUCTION") is False


class TestFutuMarketSessionGate:
    def test_uses_api_morning(self) -> None:
        quote = MagicMock()
        quote.get_market_state.return_value = (
            RET_OK,
            pd.DataFrame(
                [{"code": "HK.01347", "stock_name": "华虹", "market_state": "MORNING"}]
            ),
        )
        gate = FutuMarketSessionGate(quote, ttl_sec=60, fallback_local=False)
        assert gate.is_continuous_trading("HK.01347") is True
        quote.get_market_state.assert_called()

    def test_closed_on_weekday_holiday(self) -> None:
        quote = MagicMock()
        quote.get_market_state.return_value = (
            RET_OK,
            pd.DataFrame(
                [{"code": "HK.01347", "stock_name": "华虹", "market_state": "CLOSED"}]
            ),
        )
        gate = FutuMarketSessionGate(quote, ttl_sec=60, fallback_local=True)
        # 即使本地可能是交易时段，API CLOSED 优先
        assert gate.is_continuous_trading("HK.01347") is False

    def test_cache_avoids_repeat_calls(self) -> None:
        quote = MagicMock()
        quote.get_market_state.return_value = (
            RET_OK,
            pd.DataFrame(
                [{"code": "HK.00700", "stock_name": "腾讯", "market_state": "AFTERNOON"}]
            ),
        )
        gate = FutuMarketSessionGate(quote, ttl_sec=60)
        assert gate.is_continuous_trading("HK.00700") is True
        assert gate.is_continuous_trading("HK.00700") is True
        assert quote.get_market_state.call_count == 1

    def test_fallback_local_when_api_fails(self, monkeypatch) -> None:
        quote = MagicMock()
        quote.get_market_state.return_value = (1, "error")
        gate = FutuMarketSessionGate(quote, ttl_sec=1, fallback_local=True)

        import futu_ai_quant.brokers.futu.market_state as ms

        monkeypatch.setattr(ms, "is_trading_session", lambda market: market == "HK")
        assert gate.is_continuous_trading("HK.01347") is True
