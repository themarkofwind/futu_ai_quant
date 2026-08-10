from __future__ import annotations

from typing import Any

from futu_ai_quant.analysis.data_quality import apply_data_quality_to_trade_plan
from futu_ai_quant.indicators.technical import (
    scale_atr_to_market,
    scale_price_level_to_market,
)
from futu_ai_quant.market.fees import (
    swing_trade_meets_cost_threshold,
)
from futu_ai_quant.market.lot import calc_full_lot_trade_qty, resolve_lot_size_detail
from futu_ai_quant.utils.numbers import safe_float

# 持仓波段：相对宽的 ATR 区间（兼容原逻辑）
_HOLDING_NEAR_ATR = 0.5
_HOLDING_FAR_ATR = 1.5
# 自选观察：以锚点为中心的窄带（半宽上限）
_WATCHLIST_HALF_ATR = 0.25
_WATCHLIST_HALF_MAX_PCT = 0.006  # 现价的 0.6%
_WATCHLIST_ANCHOR_ATR = 0.5
# 布林锚点相对现价最大偏离；超出则退回 ATR，避免复权/过期日K把参考价拉飞
_WATCHLIST_BOLL_MAX_DEV_PCT = 0.10


def _sell_swing_band(
    market_price: float,
    atr_market: float | None,
) -> tuple[float, float]:
    if atr_market is not None:
        return (
            round(market_price + _HOLDING_NEAR_ATR * atr_market, 3),
            round(market_price + _HOLDING_FAR_ATR * atr_market, 3),
        )
    return round(market_price * 1.01, 3), round(market_price * 1.04, 3)


def _buy_swing_band(
    market_price: float,
    atr_market: float | None,
) -> tuple[float, float]:
    if atr_market is not None:
        return (
            round(market_price - _HOLDING_FAR_ATR * atr_market, 3),
            round(market_price - _HOLDING_NEAR_ATR * atr_market, 3),
        )
    return round(market_price * 0.96, 3), round(market_price * 0.99, 3)


def _band_half_width(market_price: float, atr_market: float | None) -> float:
    atr_half = (
        _WATCHLIST_HALF_ATR * atr_market if atr_market is not None and atr_market > 0 else None
    )
    pct_half = market_price * _WATCHLIST_HALF_MAX_PCT
    if atr_half is None:
        return round(pct_half, 4)
    return round(min(atr_half, pct_half), 4)


def _centered_band(center: float, half: float) -> tuple[float, float]:
    return round(center - half, 3), round(center + half, 3)


def _boll_anchor_usable(market_price: float, level: float | None) -> bool:
    if level is None or market_price <= 0:
        return False
    return abs(level - market_price) / market_price <= _WATCHLIST_BOLL_MAX_DEV_PCT


def _scale_watchlist_boll(
    boll_lower: float | None,
    boll_upper: float | None,
    *,
    technical_close: float | None,
    market_price: float | None,
) -> tuple[float | None, float | None]:
    return (
        scale_price_level_to_market(boll_lower, technical_close, market_price),
        scale_price_level_to_market(boll_upper, technical_close, market_price),
    )


def _watchlist_buy_band(
    market_price: float,
    atr_market: float | None,
    *,
    boll_lower: float | None = None,
) -> tuple[float, float, float]:
    """返回 (low, high, preferred)。优先锚近端布林下轨，否则 ATR。"""
    if (
        boll_lower is not None
        and boll_lower < market_price
        and _boll_anchor_usable(market_price, boll_lower)
    ):
        center = boll_lower
    elif atr_market is not None:
        center = market_price - _WATCHLIST_ANCHOR_ATR * atr_market
    else:
        center = market_price * 0.98
    half = _band_half_width(market_price, atr_market)
    low, high = _centered_band(center, half)
    return low, high, round(center, 3)


def _watchlist_sell_band(
    market_price: float,
    atr_market: float | None,
    *,
    boll_upper: float | None = None,
) -> tuple[float, float, float]:
    """返回 (low, high, preferred)。优先锚近端布林上轨，否则 ATR。"""
    if (
        boll_upper is not None
        and boll_upper > market_price
        and _boll_anchor_usable(market_price, boll_upper)
    ):
        center = boll_upper
    elif atr_market is not None:
        center = market_price + _WATCHLIST_ANCHOR_ATR * atr_market
    else:
        center = market_price * 1.02
    half = _band_half_width(market_price, atr_market)
    low, high = _centered_band(center, half)
    return low, high, round(center, 3)


