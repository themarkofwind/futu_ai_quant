"""日内 K 线拉取单元测试。"""

from __future__ import annotations

import pandas as pd
from futu import RET_OK

from futu_ai_quant.brokers.futu.intraday_kline import fetch_intraday_5m_klines


def _make_kline(n: int = 30, *, year: str = "2026") -> pd.DataFrame:
    rows = []
    for i in range(n):
        close = 100.0 + i * 0.2
        rows.append(
            {
                "time_key": f"{year}-06-16 09:{30 + i:02d}:00",
                "open": close - 0.1,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "volume": 100_000 + i * 1000,
                "turnover": (100_000 + i * 1000) * close,
            }
        )
    return pd.DataFrame(rows)


class TestFetchIntraday5mKlines:
    def test_prefers_get_cur_kline(self) -> None:
        cur = _make_kline(30, year="2026")
        hist = _make_kline(30, year="2025")

        class _Ctx:
            def get_cur_kline(self, *args, **kwargs):
                return RET_OK, cur

            def request_history_kline(self, *args, **kwargs):
                return RET_OK, hist, None

        frame, source = fetch_intraday_5m_klines(_Ctx(), "US.BABA")
        assert len(frame) == 30
        assert str(frame.iloc[-1]["time_key"]).startswith("2026")
        assert "get_cur_kline" in source

    def test_falls_back_to_history(self) -> None:
        hist = _make_kline(30, year="2025")

        class _Ctx:
            def get_cur_kline(self, *args, **kwargs):
                return RET_OK, pd.DataFrame()

            def request_history_kline(self, *args, **kwargs):
                return RET_OK, hist, None

        frame, source = fetch_intraday_5m_klines(_Ctx(), "US.BABA")
        assert len(frame) == 30
        assert "request_history_kline" in source
