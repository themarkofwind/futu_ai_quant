"""日内 T+0 历史回放单元测试。"""

from __future__ import annotations

import pandas as pd

from futu_ai_quant.market.codes import normalize_stock_code
from futu_ai_quant.strategy.intraday_t import IntradayTContext, SignalKind
from futu_ai_quant.strategy.intraday_t_replay import (
    filter_kline_by_day,
    latest_trading_day,
    replay_intraday_t,
)


class TestNormalizeCode:
    def test_hk_prefixed(self) -> None:
        assert normalize_stock_code("HK.09988") == "HK.09988"

    def test_hk_numeric(self) -> None:
        assert normalize_stock_code("09988") == "HK.09988"
        assert normalize_stock_code("HK09988") == "HK.09988"

    def test_us_prefixed(self) -> None:
        assert normalize_stock_code("US.AAPL") == "US.AAPL"
        assert normalize_stock_code("us.aapl") == "US.AAPL"

    def test_bare_ticker_defaults_us(self) -> None:
        assert normalize_stock_code("AAPL") == "US.AAPL"
        assert normalize_stock_code("brk.b") == "US.BRK.B"


def _make_day_frames(day: str, n: int = 40, base: float = 100.0) -> pd.DataFrame:
    rows = []
    for i in range(n):
        total_min = 30 + i * 5
        hour = 9 + total_min // 60
        minute = total_min % 60
        close = base + 0.1 * i
        rows.append(
            {
                "time_key": f"{day} {hour:02d}:{minute:02d}:00",
                "open": close - 0.1,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 100_000,
                "turnover": 100_000 * close,
            }
        )
    return pd.DataFrame(rows)


class TestIntradayTReplay:
    def test_filter_and_latest_day(self) -> None:
        frame = pd.concat(
            [_make_day_frames("2026-06-15", 5), _make_day_frames("2026-06-16", 7)],
            ignore_index=True,
        )
        assert latest_trading_day(frame) == "2026-06-16"
        assert len(filter_kline_by_day(frame, "2026-06-15")) == 5

    def test_replay_runs_with_warmup(self) -> None:
        frame = pd.concat(
            [_make_day_frames("2026-06-15", 40, base=90.0), _make_day_frames("2026-06-16", 40, base=110.0)],
            ignore_index=True,
        )
        events: list = []

        def on_event(event, _header: str) -> None:
            events.append(event)

        result = replay_intraday_t(
            frame,
            code="HK.TEST",
            ctx=IntradayTContext(),
            day="2026-06-16",
            on_event=on_event,
        )
        assert result.day == "2026-06-16"
        assert result.bars_total == 40
        assert result.ticks_processed == 40 * 4
        assert all(
            e.kind
            in {
                SignalKind.SELL,
                SignalKind.BUY_T,
                SignalKind.BUY_BACK,
                SignalKind.SELL_OFF,
                SignalKind.WARNING,
            }
            for e in events
        )
