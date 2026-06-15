"""触发价区间判断（分析计划与模拟挂单共用）。"""

from __future__ import annotations


def price_in_trigger(
    price: float | None,
    low: float | None,
    high: float | None,
) -> bool:
    if price is None:
        return False
    if low is None and high is None:
        return True
    if low is not None and high is not None:
        return low <= price <= high
    if low is not None:
        return price >= low
    if high is not None:
        return price <= high
    return False
