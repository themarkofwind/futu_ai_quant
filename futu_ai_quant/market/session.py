from __future__ import annotations

from datetime import datetime
from typing import Any

from futu_ai_quant.config.settings import (
    ANALYSIS_INTERVAL_SEC,
    INTRADAY_INTERVAL_SEC,
    MIN_SESSION_VOLUME_FRACTION,
    OFFHOURS_INTERVAL_SEC,
    VOLUME_CONFIRM_RATIO,
    VOLUME_FILTER,
)


def is_hk_trading_session(now: datetime | None = None) -> bool:
    """港股交易时段：周一至五 09:30-12:00、13:00-16:00（本地北京时间）。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    morning = (9 * 60 + 30) <= minutes < (12 * 60)
    afternoon = (13 * 60) <= minutes < (16 * 60)
    return morning or afternoon


def hk_session_volume_fraction(now: datetime | None = None) -> float:
    """已过港股交易时间占全日交易时段比例（09:30-12:00 + 13:00-16:00），用于盘中量比折算。"""
    now = now or datetime.now()
    morning_start = 9 * 60 + 30
    morning_end = 12 * 60
    afternoon_start = 13 * 60
    afternoon_end = 16 * 60
    total_minutes = (morning_end - morning_start) + (afternoon_end - afternoon_start)

    if now.weekday() >= 5:
        return 1.0
    minutes = now.hour * 60 + now.minute
    if minutes <= morning_start:
        return MIN_SESSION_VOLUME_FRACTION
    if minutes < morning_end:
        elapsed = minutes - morning_start
        return max(elapsed / total_minutes, MIN_SESSION_VOLUME_FRACTION)
    if minutes < afternoon_start:
        return max((morning_end - morning_start) / total_minutes, MIN_SESSION_VOLUME_FRACTION)
    if minutes < afternoon_end:
        elapsed = (morning_end - morning_start) + (minutes - afternoon_start)
        return max(elapsed / total_minutes, MIN_SESSION_VOLUME_FRACTION)
    return 1.0


def evaluate_volume_confirmed(
    volume_ratio_raw: float | None,
    timeframe: str,
    *,
    now: datetime | None = None,
) -> tuple[bool, float | None, float | None, str | None]:
    """返回 (volume_confirmed, volume_ratio, session_fraction, volume_note)。"""
    if volume_ratio_raw is None:
        return False, None, None, None
    if timeframe != "daily":
        return volume_ratio_raw >= VOLUME_CONFIRM_RATIO, volume_ratio_raw, 1.0, None

    now = now or datetime.now()
    if VOLUME_FILTER == "raw":
        return volume_ratio_raw >= VOLUME_CONFIRM_RATIO, volume_ratio_raw, 1.0, None

    if VOLUME_FILTER == "close_only":
        minutes = now.hour * 60 + now.minute
        late_session = minutes >= (14 * 60) or not is_hk_trading_session(now)
        if not late_session:
            return False, volume_ratio_raw, 1.0, "盘中前半段暂不校验量比"
        return volume_ratio_raw >= VOLUME_CONFIRM_RATIO, volume_ratio_raw, 1.0, None

    session_fraction = hk_session_volume_fraction(now)
    adjusted_ratio = (
        round(volume_ratio_raw / session_fraction, 2)
        if session_fraction > 0
        else volume_ratio_raw
    )
    confirmed = adjusted_ratio >= VOLUME_CONFIRM_RATIO
    note = None
    if is_hk_trading_session(now) and session_fraction < 1.0:
        note = f"量比按已过交易时段 {session_fraction:.0%} 折算为 {adjusted_ratio}"
    return confirmed, adjusted_ratio, session_fraction, note


def resolve_analysis_interval() -> tuple[int, str]:
    if ANALYSIS_INTERVAL_SEC > 0:
        return ANALYSIS_INTERVAL_SEC, f"固定间隔 {ANALYSIS_INTERVAL_SEC} 秒（.env 手动配置）"
    if is_hk_trading_session():
        return INTRADAY_INTERVAL_SEC, (
            f"港股交易时段，自动间隔 {INTRADAY_INTERVAL_SEC // 60} 分钟"
        )
    return OFFHOURS_INTERVAL_SEC, (
        f"非交易时段，自动间隔 {OFFHOURS_INTERVAL_SEC // 3600} 小时"
    )
