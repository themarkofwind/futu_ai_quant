from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from futu_ai_quant.config.settings import (
    ANALYSIS_INTERVAL_SEC,
    INTRADAY_INTERVAL_SEC,
    MIN_SESSION_VOLUME_FRACTION,
    OFFHOURS_INTERVAL_SEC,
    VOLUME_CONFIRM_RATIO,
    VOLUME_FILTER,
)

_US_EASTERN = ZoneInfo("America/New_York")


def is_hk_trading_session(now: datetime | None = None) -> bool:
    """港股交易时段：周一至五 09:30-12:00、13:00-16:00（本地北京时间）。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    morning = (9 * 60 + 30) <= minutes < (12 * 60)
    afternoon = (13 * 60) <= minutes < (16 * 60)
    return morning or afternoon


def is_us_trading_session(now: datetime | None = None) -> bool:
    """
    美股常规交易时段：周一至五 09:30-16:00（美东时间，自动含夏令时）。

    参数 ``now`` 语义：
    - None：取当前美东时间
    - 带时区：自动换算到美东
    - 不带时区：按已是美东时间处理
    """
    if now is None:
        now = datetime.now(_US_EASTERN)
    elif now.tzinfo is not None:
        now = now.astimezone(_US_EASTERN)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 + 30) <= minutes < (16 * 60)


def market_of_code(code: str) -> str:
    """根据标的代码前缀推断市场：返回 'US' 或 'HK'（默认）。"""
    prefix = code.split(".", 1)[0].upper() if "." in code else ""
    if prefix == "US":
        return "US"
    return "HK"


def currency_of_market(market: str) -> str:
    """按市场返回报价货币标签。"""
    if market.upper() == "US":
        return "USD"
    return "HKD"


def session_date_prefix(market: str, now: datetime | None = None) -> str:
    """当前交易日日期前缀（用于过滤日内 K 线）。"""
    if market.upper() == "US":
        if now is None:
            dt = datetime.now(_US_EASTERN)
        elif now.tzinfo is not None:
            dt = now.astimezone(_US_EASTERN)
        else:
            dt = now
        return dt.strftime("%Y-%m-%d")

    dt = now or datetime.now()
    if now and now.tzinfo is not None:
        dt = now.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d")


def is_trading_session(market: str, now: datetime | None = None) -> bool:
    """按市场分发交易时段判断。"""
    if market.upper() == "US":
        return is_us_trading_session(now)
    return is_hk_trading_session(now)


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
