"""日内 5 分钟 K 线与 VWAP 指标计算。"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pandas_ta  # noqa: F401 — 注册 DataFrame.ta 访问器

from futu_ai_quant.strategy.intraday_t_settings import (
    INTRADAY_T_BOLL_LENGTH,
    INTRADAY_T_BOLL_STD,
    INTRADAY_T_RSI_LENGTH,
)
from futu_ai_quant.utils.numbers import safe_float


def normalize_kline_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """统一 5 分钟 K 线列名与数值类型。"""
    if frame is None or frame.empty:
        return pd.DataFrame()

    work = frame.copy()
    rename_map = {
        "time_key": "time_key",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "volume": "volume",
        "turnover": "turnover",
    }
    for col in rename_map:
        if col not in work.columns:
            work[col] = pd.NA

    for col in ("open", "high", "low", "close", "volume", "turnover"):
        work[col] = pd.to_numeric(work[col], errors="coerce")

    work["time_key"] = work["time_key"].astype(str)
    work = work.dropna(subset=["close", "time_key"]).drop_duplicates(subset=["time_key"], keep="last")
    return work.sort_values("time_key").reset_index(drop=True)


def append_kline_bars(
    existing: pd.DataFrame,
    new_bars: pd.DataFrame,
    *,
    max_rows: int,
) -> pd.DataFrame:
    """合并推送 K 线并维持滚动窗口。"""
    merged = normalize_kline_frame(pd.concat([existing, new_bars], ignore_index=True))
    if merged.empty:
        return merged
    if len(merged) > max_rows:
        merged = merged.tail(max_rows).reset_index(drop=True)
    return merged


def compute_vwap(turnover: float | None, volume: float | None) -> float | None:
    """日内 VWAP = 总成交额 / 总成交量。"""
    if turnover is None or volume in (None, 0):
        return None
    return round(turnover / volume, 4)


def is_rt_data_session_fresh(rt_time: str | None, session_date: str) -> bool:
    """RT_DATA 推送时间是否属于当前交易日（过滤 OpenD 偶发的昨收陈旧推送）。"""
    if not rt_time or not session_date:
        return False
    return str(rt_time).strip()[:10] == session_date[:10]


def session_vwap_from_klines(frame: pd.DataFrame, session_date: str) -> float | None:
    """按交易日汇总 K 线成交额/成交量，估算日内 VWAP。"""
    work = normalize_kline_frame(frame)
    if work.empty:
        return None
    session = work[work["time_key"].astype(str).str.startswith(session_date)]
    if session.empty:
        latest_day = str(work.iloc[-1]["time_key"])[:10]
        session = work[work["time_key"].astype(str).str.startswith(latest_day)]
    if session.empty:
        return None
    turnover = session["turnover"].sum()
    volume = session["volume"].sum()
    return compute_vwap(float(turnover), float(volume))


def _resolve_boll_columns(frame: pd.DataFrame) -> tuple[str, str, str]:
    upper = f"BBU_{INTRADAY_T_BOLL_LENGTH}_{float(INTRADAY_T_BOLL_STD)}_{float(INTRADAY_T_BOLL_STD)}"
    mid = f"BBM_{INTRADAY_T_BOLL_LENGTH}_{float(INTRADAY_T_BOLL_STD)}_{float(INTRADAY_T_BOLL_STD)}"
    lower = f"BBL_{INTRADAY_T_BOLL_LENGTH}_{float(INTRADAY_T_BOLL_STD)}_{float(INTRADAY_T_BOLL_STD)}"
    if upper not in frame.columns:
        upper = next((c for c in frame.columns if c.startswith("BBU_")), upper)
    if mid not in frame.columns:
        mid = next((c for c in frame.columns if c.startswith("BBM_")), mid)
    if lower not in frame.columns:
        lower = next((c for c in frame.columns if c.startswith("BBL_")), lower)
    return upper, mid, lower


def compute_intraday_indicators(frame: pd.DataFrame) -> dict[str, Any]:
    """基于 5 分钟 K 线计算 BOLL 与 RSI（含未收盘 K 线，仅供调试）。"""
    result: dict[str, Any] = {
        "close": None,
        "rsi": None,
        "boll_upper": None,
        "boll_mid": None,
        "boll_lower": None,
        "volume": None,
        "volume_ma": None,
        "ready": False,
        "error": None,
    }

    work = normalize_kline_frame(frame)
    min_bars = max(INTRADAY_T_BOLL_LENGTH, INTRADAY_T_RSI_LENGTH) + 2
    if len(work) < min_bars:
        result["error"] = f"K 线不足（{len(work)}/{min_bars}）"
        return result

    try:
        work.ta.rsi(close="close", length=INTRADAY_T_RSI_LENGTH, append=True)
        work.ta.bbands(
            close="close",
            length=INTRADAY_T_BOLL_LENGTH,
            std=INTRADAY_T_BOLL_STD,
            append=True,
        )
        rsi_col = f"RSI_{INTRADAY_T_RSI_LENGTH}"
        if rsi_col not in work.columns:
            rsi_col = next((c for c in work.columns if c.startswith("RSI_")), rsi_col)

        upper_col, mid_col, lower_col = _resolve_boll_columns(work)
        latest = work.iloc[-1]
        volume_ma = (
            round(float(work["volume"].tail(INTRADAY_T_BOLL_LENGTH).mean()), 2)
            if work["volume"].notna().any()
            else None
        )

        result.update(
            {
                "close": safe_float(latest.get("close")),
                "rsi": safe_float(latest.get(rsi_col)),
                "boll_upper": safe_float(latest.get(upper_col)),
                "boll_mid": safe_float(latest.get(mid_col)),
                "boll_lower": safe_float(latest.get(lower_col)),
                "volume": safe_float(latest.get("volume")),
                "volume_ma": volume_ma,
                "ready": True,
                "frame": work,
            }
        )
    except Exception as exc:
        result["error"] = str(exc)

    return result


def closed_kline_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """
    仅保留已收盘的 5 分钟 K 线。

    订阅推送中最后一根为正在形成的 K 线，参与指标计算会导致 RSI/BOLL 闪烁（Repainting）。
    """
    work = normalize_kline_frame(frame)
    if len(work) <= 1:
        return pd.DataFrame()
    return work.iloc[:-1].reset_index(drop=True)


def compute_locked_intraday_indicators(frame: pd.DataFrame) -> dict[str, Any]:
    """基于已收盘 5 分钟 K 线锁定 RSI / BOLL，供秒级现价对比。"""
    closed = closed_kline_bars(frame)
    work = normalize_kline_frame(frame)
    result = compute_intraday_indicators(closed)
    result["locked"] = result.get("ready", False)
    result["locked_at"] = str(closed.iloc[-1]["time_key"]) if not closed.empty else None
    result["forming_time_key"] = str(work.iloc[-1]["time_key"]) if not work.empty else None
    return result


def count_consecutive_closes_above_upper(frame: pd.DataFrame, upper_col: str) -> int:
    """统计从最新 K 线向前连续收盘站上布林上轨的根数。"""
    work = normalize_kline_frame(frame)
    if work.empty or upper_col not in work.columns:
        return 0

    count = 0
    for _, row in work.iloc[::-1].iterrows():
        close = safe_float(row.get("close"))
        upper = safe_float(row.get(upper_col))
        if close is None or upper is None or close < upper:
            break
        count += 1
    return count


def count_consecutive_closes_below_lower(frame: pd.DataFrame, lower_col: str) -> int:
    """统计从最新 K 线向前连续收盘跌破布林下轨的根数。"""
    work = normalize_kline_frame(frame)
    if work.empty or lower_col not in work.columns:
        return 0

    count = 0
    for _, row in work.iloc[::-1].iterrows():
        close = safe_float(row.get("close"))
        lower = safe_float(row.get(lower_col))
        if close is None or lower is None or close > lower:
            break
        count += 1
    return count