def attach_watch_triggers(
    plan: dict[str, Any],
    swing_strategy: dict[str, Any],
    *,
    market_price: float,
    atr_market: float | None,
    boll_lower: float | None = None,
    boll_upper: float | None = None,
) -> None:
    """HOLD/WAIT 时给出条件观望参考价（不生成实际挂单数量）。"""
    tier = str(swing_strategy.get("loss_tier") or "moderate_loss")
    watches: list[dict[str, Any]] = []

    if tier == "watchlist":
        buy_low, buy_high, buy_pref = _watchlist_buy_band(
            market_price, atr_market, boll_lower=boll_lower
        )
        sell_low, sell_high, sell_pref = _watchlist_sell_band(
            market_price, atr_market, boll_upper=boll_upper
        )
        watches.append(
            {
                "side": "buy",
                "price_low": buy_low,
                "price_high": buy_high,
                "preferred_price": buy_pref,
                "note": "回调至附近可考虑买入/回补",
            }
        )
        watches.append(
            {
                "side": "sell",
                "price_low": sell_low,
                "price_high": sell_high,
                "preferred_price": sell_pref,
                "note": "反弹至附近可考虑卖出/做空",
            }
        )
        plan["watch_triggers"] = watches
        return

    if tier in ("moderate_loss", "deep_loss"):
        low, high = _buy_swing_band(market_price, atr_market)
        watches.append(
            {
                "side": "buy",
                "price_low": low,
                "price_high": high,
                "note": "回调至此区间可考虑低吸降本",
            }
        )
    if tier in ("moderate_loss", "profitable"):
        low, high = _sell_swing_band(market_price, atr_market)
        watches.append(
            {
                "side": "sell",
                "price_low": low,
                "price_high": high,
                "note": "反弹至此区间可考虑减仓",
            }
        )

    plan["watch_triggers"] = watches


def format_price_band(low: Any, high: Any, preferred: Any = None) -> str:
    if preferred is not None and low is not None and high is not None:
        return f"{preferred}（{low}-{high}）"
    if low is not None and high is not None:
        return f"{low}-{high}"
    return ""


