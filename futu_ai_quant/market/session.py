from __future__ import annotations

from datetime import datetime, timedelta
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
_HK_MORNING_OPEN = 9 * 60 + 30
_HK_MORNING_CLOSE = 12 * 60
_HK_AFTERNOON_OPEN = 13 * 60
_HK_AFTERNOON_CLOSE = 16 * 60


def is_hk_trading_session(now: datetime | None = None) -> bool:
    """港股交易时段：周一至五 09:30-12:00、13:00-16:00（本地北京时间）。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    morning = _HK_MORNING_OPEN <= minutes < _HK_MORNING_CLOSE
    afternoon = _HK_AFTERNOON_OPEN <= minutes < _HK_AFTERNOON_CLOSE
    return morning or afternoon


def next_hk_session_open(now: datetime | None = None) -> datetime:
    """
    下一港股可交易开盘时刻。

    - 盘前 → 当日 09:30
    - 午休（12:00–13:00）→ 当日 13:00
    - 收盘后 / 周末 → 下一交易日 09:30
    - 已在交易时段 → 返回 ``now``（调用方应先判断 ``is_hk_trading_session``）
    """
    now = now or datetime.now()
    if is_hk_trading_session(now):
        return now

    day = now.replace(second=0, microsecond=0)
    for _ in range(8):
        if day.weekday() < 5:
            morning = day.replace(hour=9, minute=30, second=0, microsecond=0)
            afternoon = day.replace(hour=13, minute=0, second=0, microsecond=0)
            minutes = day.hour * 60 + day.minute
            if minutes < _HK_MORNING_OPEN:
                return morning
            if _HK_MORNING_CLOSE <= minutes < _HK_AFTERNOON_OPEN:
                return afternoon
            if minutes >= _HK_AFTERNOON_CLOSE:
                day = (day + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            # 理论上不会落到此处（盘中已在上方返回）
            return morning if minutes < _HK_MORNING_CLOSE else afternoon
        day = (day + timedelta(days=1)).replace(hour=0, minute=0)
    return (now + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)


def seconds_until_hk_session(now: datetime | None = None) -> float:
    """距下一港股交易时段的秒数；已在时段内返回 0。"""
    now = now or datetime.now()
    if is_hk_trading_session(now):
        return 0.0
    target = next_hk_session_open(now)
    return max((target - now).total_seconds(), 0.0)


def next_us_session_open(now: datetime | None = None) -> datetime:
    """下一美股常规开盘（美东 09:30）；返回带美东时区的 datetime。"""
    if now is None:
        eastern = datetime.now(_US_EASTERN)
    elif now.tzinfo is not None:
        eastern = now.astimezone(_US_EASTERN)
    else:
        eastern = now.replace(tzinfo=_US_EASTERN)

    if is_us_trading_session(eastern):
        return eastern

    day = eastern.replace(second=0, microsecond=0)
    for _ in range(8):
        if day.weekday() < 5:
            open_at = day.replace(hour=9, minute=30, second=0, microsecond=0)
            minutes = day.hour * 60 + day.minute
            if minutes < (9 * 60 + 30):
                return open_at
            # 已收盘 → 下一天
            day = (day + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        day = (day + timedelta(days=1)).replace(hour=0, minute=0)
    return (eastern + timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)


def seconds_until_trading_session(market: str, now: datetime | None = None) -> float:
    """距指定市场下一交易时段的秒数；已在时段内返回 0。"""
    if market.upper() == "US":
        if is_us_trading_session(now):
            return 0.0
        if now is None:
            now_e = datetime.now(_US_EASTERN)
        elif now.tzinfo is not None:
            now_e = now.astimezone(_US_EASTERN)
        else:
            now_e = now.replace(tzinfo=_US_EASTERN)
        target = next_us_session_open(now_e)
        return max((target - now_e).total_seconds(), 0.0)
    return seconds_until_hk_session(now)


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


def _market_local_now(market: str, now: datetime | None = None) -> datetime:
    """换算到该市场本地时钟（无时区则按本地已是该市场时间）。"""
    if market.upper() == "US":
        if now is None:
            return datetime.now(_US_EASTERN)
        if now.tzinfo is not None:
            return now.astimezone(_US_EASTERN)
        return now.replace(tzinfo=_US_EASTERN)

    if now is None:
        return datetime.now()
    if now.tzinfo is not None:
        return now.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
    return now


def minutes_since_continuous_open(market: str, now: datetime | None = None) -> float | None:
    """
    距当前连续交易小节开盘的分钟数。

    港股：上午 09:30 / 下午 13:00；美股：09:30。非连续交易时段返回 None。
    """
    local = _market_local_now(market, now)
    minutes = local.hour * 60 + local.minute + local.second / 60.0
    if market.upper() == "US":
        if not is_us_trading_session(local):
            return None
        return minutes - (9 * 60 + 30)

    if not is_hk_trading_session(local):
        return None
    if _HK_MORNING_OPEN <= minutes < _HK_MORNING_CLOSE:
        return minutes - _HK_MORNING_OPEN
    if _HK_AFTERNOON_OPEN <= minutes < _HK_AFTERNOON_CLOSE:
        return minutes - _HK_AFTERNOON_OPEN
    return None


def minutes_until_day_close(market: str, now: datetime | None = None) -> float | None:
    """
    距当日常规收盘的分钟数（港股 16:00 / 美股 16:00 美东）。

    午休或不在交易日连续时段时返回 None。
    """
    local = _market_local_now(market, now)
    minutes = local.hour * 60 + local.minute + local.second / 60.0
    if market.upper() == "US":
        if not is_us_trading_session(local):
            return None
        return (16 * 60) - minutes

    if not is_hk_trading_session(local):
        return None
    return (16 * 60) - minutes


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
