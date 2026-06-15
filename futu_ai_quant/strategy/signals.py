from __future__ import annotations

from typing import Any


def resolve_effective_swing_signal(
    primary_signal: str,
    secondary_signal: str,
    swing_strategy: dict[str, Any],
    *,
    primary_timeframe: str,
) -> dict[str, Any]:
    """综合主/次周期信号，产出实际用于交易计划的 effective_signal。"""
    combined: dict[str, Any] = {
        "primary_timeframe": primary_timeframe,
        "primary_signal": primary_signal,
        "secondary_signal": secondary_signal,
        "aligned": primary_signal == secondary_signal,
        "effective_signal": primary_signal,
        "signal_note": None,
    }

    if primary_signal in ("HOLD", "WAIT"):
        combined["effective_signal"] = primary_signal
        return combined

    if primary_signal == "SELL_SWING":
        combined["effective_signal"] = "SELL_SWING"
        if secondary_signal == "BUY_SWING":
            combined["signal_note"] = "次周期看多，主周期减仓仍有效，宜控制比例"
        return combined

    if primary_signal == "BUY_SWING":
        if secondary_signal == "SELL_SWING":
            combined["effective_signal"] = "HOLD"
            combined["signal_note"] = "次周期看空，暂缓低吸"
            return combined

        loss_tier = swing_strategy.get("loss_tier", "unknown")
        primary_tf = swing_strategy.get("primary_timeframe", "weekly")
        if loss_tier == "deep_loss" and secondary_signal in ("HOLD", "WAIT"):
            combined["effective_signal"] = "HOLD"
            combined["signal_note"] = "深套仓位需次周期配合才小仓低吸"
            return combined
        if primary_tf == "daily" and secondary_signal in ("HOLD", "WAIT"):
            combined["effective_signal"] = "HOLD"
            combined["signal_note"] = "日K主导低吸需周K非观望确认"
            return combined
        if secondary_signal == "BUY_SWING":
            combined["signal_note"] = "双周期共振看多"
        elif secondary_signal in ("HOLD", "WAIT") and primary_tf == "weekly":
            combined["signal_note"] = "周K主导，次周期观望"

        combined["effective_signal"] = "BUY_SWING"
        return combined

    combined["effective_signal"] = primary_signal
    return combined


def derive_macd_bias(
    macd_line: float | None,
    macd_signal: float | None,
    macd_hist: float | None,
    prev_macd_line: float | None,
    prev_macd_signal: float | None,
    prev_macd_hist: float | None,
) -> str:
    if None in (macd_line, macd_signal, macd_hist):
        return "unknown"
    if (
        prev_macd_line is not None
        and prev_macd_signal is not None
        and prev_macd_line <= prev_macd_signal
        and macd_line > macd_signal
    ):
        return "golden_cross"
    if (
        prev_macd_line is not None
        and prev_macd_signal is not None
        and prev_macd_line >= prev_macd_signal
        and macd_line < macd_signal
    ):
        return "death_cross"
    if macd_hist > 0 and (prev_macd_hist is None or macd_hist >= prev_macd_hist):
        return "bullish"
    if macd_hist < 0 and (prev_macd_hist is None or macd_hist <= prev_macd_hist):
        return "bearish"
    return "neutral"


def describe_boll_position(
    price: float | None,
    upper: float | None,
    mid: float | None,
    lower: float | None,
) -> str:
    if price is None or upper is None or mid is None or lower is None:
        return "unknown"
    if price >= upper:
        return "above_upper"
    if price >= mid + (upper - mid) * 0.6:
        return "near_upper"
    if price <= lower:
        return "below_lower"
    if price <= mid - (mid - lower) * 0.6:
        return "near_lower"
    return "around_mid"


def derive_swing_signal(
    rsi: float | None,
    boll_position: str,
    timeframe: str,
    macd_bias: str = "unknown",
    volume_confirmed: bool = False,
) -> str:
    if rsi is None or boll_position == "unknown":
        return "WAIT"

    base_signal = "HOLD"
    if timeframe == "weekly":
        if rsi < 40 and boll_position in ("below_lower", "near_lower"):
            base_signal = "BUY_SWING"
        elif rsi > 60 and boll_position in ("above_upper", "near_upper"):
            base_signal = "SELL_SWING"
    else:
        if rsi < 35 and boll_position in ("below_lower", "near_lower"):
            base_signal = "BUY_SWING"
        elif rsi > 65 and boll_position in ("above_upper", "near_upper"):
            base_signal = "SELL_SWING"

    if base_signal == "HOLD":
        return "HOLD"

    # MACD 冲突时降级
    if base_signal == "BUY_SWING" and macd_bias in ("death_cross", "bearish"):
        return "HOLD"
    if base_signal == "SELL_SWING" and macd_bias in ("golden_cross", "bullish"):
        return "HOLD"

    # 日K需成交量确认；MACD 同向时强化信号
    if timeframe == "daily" and not volume_confirmed:
        if macd_bias not in ("golden_cross", "death_cross"):
            return "HOLD"

    return base_signal
