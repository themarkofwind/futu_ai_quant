"""信号级历史回测（规则引擎，不调用 LLM）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
from futu import RET_OK, KLType, OpenQuoteContext

from futu_ai_quant.config.settings import KLINE_COUNT
from futu_ai_quant.indicators.kline_cache import fetch_history_kline_cached
from futu_ai_quant.indicators.technical import compute_indicators_from_frame, resample_to_weekly
from futu_ai_quant.strategy.profile import build_swing_strategy_profile
from futu_ai_quant.strategy.signals import resolve_effective_swing_signal


@dataclass
class SignalEvent:
    date: str
    signal: str
    close: float
    forward_return_5d: float | None
    forward_return_10d: float | None


def _forward_return(closes: pd.Series, idx: int, horizon: int) -> float | None:
    if idx + horizon >= len(closes):
        return None
    base = closes.iloc[idx]
    future = closes.iloc[idx + horizon]
    if base in (None, 0) or pd.isna(base) or pd.isna(future):
        return None
    return round((future - base) / base * 100, 4)


def run_signal_backtest_on_frame(
    frame: pd.DataFrame,
    *,
    pl_ratio: float = -30.0,
    min_warmup: int = 30,
    hold_horizons: tuple[int, ...] = (5, 10),
) -> dict[str, Any]:
    """
    在历史 K 线上逐日回放波段信号，统计前瞻收益。

    Parameters
    ----------
    frame :
        含 open/high/low/close/volume 的日 K DataFrame，按时间升序。
    pl_ratio :
        模拟持仓盈亏比例，用于分层策略（默认中度亏损）。
    """
    if frame is None or frame.empty or "close" not in frame.columns:
        return {"error": "K 线数据无效", "events": []}

    work = frame.copy()
    for col in ("open", "high", "low", "close", "volume"):
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")
    work = work.dropna(subset=["close"]).reset_index(drop=True)

    swing_strategy = build_swing_strategy_profile(pl_ratio)
    primary_tf = swing_strategy["primary_timeframe"]

    events: list[SignalEvent] = []
    closes = work["close"]

    for i in range(min_warmup, len(work)):
        slice_frame = work.iloc[: i + 1]
        daily = compute_indicators_from_frame(slice_frame, "daily")
        weekly_frame = resample_to_weekly(slice_frame)
        weekly = (
            compute_indicators_from_frame(weekly_frame, "weekly")
            if len(weekly_frame) >= 10
            else {"swing_signal": "WAIT"}
        )

        primary_signal = weekly["swing_signal"] if primary_tf == "weekly" else daily["swing_signal"]
        secondary_signal = daily["swing_signal"] if primary_tf == "weekly" else weekly["swing_signal"]
        combined = resolve_effective_swing_signal(
            primary_signal,
            secondary_signal,
            swing_strategy,
            primary_timeframe=primary_tf,
        )
        effective = combined.get("effective_signal", "HOLD")
        if effective not in ("BUY_SWING", "SELL_SWING"):
            continue

        date_val = work.iloc[i].get("time_key") or work.iloc[i].get("date") or str(i)
        event = SignalEvent(
            date=str(date_val),
            signal=effective,
            close=float(closes.iloc[i]),
            forward_return_5d=_forward_return(closes, i, hold_horizons[0]),
            forward_return_10d=_forward_return(closes, i, hold_horizons[1] if len(hold_horizons) > 1 else hold_horizons[0]),
        )
        events.append(event)

    return summarize_signal_events(events)


def summarize_signal_events(events: list[SignalEvent]) -> dict[str, Any]:
    if not events:
        return {
            "signal_count": 0,
            "buy_count": 0,
            "sell_count": 0,
            "events": [],
            "stats": {},
        }

    buy_events = [e for e in events if e.signal == "BUY_SWING"]
    sell_events = [e for e in events if e.signal == "SELL_SWING"]

    def _avg_forward(items: list[SignalEvent], field: str) -> float | None:
        vals = [getattr(e, field) for e in items if getattr(e, field) is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    def _win_rate(items: list[SignalEvent], field: str, *, buy: bool) -> float | None:
        vals = [getattr(e, field) for e in items if getattr(e, field) is not None]
        if not vals:
            return None
        if buy:
            wins = sum(1 for v in vals if v > 0)
        else:
            wins = sum(1 for v in vals if v < 0)
        return round(wins / len(vals) * 100, 2)

    stats = {
        "buy_avg_forward_5d_pct": _avg_forward(buy_events, "forward_return_5d"),
        "buy_avg_forward_10d_pct": _avg_forward(buy_events, "forward_return_10d"),
        "sell_avg_forward_5d_pct": _avg_forward(sell_events, "forward_return_5d"),
        "sell_avg_forward_10d_pct": _avg_forward(sell_events, "forward_return_10d"),
        "buy_win_rate_5d_pct": _win_rate(buy_events, "forward_return_5d", buy=True),
        "sell_win_rate_5d_pct": _win_rate(sell_events, "forward_return_5d", buy=False),
    }

    return {
        "signal_count": len(events),
        "buy_count": len(buy_events),
        "sell_count": len(sell_events),
        "stats": stats,
        "events": [
            {
                "date": e.date,
                "signal": e.signal,
                "close": e.close,
                "forward_return_5d": e.forward_return_5d,
                "forward_return_10d": e.forward_return_10d,
            }
            for e in events[-50:]
        ],
    }


def run_signal_backtest(
    quote_ctx: OpenQuoteContext,
    code: str,
    *,
    pl_ratio: float = -30.0,
    kline_count: int = KLINE_COUNT,
) -> dict[str, Any]:
    """拉取日 K 并执行信号回测。"""
    ret, kline, _ = fetch_history_kline_cached(quote_ctx, code, KLType.K_DAY, kline_count)
    if ret != RET_OK or kline is None or kline.empty:
        return {"code": code, "error": f"K线拉取失败: {kline}", "events": []}

    result = run_signal_backtest_on_frame(kline, pl_ratio=pl_ratio)
    result["code"] = code
    return result
