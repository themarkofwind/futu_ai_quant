from __future__ import annotations

from typing import Any

from futu_ai_quant.indicators.technical import scale_atr_to_market
from futu_ai_quant.market.fees import (
    swing_trade_meets_cost_threshold,
)
from futu_ai_quant.market.lot import calc_full_lot_trade_qty, resolve_lot_size
from futu_ai_quant.utils.numbers import safe_float


def apply_swing_trade_to_plan(
    plan: dict[str, Any],
    *,
    direction: str,
    suggested_qty: int,
    suggested_lots: int,
    lot_size: int,
    holding_qty: float,
    market_price: float | None,
    atr_market: float | None,
    capacity_note: str | None,
) -> None:
    if suggested_qty <= 0:
        plan["trade_note"] = capacity_note
        return
    meets_cost, cost_note = swing_trade_meets_cost_threshold(
        direction=direction,
        suggested_qty=suggested_qty,
        market_price=market_price,
        atr_market=atr_market,
    )
    if not meets_cost:
        plan["trade_note"] = cost_note
        return
    verb = "卖出" if direction == "sell" else "买入"
    plan.update(
        {
            "direction": direction,
            "suggested_qty": suggested_qty,
            "suggested_lots": suggested_lots,
            "pct_of_holding": round(suggested_qty / abs(holding_qty) * 100, 2) if holding_qty else 0.0,
            "trade_note": f"建议{verb} {suggested_lots} 手（{suggested_qty} 股，每手 {lot_size} 股）",
        }
    )


def build_stock_trade_plan(
    stock: dict[str, Any],
    swing_strategy: dict[str, Any],
    combined_signal: dict[str, Any],
    snapshot: dict[str, Any] | None,
    pnl: dict[str, Any],
) -> dict[str, Any]:
    qty = safe_float(stock.get("qty")) or 0.0
    can_sell = safe_float(stock.get("can_sell_qty")) or qty
    lot_size = resolve_lot_size(snapshot, stock)
    max_pct = float(swing_strategy.get("max_swing_position_pct") or 10)
    market_price = safe_float(pnl.get("market_price"))
    signal = combined_signal.get("effective_signal", combined_signal.get("primary_signal", "HOLD"))

    plan: dict[str, Any] = {
        "current_qty": int(qty),
        "can_sell_qty": int(can_sell),
        "lot_size": lot_size,
        "shares_per_lot": lot_size,
        "current_lots": int(qty // lot_size) if lot_size else 0,
        "can_sell_lots": int(can_sell // lot_size) if lot_size else 0,
        "max_swing_position_pct": max_pct,
        "direction": "none",
        "suggested_qty": 0,
        "suggested_lots": 0,
        "pct_of_holding": 0.0,
        "trigger_price_low": None,
        "trigger_price_high": None,
        "atr_used": None,
        "trade_note": None,
    }

    daily = stock.get("daily") or {}
    atr_market = scale_atr_to_market(
        safe_float(daily.get("atr")),
        safe_float(daily.get("technical_close")),
        market_price,
    )
    if atr_market is not None:
        plan["atr_used"] = atr_market

    if market_price is not None:
        if signal == "SELL_SWING":
            if atr_market is not None:
                plan["trigger_price_low"] = round(market_price + 0.5 * atr_market, 3)
                plan["trigger_price_high"] = round(market_price + 1.5 * atr_market, 3)
            else:
                plan["trigger_price_low"] = round(market_price * 1.01, 3)
                plan["trigger_price_high"] = round(market_price * 1.04, 3)
        elif signal == "BUY_SWING":
            if atr_market is not None:
                plan["trigger_price_low"] = round(market_price - 1.5 * atr_market, 3)
                plan["trigger_price_high"] = round(market_price - 0.5 * atr_market, 3)
            else:
                plan["trigger_price_low"] = round(market_price * 0.96, 3)
                plan["trigger_price_high"] = round(market_price * 0.99, 3)

    if signal == "SELL_SWING" and can_sell >= lot_size:
        suggested_qty, suggested_lots, note = calc_full_lot_trade_qty(
            qty, can_sell, lot_size, max_pct, for_sell=True
        )
        apply_swing_trade_to_plan(
            plan,
            direction="sell",
            suggested_qty=suggested_qty,
            suggested_lots=suggested_lots,
            lot_size=lot_size,
            holding_qty=qty,
            market_price=market_price,
            atr_market=atr_market,
            capacity_note=note,
        )
    elif signal == "BUY_SWING":
        suggested_qty, suggested_lots, note = calc_full_lot_trade_qty(
            qty, qty, lot_size, max_pct, for_sell=False
        )
        apply_swing_trade_to_plan(
            plan,
            direction="buy",
            suggested_qty=suggested_qty,
            suggested_lots=suggested_lots,
            lot_size=lot_size,
            holding_qty=qty,
            market_price=market_price,
            atr_market=atr_market,
            capacity_note=note,
        )

    return plan


def empty_stock_trade_plan() -> dict[str, Any]:
    return {
        "direction": "none",
        "suggested_qty": 0,
        "suggested_lots": 0,
        "lot_size": None,
        "pct_of_holding": 0.0,
        "trigger_price_low": None,
        "trigger_price_high": None,
    }
