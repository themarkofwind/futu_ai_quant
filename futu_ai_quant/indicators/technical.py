from __future__ import annotations

from typing import Any

import pandas as pd
import pandas_ta  # noqa: F401 — 注册 DataFrame.ta 访问器
from futu import RET_OK, KLType, OpenQuoteContext

from futu_ai_quant.config.settings import (
    ATR_LENGTH,
    BOLL_LENGTH,
    BOLL_STD,
    MACD_FAST,
    MACD_SIGNAL,
    MACD_SLOW,
    RSI_LENGTH,
    VOLUME_MA_LENGTH,
)
from futu_ai_quant.indicators.kline_cache import fetch_history_kline_cached
from futu_ai_quant.market.session import evaluate_volume_confirmed
from futu_ai_quant.strategy.signals import (
    derive_macd_bias,
    derive_swing_signal,
    describe_boll_position,
)
from futu_ai_quant.utils.numbers import safe_float


def _resolve_indicator_columns(frame: pd.DataFrame) -> tuple[str, str, str, str]:
    rsi_col = f"RSI_{RSI_LENGTH}"
    boll_upper_col = f"BBU_{BOLL_LENGTH}_{float(BOLL_STD)}_{float(BOLL_STD)}"
    boll_mid_col = f"BBM_{BOLL_LENGTH}_{float(BOLL_STD)}_{float(BOLL_STD)}"
    boll_lower_col = f"BBL_{BOLL_LENGTH}_{float(BOLL_STD)}_{float(BOLL_STD)}"

    if rsi_col not in frame.columns:
        rsi_col = next((c for c in frame.columns if c.startswith("RSI_")), rsi_col)
    if boll_upper_col not in frame.columns:
        boll_upper_col = next((c for c in frame.columns if c.startswith("BBU_")), boll_upper_col)
    if boll_mid_col not in frame.columns:
        boll_mid_col = next((c for c in frame.columns if c.startswith("BBM_")), boll_mid_col)
    if boll_lower_col not in frame.columns:
        boll_lower_col = next((c for c in frame.columns if c.startswith("BBL_")), boll_lower_col)
    return rsi_col, boll_upper_col, boll_mid_col, boll_lower_col


def _resolve_macd_columns(frame: pd.DataFrame) -> tuple[str, str, str]:
    macd_col = f"MACD_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
    hist_col = f"MACDh_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
    signal_col = f"MACDs_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
    if macd_col not in frame.columns:
        macd_col = next((c for c in frame.columns if c.startswith("MACD_")), macd_col)
    if hist_col not in frame.columns:
        hist_col = next((c for c in frame.columns if c.startswith("MACDh_")), hist_col)
    if signal_col not in frame.columns:
        signal_col = next((c for c in frame.columns if c.startswith("MACDs_")), signal_col)
    return macd_col, hist_col, signal_col


def _resolve_atr_column(frame: pd.DataFrame) -> str:
    atr_col = f"ATRr_{ATR_LENGTH}"
    if atr_col not in frame.columns:
        atr_col = next((c for c in frame.columns if c.startswith("ATR")), f"ATRr_{ATR_LENGTH}")
    return atr_col


def compute_timeframe_indicators(
    quote_ctx: OpenQuoteContext,
    code: str,
    ktype: KLType,
    max_count: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "timeframe": "daily" if ktype == KLType.K_DAY else "weekly",
        "technical_close": None,
        "rsi": None,
        "boll_upper": None,
        "boll_mid": None,
        "boll_lower": None,
        "boll_position": "unknown",
        "macd_line": None,
        "macd_signal": None,
        "macd_hist": None,
        "macd_bias": "unknown",
        "atr": None,
        "volume": None,
        "volume_ma": None,
        "volume_ratio_raw": None,
        "volume_ratio": None,
        "volume_session_fraction": None,
        "volume_note": None,
        "volume_confirmed": False,
        "swing_signal": "WAIT",
        "error": None,
    }

    try:
        ret, kline, _ = fetch_history_kline_cached(quote_ctx, code, ktype, max_count)
        if ret != RET_OK or kline is None or kline.empty:
            result["error"] = f"K线拉取失败: {kline}"
            return result

        frame = kline.copy()
        frame.ta.rsi(close="close", length=RSI_LENGTH, append=True)
        frame.ta.bbands(close="close", length=BOLL_LENGTH, std=BOLL_STD, append=True)
        frame.ta.macd(
            close="close",
            fast=MACD_FAST,
            slow=MACD_SLOW,
            signal=MACD_SIGNAL,
            append=True,
        )
        frame.ta.atr(high="high", low="low", close="close", length=ATR_LENGTH, append=True)

        latest = frame.iloc[-1]
        prev = frame.iloc[-2] if len(frame) >= 2 else latest
        rsi_col, boll_upper_col, boll_mid_col, boll_lower_col = _resolve_indicator_columns(frame)
        macd_col, hist_col, signal_col = _resolve_macd_columns(frame)
        atr_col = _resolve_atr_column(frame)

        technical_close = safe_float(latest.get("close"))
        rsi = safe_float(latest.get(rsi_col))
        boll_upper = safe_float(latest.get(boll_upper_col))
        boll_mid = safe_float(latest.get(boll_mid_col))
        boll_lower = safe_float(latest.get(boll_lower_col))
        boll_position = describe_boll_position(technical_close, boll_upper, boll_mid, boll_lower)
        timeframe = result["timeframe"]

        macd_line = safe_float(latest.get(macd_col))
        macd_signal_val = safe_float(latest.get(signal_col))
        macd_hist = safe_float(latest.get(hist_col))
        macd_bias = derive_macd_bias(
            macd_line,
            macd_signal_val,
            macd_hist,
            safe_float(prev.get(macd_col)),
            safe_float(prev.get(signal_col)),
            safe_float(prev.get(hist_col)),
        )

        atr = safe_float(latest.get(atr_col))
        volume = safe_float(latest.get("volume"))
        volume_ma = (
            round(float(frame["volume"].tail(VOLUME_MA_LENGTH).mean()), 2)
            if "volume" in frame.columns and len(frame) >= 5
            else None
        )
        volume_ratio_raw = (
            round(volume / volume_ma, 2)
            if volume is not None and volume_ma not in (None, 0)
            else None
        )
        volume_confirmed, volume_ratio, session_fraction, volume_note = evaluate_volume_confirmed(
            volume_ratio_raw,
            timeframe,
        )

        swing_signal = derive_swing_signal(
            rsi,
            boll_position,
            timeframe,
            macd_bias=macd_bias,
            volume_confirmed=volume_confirmed,
        )

        result.update(
            {
                "technical_close": technical_close,
                "rsi": rsi,
                "boll_upper": boll_upper,
                "boll_mid": boll_mid,
                "boll_lower": boll_lower,
                "boll_position": boll_position,
                "macd_line": macd_line,
                "macd_signal": macd_signal_val,
                "macd_hist": macd_hist,
                "macd_bias": macd_bias,
                "atr": atr,
                "volume": volume,
                "volume_ma": volume_ma,
                "volume_ratio_raw": volume_ratio_raw,
                "volume_ratio": volume_ratio,
                "volume_session_fraction": session_fraction,
                "volume_note": volume_note,
                "volume_confirmed": volume_confirmed,
                "swing_signal": swing_signal,
            }
        )
    except Exception as exc:
        result["error"] = str(exc)

    return result


def scale_atr_to_market(
    atr: float | None,
    technical_close: float | None,
    market_price: float | None,
) -> float | None:
    if atr is None or technical_close in (None, 0) or market_price is None:
        return None
    return round(atr / technical_close * market_price, 4)
