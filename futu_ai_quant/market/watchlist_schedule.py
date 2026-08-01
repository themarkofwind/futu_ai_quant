"""港股自选分析三槽调度（盘前竞价 / 午后开盘 / 收盘前半小时）。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from futu_ai_quant.config.watchlist import load_watchlist_slots, slot_label

# 半日市（如圣诞前夕 MORNING）仅允许盘前槽
_AFTERNOON_SLOTS = frozenset({"lunch", "preclose"})


def trade_date_type_allows_slot(slot_key: str, trade_date_type: str | None) -> bool:
    """根据 OpenD ``trade_date_type`` 判断槽位是否应执行。"""
    kind = (trade_date_type or "WHOLE").strip().upper() or "WHOLE"
    if slot_key in _AFTERNOON_SLOTS:
        return kind == "WHOLE"
    # auction / manual：半日市也可跑
    return kind in {"WHOLE", "MORNING"}


def next_watchlist_slot(
    now: datetime | None = None,
    *,
    trading_days: Mapping[str, Mapping[str, str]] | None = None,
    allow_day: Callable[[datetime], bool] | None = None,
) -> tuple[str, datetime, str]:
    """
    计算下一港股推送槽位。

    Parameters
    ----------
    trading_days :
        OpenD ``request_trading_days`` 结果映射 ``YYYY-MM-DD -> {trade_date_type, ...}``。
        提供时跳过非交易日，并按半日市过滤午后槽。
    allow_day :
        兼容旧调用的按日过滤；若同时提供 ``trading_days`` 则以日历为准。

    Returns
    -------
    (slot_key, fire_at, label)
    """
    now = now or datetime.now()
    slots = load_watchlist_slots()
    # 扫描足够天数以跨假期长周末
    for day_offset in range(0, 21):
        day = (now + timedelta(days=day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_key = day.strftime("%Y-%m-%d")

        if trading_days is not None:
            info = trading_days.get(day_key)
            if info is None:
                continue
            trade_type = str(info.get("trade_date_type") or "WHOLE")
        else:
            if day.weekday() >= 5:
                continue
            if allow_day is not None and not allow_day(day):
                continue
            trade_type = "WHOLE"

        for key, hour, minute in slots:
            if trading_days is not None and not trade_date_type_allows_slot(key, trade_type):
                continue
            fire_at = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if fire_at > now:
                return key, fire_at, slot_label(key)

    key, hour, minute = slots[0]
    day = now + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    fire_at = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return key, fire_at, slot_label(key)


def seconds_until(target: datetime, *, now: datetime | None = None) -> float:
    now = now or datetime.now()
    return max(0.0, (target - now).total_seconds())


def should_run_watchlist_slot(
    slot_key: str,
    *,
    trading_days: Mapping[str, Mapping[str, str]] | None,
    day: datetime | None = None,
    api_failed: bool = False,
) -> tuple[bool, str]:
    """
    槽位触发时二次确认是否应执行。

    Returns
    -------
    (should_run, reason)
    """
    day = day or datetime.now()
    day_key = day.strftime("%Y-%m-%d")

    if trading_days is None:
        if api_failed:
            # API 失败：回退周一至五，避免假期误跑不了也避免工作日全停
            if day.weekday() >= 5:
                return False, "周末且交易日历接口失败，跳过"
            return True, "交易日历接口失败，回退本地工作日"
        return True, "未提供交易日历"

    info = trading_days.get(day_key)
    if info is None:
        return False, f"{day_key} 非港股交易日（OpenD request_trading_days），跳过"
    trade_type = str(info.get("trade_date_type") or "WHOLE")
    if not trade_date_type_allows_slot(slot_key, trade_type):
        return False, (
            f"{day_key} 为半日市({trade_type})，槽位 {slot_key} 不适用，跳过"
        )
    return True, f"{day_key} 交易日 type={trade_type}"
