"""
多策略技术指标集成（趋势 / 均值回归 / 动量 / 波动率 / 统计套利）。

借鉴 ai-hedge-fund technical_analyst，输出 bullish/bearish/neutral 信号与置信度。
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from futu_ai_quant.utils.numbers import safe_float

_STRATEGY_WEIGHTS = {
    "trend": 0.25,
    "mean_reversion": 0.20,
    "momentum": 0.25,
    "volatility": 0.15,
    "stat_arb": 0.15,
}


def _signal_values() -> dict[str, int]:
    return {"bullish": 1, "neutral": 0, "bearish": -1}


def weighted_signal_combination(
    signals: dict[str, dict[str, Any]],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    weights = weights or _STRATEGY_WEIGHTS
    signal_values = _signal_values()
    weighted_sum = 0.0
    total_confidence = 0.0

    for strategy, signal in signals.items():
        weight = weights.get(strategy, 0.0)
        if weight <= 0:
            continue
        numeric = signal_values.get(signal.get("signal", "neutral"), 0)
        confidence = float(signal.get("confidence") or 0.5)
        weighted_sum += numeric * weight * confidence
        total_confidence += weight * confidence

    final_score = weighted_sum / total_confidence if total_confidence > 0 else 0.0
    if final_score > 0.2:
        combined = "bullish"
    elif final_score < -0.2:
        combined = "bearish"
    else:
        combined = "neutral"

    return {
        "signal": combined,
        "confidence": round(abs(final_score), 4),
        "score": round(final_score, 4),
    }


def calculate_ema(series: pd.Series, window: int) -> pd.Series:
    return series.ewm(span=window, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).fillna(0.0)
    loss = (-delta.where(delta < 0, 0.0)).fillna(0.0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calculate_bollinger_bands(
    series: pd.Series,
    window: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series]:
    sma = series.rolling(window).mean()
    std = series.rolling(window).std()
    return sma + std * num_std, sma - std * num_std


def calculate_adx(frame: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high_low = frame["high"] - frame["low"]
    high_close = (frame["high"] - frame["close"].shift()).abs()
    low_close = (frame["low"] - frame["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

    up_move = frame["high"] - frame["high"].shift()
    down_move = frame["low"].shift() - frame["low"]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    plus_di = 100 * (pd.Series(plus_dm, index=frame.index).ewm(span=period).mean() / tr.ewm(span=period).mean())
    minus_di = 100 * (pd.Series(minus_dm, index=frame.index).ewm(span=period).mean() / tr.ewm(span=period).mean())
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(span=period).mean()
    return pd.DataFrame({"adx": adx, "+di": plus_di, "-di": minus_di})


def calculate_atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = frame["high"] - frame["low"]
    high_close = (frame["high"] - frame["close"].shift()).abs()
    low_close = (frame["low"] - frame["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def calculate_hurst_exponent(price_series: pd.Series, max_lag: int = 20) -> float:
    lags = range(2, max_lag)
    tau = [
        max(1e-8, float(np.sqrt(np.std(np.subtract(price_series.iloc[lag:], price_series.iloc[:-lag])))))
        for lag in lags
        if len(price_series) > lag
    ]
    if len(tau) < 2:
        return 0.5
    try:
        reg = np.polyfit(np.log(list(lags)[: len(tau)]), np.log(tau), 1)
        return float(reg[0])
    except (ValueError, FloatingPointError):
        return 0.5


def calculate_trend_signals(frame: pd.DataFrame) -> dict[str, Any]:
    ema_8 = calculate_ema(frame["close"], 8)
    ema_21 = calculate_ema(frame["close"], 21)
    ema_55 = calculate_ema(frame["close"], 55)
    adx = calculate_adx(frame, 14)

    short_trend = ema_8 > ema_21
    medium_trend = ema_21 > ema_55
    trend_strength = float(adx["adx"].iloc[-1] / 100.0) if not pd.isna(adx["adx"].iloc[-1]) else 0.5

    if bool(short_trend.iloc[-1]) and bool(medium_trend.iloc[-1]):
        signal, confidence = "bullish", trend_strength
    elif not bool(short_trend.iloc[-1]) and not bool(medium_trend.iloc[-1]):
        signal, confidence = "bearish", trend_strength
    else:
        signal, confidence = "neutral", 0.5

    return {
        "signal": signal,
        "confidence": confidence,
        "metrics": {"adx": safe_float(adx["adx"].iloc[-1]), "trend_strength": safe_float(trend_strength)},
    }


def calculate_mean_reversion_signals(frame: pd.DataFrame) -> dict[str, Any]:
    ma_50 = frame["close"].rolling(window=50).mean()
    std_50 = frame["close"].rolling(window=50).std()
    z_score = (frame["close"] - ma_50) / std_50.replace(0, np.nan)
    bb_upper, bb_lower = calculate_bollinger_bands(frame["close"])
    rsi_14 = calculate_rsi(frame["close"], 14)

    last_close = frame["close"].iloc[-1]
    bb_range = bb_upper.iloc[-1] - bb_lower.iloc[-1]
    price_vs_bb = (last_close - bb_lower.iloc[-1]) / bb_range if bb_range and not pd.isna(bb_range) else 0.5

    z = z_score.iloc[-1]
    if not pd.isna(z) and z < -2 and price_vs_bb < 0.2:
        signal, confidence = "bullish", min(abs(z) / 4, 1.0)
    elif not pd.isna(z) and z > 2 and price_vs_bb > 0.8:
        signal, confidence = "bearish", min(abs(z) / 4, 1.0)
    else:
        signal, confidence = "neutral", 0.5

    return {
        "signal": signal,
        "confidence": confidence,
        "metrics": {
            "z_score": safe_float(z),
            "price_vs_bb": safe_float(price_vs_bb),
            "rsi_14": safe_float(rsi_14.iloc[-1]),
        },
    }


def calculate_momentum_signals(frame: pd.DataFrame) -> dict[str, Any]:
    returns = frame["close"].pct_change()
    mom_1m = returns.rolling(21).sum()
    mom_3m = returns.rolling(63).sum()
    mom_6m = returns.rolling(126).sum()

    volume_ma = frame["volume"].rolling(21).mean() if "volume" in frame.columns else None
    volume_momentum = (
        frame["volume"] / volume_ma.replace(0, np.nan)
        if volume_ma is not None
        else pd.Series(1.0, index=frame.index)
    )

    momentum_score = float(0.4 * mom_1m.iloc[-1] + 0.3 * mom_3m.iloc[-1] + 0.3 * mom_6m.iloc[-1])
    volume_confirmation = bool(volume_momentum.iloc[-1] > 1.0) if not pd.isna(volume_momentum.iloc[-1]) else False

    if momentum_score > 0.05 and volume_confirmation:
        signal, confidence = "bullish", min(abs(momentum_score) * 5, 1.0)
    elif momentum_score < -0.05 and volume_confirmation:
        signal, confidence = "bearish", min(abs(momentum_score) * 5, 1.0)
    else:
        signal, confidence = "neutral", 0.5

    return {
        "signal": signal,
        "confidence": confidence,
        "metrics": {
            "momentum_1m": safe_float(mom_1m.iloc[-1]),
            "momentum_3m": safe_float(mom_3m.iloc[-1]),
            "momentum_6m": safe_float(mom_6m.iloc[-1]),
            "volume_momentum": safe_float(volume_momentum.iloc[-1]),
        },
    }


def calculate_volatility_signals(frame: pd.DataFrame) -> dict[str, Any]:
    returns = frame["close"].pct_change()
    hist_vol = returns.rolling(21).std() * math.sqrt(252)
    vol_ma = hist_vol.rolling(63).mean()
    vol_regime = hist_vol / vol_ma.replace(0, np.nan)
    vol_z_score = (hist_vol - vol_ma) / hist_vol.rolling(63).std().replace(0, np.nan)
    atr = calculate_atr(frame)
    atr_ratio = atr / frame["close"].replace(0, np.nan)

    current_regime = vol_regime.iloc[-1]
    vol_z = vol_z_score.iloc[-1]

    if not pd.isna(current_regime) and not pd.isna(vol_z) and current_regime < 0.8 and vol_z < -1:
        signal, confidence = "bullish", min(abs(vol_z) / 3, 1.0)
    elif not pd.isna(current_regime) and not pd.isna(vol_z) and current_regime > 1.2 and vol_z > 1:
        signal, confidence = "bearish", min(abs(vol_z) / 3, 1.0)
    else:
        signal, confidence = "neutral", 0.5

    return {
        "signal": signal,
        "confidence": confidence,
        "metrics": {
            "historical_volatility": safe_float(hist_vol.iloc[-1]),
            "volatility_regime": safe_float(current_regime),
            "volatility_z_score": safe_float(vol_z),
            "atr_ratio": safe_float(atr_ratio.iloc[-1]),
        },
    }


def calculate_stat_arb_signals(frame: pd.DataFrame) -> dict[str, Any]:
    returns = frame["close"].pct_change()
    skew = returns.rolling(63).skew()
    kurt = returns.rolling(63).kurt()
    hurst = calculate_hurst_exponent(frame["close"])

    skew_val = skew.iloc[-1]
    if hurst < 0.4 and not pd.isna(skew_val) and skew_val > 1:
        signal, confidence = "bullish", (0.5 - hurst) * 2
    elif hurst < 0.4 and not pd.isna(skew_val) and skew_val < -1:
        signal, confidence = "bearish", (0.5 - hurst) * 2
    else:
        signal, confidence = "neutral", 0.5

    return {
        "signal": signal,
        "confidence": confidence,
        "metrics": {
            "hurst_exponent": safe_float(hurst),
            "skewness": safe_float(skew_val),
            "kurtosis": safe_float(kurt.iloc[-1]),
        },
    }


def compute_technical_ensemble(frame: pd.DataFrame) -> dict[str, Any] | None:
    """对 OHLCV DataFrame 计算五策略集成信号。"""
    required = {"open", "high", "low", "close"}
    if frame is None or frame.empty or not required.issubset(frame.columns):
        return None
    if len(frame) < 30:
        return None

    work = frame.copy()
    for col in ("open", "high", "low", "close", "volume"):
        if col in work.columns:
            work[col] = pd.to_numeric(work[col], errors="coerce")

    sub_signals = {
        "trend": calculate_trend_signals(work),
        "mean_reversion": calculate_mean_reversion_signals(work),
        "momentum": calculate_momentum_signals(work),
        "volatility": calculate_volatility_signals(work),
        "stat_arb": calculate_stat_arb_signals(work),
    }
    combined = weighted_signal_combination(sub_signals)

    return {
        "signal": combined["signal"],
        "confidence": round(combined["confidence"] * 100, 1),
        "score": combined["score"],
        "strategies": {
            name: {
                "signal": sig["signal"],
                "confidence": round(float(sig["confidence"]) * 100, 1),
            }
            for name, sig in sub_signals.items()
        },
    }
