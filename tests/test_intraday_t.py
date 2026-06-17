"""日内 T+0 状态机与指标单元测试。"""

from __future__ import annotations

import pandas as pd

from futu_ai_quant.indicators.intraday import (
    append_kline_bars,
    closed_kline_bars,
    compute_intraday_indicators,
    compute_locked_intraday_indicators,
    compute_vwap,
)
from futu_ai_quant.strategy.intraday_t import (
    IntradayTContext,
    IntradayTState,
    SignalKind,
    detect_strong_trend_warning,
    evaluate_intraday_t,
    indicators_from_kline,
)


def _make_kline_frame(n: int = 30, base: float = 100.0, spread: float = 0.2) -> pd.DataFrame:
    rows = []
    for i in range(n):
        close = base + spread * i
        rows.append(
            {
                "time_key": f"2026-06-16 09:{30 + i:02d}:00",
                "open": close - 0.2,
                "high": close + 0.3,
                "low": close - 0.3,
                "close": close,
                "volume": 100_000 + i * 1_000,
                "turnover": (100_000 + i * 1_000) * close,
            }
        )
    return pd.DataFrame(rows)


class TestIntradayIndicators:
    def test_vwap(self) -> None:
        assert compute_vwap(1_000_000, 10_000) == 100.0
        assert compute_vwap(1_000_000, 0) is None

    def test_append_kline_dedupes(self) -> None:
        base = _make_kline_frame(5)
        dup = base.tail(1).copy()
        dup.loc[dup.index[0], "close"] = 999.0
        merged = append_kline_bars(base, dup, max_rows=10)
        assert len(merged) == 5
        assert float(merged.iloc[-1]["close"]) == 999.0

    def test_compute_indicators_ready(self) -> None:
        frame = _make_kline_frame(30)
        result = compute_intraday_indicators(frame)
        assert result["ready"] is True
        assert result["rsi"] is not None
        assert result["boll_upper"] is not None

    def test_locked_indicators_exclude_forming_bar(self) -> None:
        frame = _make_kline_frame(30)
        closed = closed_kline_bars(frame)
        assert len(closed) == 29

        locked = compute_locked_intraday_indicators(frame)
        assert locked["locked"] is True
        assert locked["locked_at"] == str(closed.iloc[-1]["time_key"])
        assert locked["forming_time_key"] == str(frame.iloc[-1]["time_key"])
        assert locked.get("rsi") is not None

    def test_locked_vs_full_can_differ_on_forming_bar(self) -> None:
        frame = _make_kline_frame(30)
        frame.loc[frame.index[-1], "close"] = frame.iloc[-2]["close"] + 5.0
        locked = compute_locked_intraday_indicators(frame)
        full = compute_intraday_indicators(frame)
        assert locked["locked"] is True
        assert locked["rsi"] != full["rsi"] or locked["boll_upper"] != full["boll_upper"]


