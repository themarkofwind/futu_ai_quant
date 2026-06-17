"""日内做 T 目标价差：按手续费与股数自动抬高下限。"""

from __future__ import annotations

import math

from futu import OpenQuoteContext

from futu_ai_quant.brokers.futu.quotes import fetch_snapshot_map
from futu_ai_quant.market.fees import estimate_hk_stock_trade_fees
from futu_ai_quant.market.session import currency_of_market, market_of_code
from futu_ai_quant.strategy.intraday_t_settings import (
    INTRADAY_T_MIN_PROFIT_COST_RATIO,
    INTRADAY_T_TARGET_SPREAD,
    INTRADAY_T_TARGET_SPREAD_AUTO,
    INTRADAY_T_US_COMMISSION_PER_SHARE,
    INTRADAY_T_US_MIN_COMMISSION,
    INTRADAY_T_US_PLATFORM_FEE,
)
from futu_ai_quant.utils.numbers import safe_float


def _round_up_spread(value: float) -> float:
    return math.ceil(value * 100) / 100


def estimate_us_stock_trade_fees(*, shares: int) -> float:
    """美股单边交易费用估算（佣金 + 平台费）。"""
    commission = max(shares * INTRADAY_T_US_COMMISSION_PER_SHARE, INTRADAY_T_US_MIN_COMMISSION)
    return round(commission + INTRADAY_T_US_PLATFORM_FEE, 4)


def estimate_round_trip_t_fees(code: str, price: float, lot_size: int) -> float:
    """估算一轮做 T（卖 + 买）总费用。"""
    if price <= 0 or lot_size <= 0:
        return 0.0

    gross = price * lot_size
    if market_of_code(code) == "US":
        one_side = estimate_us_stock_trade_fees(shares=lot_size)
        return round(one_side * 2, 4)

    sell_fees = estimate_hk_stock_trade_fees("sell", gross)
    buy_fees = estimate_hk_stock_trade_fees("buy", gross)
    return round(sell_fees + buy_fees, 4)


def min_target_spread_from_fees(
    fees: float,
    lot_size: int,
    *,
    cost_ratio: float = INTRADAY_T_MIN_PROFIT_COST_RATIO,
) -> float:
    """根据往返费用与股数，计算每股最低目标价差。"""
    if lot_size <= 0 or fees <= 0:
        return 0.0
    return _round_up_spread((fees / lot_size) * cost_ratio)


def resolve_price_for_cost(quote_ctx: OpenQuoteContext, code: str) -> float | None:
    snapshot = fetch_snapshot_map(quote_ctx, [code]).get(code, {})
    return safe_float(snapshot.get("last_price")) or safe_float(snapshot.get("cur_price"))


def resolve_intraday_t_target_spread(
    quote_ctx: OpenQuoteContext,
    code: str,
    *,
    lot_size: int,
    manual_spread: float = INTRADAY_T_TARGET_SPREAD,
    cost_ratio: float = INTRADAY_T_MIN_PROFIT_COST_RATIO,
    auto: bool = INTRADAY_T_TARGET_SPREAD_AUTO,
) -> tuple[float, str]:
    """
    解析有效目标价差：``max(手动配置, 费用保本×安全系数)``。

    返回 (target_spread, 说明)。
    """
    currency = currency_of_market(market_of_code(code))
    if not auto or lot_size <= 0:
        return manual_spread, f"目标净价差 {manual_spread:.2f} {currency}（未启用费用自动校正）"

    price = resolve_price_for_cost(quote_ctx, code)
    if price is None:
        return manual_spread, f"现价未知，目标净价差 {manual_spread:.2f} {currency}"

    fees = estimate_round_trip_t_fees(code, price, lot_size)
    min_spread = min_target_spread_from_fees(fees, lot_size, cost_ratio=cost_ratio)
    effective = max(manual_spread, min_spread)
    breakeven = fees / lot_size

    parts = [
        f"现价 {price:.3f} {currency}",
        f"做T {lot_size} 股",
        f"预估往返费用 {fees:.2f} {currency}",
        f"保本价差 {breakeven:.3f}/股",
        f"按 {cost_ratio:g} 倍安全系数最低 {min_spread:.2f}/股",
        f"目标净价差 {effective:.2f} {currency}",
    ]
    if effective > manual_spread:
        parts.append(f"（已高于配置值 {manual_spread:.2f}）")
    return effective, " | ".join(parts)
