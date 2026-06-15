from __future__ import annotations

from futu_ai_quant.config.settings import (
    SWING_COMMISSION_RATE,
    SWING_MIN_COMMISSION,
    SWING_MIN_PROFIT_COST_RATIO,
    SWING_PLATFORM_FEE,
    SWING_STAMP_DUTY_RATE,
)


def estimate_hk_stock_trade_fees(side: str, gross_amount: float) -> float:
    commission = max(gross_amount * SWING_COMMISSION_RATE, SWING_MIN_COMMISSION)
    stamp = gross_amount * SWING_STAMP_DUTY_RATE if side == "sell" else 0.0
    return round(commission + SWING_PLATFORM_FEE + stamp, 2)


def swing_trade_meets_cost_threshold(
    *,
    direction: str,
    suggested_qty: int,
    market_price: float | None,
    atr_market: float | None,
) -> tuple[bool, str | None]:
    if suggested_qty <= 0 or market_price is None:
        return True, None
    gross = market_price * suggested_qty
    fees = estimate_hk_stock_trade_fees(direction, gross)
    if atr_market is None:
        return True, None
    expected_benefit = atr_market * suggested_qty
    min_required = fees * SWING_MIN_PROFIT_COST_RATIO
    if expected_benefit < min_required:
        return False, (
            f"预期波段空间约 {expected_benefit:.0f} HKD，"
            f"不足以覆盖预估费用 {fees:.0f} HKD × {SWING_MIN_PROFIT_COST_RATIO:g}"
        )
    return True, None