class TestIntradayTStateMachine:
    def _rich_indicators(self, *, close: float, rsi: float, upper: float, lower: float) -> dict:
        frame = _make_kline_frame(30, base=close)
        indicators = indicators_from_kline(frame)
        indicators["close"] = close
        indicators["rsi"] = rsi
        indicators["boll_upper"] = upper
        indicators["boll_lower"] = lower
        indicators["ready"] = True
        indicators["locked"] = True
        return indicators

    def test_sell_signal_when_overbought(self) -> None:
        ctx = IntradayTContext()
        indicators = self._rich_indicators(close=110.0, rsi=80.0, upper=105.0, lower=95.0)
        events = evaluate_intraday_t(
            ctx,
            current_price=110.0,
            vwap=100.0,
            indicators=indicators,
        )
        assert any(e.kind == SignalKind.SELL for e in events)
        assert ctx.state == IntradayTState.SHORT_T
        assert ctx.entry_price == 110.0

    def test_buy_t_signal_when_oversold(self) -> None:
        ctx = IntradayTContext()
        indicators = self._rich_indicators(close=90.0, rsi=30.0, upper=105.0, lower=95.0)
        events = evaluate_intraday_t(
            ctx,
            current_price=90.0,
            vwap=100.0,
            indicators=indicators,
        )
        assert any(e.kind == SignalKind.BUY_T for e in events)
        assert ctx.state == IntradayTState.LONG_T
        assert ctx.entry_price == 90.0

    def test_sell_blocked_by_vwap_premium(self) -> None:
        ctx = IntradayTContext()
        indicators = self._rich_indicators(close=110.0, rsi=80.0, upper=105.0, lower=95.0)
        events = evaluate_intraday_t(
            ctx,
            current_price=110.0,
            vwap=109.0,
            indicators=indicators,
        )
        assert not any(e.kind == SignalKind.SELL for e in events)
        assert ctx.state == IntradayTState.AT_BASE

    def test_buy_t_blocked_by_vwap_discount(self) -> None:
        ctx = IntradayTContext()
        indicators = self._rich_indicators(close=90.0, rsi=30.0, upper=105.0, lower=95.0)
        events = evaluate_intraday_t(
            ctx,
            current_price=90.0,
            vwap=91.0,
            indicators=indicators,
        )
        assert not any(e.kind == SignalKind.BUY_T for e in events)
        assert ctx.state == IntradayTState.AT_BASE

    def test_buy_back_take_profit(self) -> None:
        ctx = IntradayTContext()
        ctx.state = IntradayTState.SHORT_T
        ctx.entry_price = 110.0
        indicators = self._rich_indicators(close=108.5, rsi=50.0, upper=115.0, lower=95.0)
        events = evaluate_intraday_t(
            ctx,
            current_price=108.5,
            vwap=105.0,
            indicators=indicators,
        )
        assert any(e.kind == SignalKind.BUY_BACK for e in events)
        assert ctx.state == IntradayTState.AT_BASE

    def test_buy_back_technical(self) -> None:
        ctx = IntradayTContext()
        ctx.state = IntradayTState.SHORT_T
        ctx.entry_price = 110.0
        indicators = self._rich_indicators(close=96.0, rsi=30.0, upper=115.0, lower=97.0)
        events = evaluate_intraday_t(
            ctx,
            current_price=96.0,
            vwap=100.0,
            indicators=indicators,
        )
        assert any(e.kind == SignalKind.BUY_BACK for e in events)
        assert ctx.state == IntradayTState.AT_BASE

    def test_sell_off_take_profit(self) -> None:
        ctx = IntradayTContext()
        ctx.state = IntradayTState.LONG_T
        ctx.entry_price = 90.0
        indicators = self._rich_indicators(close=91.5, rsi=50.0, upper=105.0, lower=85.0)
        events = evaluate_intraday_t(
            ctx,
            current_price=91.5,
            vwap=100.0,
            indicators=indicators,
        )
        assert any(e.kind == SignalKind.SELL_OFF for e in events)
        assert ctx.state == IntradayTState.AT_BASE

    def test_sell_off_technical(self) -> None:
        ctx = IntradayTContext()
        ctx.state = IntradayTState.LONG_T
        ctx.entry_price = 90.0
        indicators = self._rich_indicators(close=110.0, rsi=80.0, upper=105.0, lower=95.0)
        events = evaluate_intraday_t(
            ctx,
            current_price=110.0,
            vwap=100.0,
            indicators=indicators,
        )
        assert any(e.kind == SignalKind.SELL_OFF for e in events)
        assert ctx.state == IntradayTState.AT_BASE

    def test_strong_trend_warning_blocks_sell(self) -> None:
        frame = _make_kline_frame(30, base=100.0, spread=1.0)
        indicators = indicators_from_kline(frame)
        indicators["volume"] = 1_000_000
        indicators["volume_ma"] = 100_000
        work = indicators["frame"]
        upper_col = next(c for c in work.columns if c.startswith("BBU_"))
        work.loc[work.index[-3:], "close"] = work.loc[work.index[-3:], upper_col] + 1
        work.loc[work.index[-3:], upper_col] = work.loc[work.index[-3:], "close"] - 0.5
        indicators["frame"] = work

        warned, _ = detect_strong_trend_warning(indicators)
        assert warned is True

        ctx = IntradayTContext()
        indicators["rsi"] = 80.0
        indicators["boll_upper"] = 90.0
        indicators["close"] = 110.0
        events = evaluate_intraday_t(
            ctx,
            current_price=110.0,
            vwap=100.0,
            indicators=indicators,
        )
        assert any(e.kind == SignalKind.WARNING for e in events)
        assert ctx.state == IntradayTState.AT_BASE

    def test_short_t_skips_open_and_warning(self) -> None:
        ctx = IntradayTContext()
        ctx.state = IntradayTState.SHORT_T
        ctx.entry_price = 110.0
        indicators = self._rich_indicators(close=110.0, rsi=80.0, upper=105.0, lower=95.0)
        events = evaluate_intraday_t(
            ctx,
            current_price=110.0,
            vwap=100.0,
            indicators=indicators,
        )
        assert not any(e.kind == SignalKind.SELL for e in events)
        assert not any(e.kind == SignalKind.WARNING for e in events)
        assert ctx.state == IntradayTState.SHORT_T