def format_watch_triggers(plan: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in plan.get("watch_triggers") or []:
        if not isinstance(item, dict):
            continue
        low = item.get("price_low")
        high = item.get("price_high")
        if low is None or high is None:
            continue
        side = item.get("side")
        if side == "sell":
            label = "卖出参考"
        elif side == "buy":
            label = "买入参考"
        else:
            label = "参考"
        note = str(item.get("note") or "").strip()
        band = format_price_band(low, high, item.get("preferred_price"))
        chunk = f"{label} {band}"
        if note:
            chunk = f"{chunk}（{note}）"
        parts.append(chunk)
    return "；".join(parts)


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
    trade_note = f"建议{verb} {suggested_lots} 手（{suggested_qty} 股，每手 {lot_size} 股）"
    if capacity_note:
        trade_note = f"{trade_note}；{capacity_note}"
    plan.update(
        {
            "direction": direction,
            "suggested_qty": suggested_qty,
            "suggested_lots": suggested_lots,
            "pct_of_holding": round(suggested_qty / abs(holding_qty) * 100, 2) if holding_qty else 0.0,
            "trade_note": trade_note,
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
    lot_size, lot_confirmed = resolve_lot_size_detail(snapshot, stock)
    if stock.get("lot_confirmed") is True:
        lot_confirmed = True
        lot_size = int(stock.get("lot_size") or lot_size)
    max_pct = float(swing_strategy.get("max_swing_position_pct") or 10)
    risk_limits = stock.get("risk_limits") or {}
    if risk_limits.get("adjusted_max_swing_pct") is not None:
        max_pct = float(risk_limits["adjusted_max_swing_pct"])
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
        "tier_max_swing_pct": risk_limits.get("tier_max_swing_pct"),
        "direction": "none",
        "suggested_qty": 0,
        "suggested_lots": 0,
        "pct_of_holding": 0.0,
        "trigger_price_low": None,
        "trigger_price_high": None,
        "watch_triggers": [],
        "atr_used": None,
        "trade_note": None,
        "lot_confirmed": lot_confirmed,
    }

    if not lot_confirmed and not (
        str(stock.get("position_type") or "") == "WATCHLIST"
        or str(swing_strategy.get("loss_tier") or "") == "watchlist"
    ):
        plan["trade_note"] = "每手股数未从行情确认，暂不生成交易数量"
        apply_data_quality_to_trade_plan(plan, stock)
        return plan

    daily = stock.get("daily") or {}
    technical_close = safe_float(daily.get("technical_close"))
    atr_market = scale_atr_to_market(
        safe_float(daily.get("atr")),
        technical_close,
        market_price,
    )
    if atr_market is not None:
        plan["atr_used"] = atr_market

    boll_lower = safe_float(daily.get("boll_lower"))
    boll_upper = safe_float(daily.get("boll_upper"))
    is_watchlist = str(stock.get("position_type") or "") == "WATCHLIST" or (
        str(swing_strategy.get("loss_tier") or "") == "watchlist"
    )
    if is_watchlist and market_price is not None:
        # 前复权布林轨 → 未复权现价空间，再交给近端偏离校验
        boll_lower, boll_upper = _scale_watchlist_boll(
            boll_lower,
            boll_upper,
            technical_close=technical_close,
            market_price=market_price,
        )

    if market_price is not None:
        if signal == "SELL_SWING":
            if is_watchlist:
                low, high, pref = _watchlist_sell_band(
                    market_price, atr_market, boll_upper=boll_upper
                )
                plan["preferred_trigger_price"] = pref
            else:
                low, high = _sell_swing_band(market_price, atr_market)
            plan["trigger_price_low"] = low
            plan["trigger_price_high"] = high
        elif signal == "BUY_SWING":
            if is_watchlist:
                low, high, pref = _watchlist_buy_band(
                    market_price, atr_market, boll_lower=boll_lower
                )
                plan["preferred_trigger_price"] = pref
            else:
                low, high = _buy_swing_band(market_price, atr_market)
            plan["trigger_price_low"] = low
            plan["trigger_price_high"] = high

    if is_watchlist and market_price is not None:
        # 自选：不按持仓数量下单，只给方向 + 股价区间
        watch_kwargs = {
            "market_price": market_price,
            "atr_market": atr_market,
            "boll_lower": boll_lower,
            "boll_upper": boll_upper,
        }
        if signal == "SELL_SWING":
            plan["direction"] = "sell"
            band = format_price_band(
                plan.get("trigger_price_low"),
                plan.get("trigger_price_high"),
                plan.get("preferred_trigger_price"),
            )
            plan["trade_note"] = f"技术面提示卖出/做空，参考区间 {band}"
            attach_watch_triggers(plan, swing_strategy, **watch_kwargs)
        elif signal == "BUY_SWING":
            plan["direction"] = "buy"
            band = format_price_band(
                plan.get("trigger_price_low"),
                plan.get("trigger_price_high"),
                plan.get("preferred_trigger_price"),
            )
            plan["trade_note"] = f"技术面提示买入/回补，参考区间 {band}"
            attach_watch_triggers(plan, swing_strategy, **watch_kwargs)
        elif signal in ("HOLD", "WAIT"):
            attach_watch_triggers(plan, swing_strategy, **watch_kwargs)
            plan["trade_note"] = "暂无明确方向，附买入/卖出参考价"
        apply_data_quality_to_trade_plan(plan, stock)
        return plan

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
    elif signal in ("HOLD", "WAIT") and market_price is not None and plan["direction"] == "none":
        attach_watch_triggers(
            plan,
            swing_strategy,
            market_price=market_price,
            atr_market=atr_market,
        )

    apply_data_quality_to_trade_plan(plan, stock)
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
        "watch_triggers": [],
    }


def overlay_intraday_onto_daily_for_plan(
    daily: dict[str, Any],
    intraday: dict[str, Any],
) -> dict[str, Any]:
    """盘中槽位：用分钟K的 ATR/布林/收盘覆盖日K，保持技术价同源。"""
    return {
        **daily,
        "atr": intraday.get("atr"),
        "technical_close": intraday.get("technical_close") or daily.get("technical_close"),
        "boll_upper": intraday.get("boll_upper") or daily.get("boll_upper"),
        "boll_mid": intraday.get("boll_mid") or daily.get("boll_mid"),
        "boll_lower": intraday.get("boll_lower") or daily.get("boll_lower"),
    }
