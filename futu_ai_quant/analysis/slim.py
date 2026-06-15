"""
将完整 portfolio_payload 精简为发给 DeepSeek 的版本。

原则：只删冗余与已汇总字段；``data/payloads`` 仍保存全量，本模块仅用于 API 输入。
"""

from __future__ import annotations

from typing import Any

from futu_ai_quant.utils.numbers import safe_float

_STOCK_TRADE_PLAN_KEEP = frozenset(
    {
        "direction",
        "suggested_qty",
        "suggested_lots",
        "lot_size",
        "pct_of_holding",
        "trigger_price_low",
        "trigger_price_high",
        "atr_used",
        "trade_note",
        "plan_source",
        "skip_reason",
    }
)

_OPTION_TRADE_PLAN_KEEP = frozenset(
    {
        "action",
        "plan_source",
        "contract_code",
        "expire_date",
        "strike_price",
        "days_to_expiry",
        "delta",
        "contracts",
        "contract_size",
        "implied_volatility",
        "iv_relative",
        "iv_rank",
        "premium_per_share",
        "skip_reason",
    }
)

_TIMEFRAME_KEEP = frozenset(
    {
        "timeframe",
        "swing_signal",
        "macd_bias",
        "boll_position",
        "volume_confirmed",
        "volume_ratio",
        "atr",
        "rsi",
        "error",
    }
)

_PNL_KEEP = frozenset(
    {
        "market_price",
        "pl_ratio",
        "pl_val",
        "cost_gap_pct",
        "today_change_pct",
    }
)

_EXISTING_OPTION_KEEP = frozenset(
    {
        "code",
        "position_direction",
        "qty",
        "strike_price",
        "expire_time",
        "option_type",
        "last_price",
        "implied_volatility",
        "delta",
        "theta",
        "option_trade_plan",
    }
)

_OPTION_POSITION_KEEP = frozenset(
    {
        "code",
        "name",
        "position_direction",
        "position_side",
        "qty",
        "strike_price",
        "expire_time",
        "option_type",
        "last_price",
        "implied_volatility",
        "delta",
        "theta",
        "days_to_expiry",
        "option_trade_plan",
    }
)


def _round_num(value: Any, places: int = 4) -> Any:
    num = safe_float(value)
    if num is None:
        return value
    return round(num, places)


def _short_error(value: Any, max_len: int = 160) -> Any:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    first_line = text.split("\n", 1)[0]
    if len(first_line) > max_len:
        return first_line[: max_len - 3] + "..."
    return first_line


def _pick_fields(data: dict[str, Any], keys: frozenset[str]) -> dict[str, Any]:
    return {key: data[key] for key in keys if key in data and data[key] is not None}


def _slim_timeframe(frame: dict[str, Any] | None) -> dict[str, Any] | None:
    if not frame:
        return None
    slim = _pick_fields(frame, _TIMEFRAME_KEEP)
    if slim.get("rsi") is not None:
        slim["rsi"] = _round_num(slim["rsi"], 2)
    if slim.get("atr") is not None:
        slim["atr"] = _round_num(slim["atr"], 4)
    if slim.get("volume_ratio") is not None:
        slim["volume_ratio"] = _round_num(slim["volume_ratio"], 2)
    if "error" in slim:
        slim["error"] = _short_error(slim["error"])
    return slim or None


def _slim_pnl(pnl: dict[str, Any] | None) -> dict[str, Any] | None:
    if not pnl:
        return None
    slim = _pick_fields(pnl, _PNL_KEEP)
    if slim.get("pl_ratio") is not None:
        slim["pl_ratio"] = _round_num(slim["pl_ratio"], 2)
    if slim.get("cost_gap_pct") is not None:
        slim["cost_gap_pct"] = _round_num(slim["cost_gap_pct"], 2)
    if slim.get("today_change_pct") is not None:
        slim["today_change_pct"] = _round_num(slim["today_change_pct"], 2)
    return slim or None


def _slim_stock_trade_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    return _pick_fields(plan, _STOCK_TRADE_PLAN_KEEP) or None


def _slim_option_trade_plan(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan:
        return None
    slim = _pick_fields(plan, _OPTION_TRADE_PLAN_KEEP)
    for key in ("strike_price", "delta", "premium_per_share", "implied_volatility", "iv_rank", "iv_relative"):
        if key in slim:
            slim[key] = _round_num(slim[key], 4)
    return slim or None


def _slim_option_overlay(
    overlay: dict[str, Any] | None,
    option_trade_plan: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not overlay:
        return None
    scan_note = overlay.get("scan_note")
    has_plan = bool(option_trade_plan and option_trade_plan.get("action") not in (None, "none"))
    if has_plan:
        if scan_note:
            return {"scan_note": _short_error(scan_note)}
        return None
    if scan_note:
        return {"scan_note": _short_error(scan_note)}
    if not (overlay.get("sell_call_candidates") or overlay.get("sell_put_candidates")):
        return None
    return {"scan_note": "无卖权候选或未生成建议方案"}


def _slim_trade_history(history: dict[str, Any] | None) -> dict[str, Any] | None:
    if not history:
        return None
    recent = history.get("recent_swing_window") or {}
    ytd = history.get("ytd_summary") or {}
    slim_ytd = {
        key: ytd[key]
        for key in (
            "trade_count",
            "buy_count",
            "sell_count",
            "buy_qty",
            "sell_qty",
            "avg_buy_price",
            "avg_sell_price",
            "net_qty_change",
            "last_trade",
        )
        if key in ytd and ytd[key] is not None
    }
    return {
        "recent_swing_days": history.get("recent_swing_days"),
        "ytd_summary": slim_ytd,
        "ytd_option_trade_count": history.get("ytd_option_trade_count"),
        "recent_swing_window": {
            "stock_trades": recent.get("stock_trades") or [],
            "option_trades": recent.get("option_trades") or [],
            "stock_trade_count": recent.get("stock_trade_count", 0),
            "option_trade_count": recent.get("option_trade_count", 0),
        },
        "swing_hint": history.get("swing_hint"),
    }


def _slim_existing_option(option: dict[str, Any]) -> dict[str, Any]:
    slim = _pick_fields(option, _EXISTING_OPTION_KEEP)
    plan = option.get("option_trade_plan")
    if plan:
        slim["option_trade_plan"] = _slim_option_trade_plan(plan)
    return slim


def slim_stock_for_ai(stock: dict[str, Any]) -> dict[str, Any]:
    option_plan = stock.get("option_trade_plan")
    slim: dict[str, Any] = {
        "code": stock.get("code"),
        "name": stock.get("name"),
        "position_direction": stock.get("position_direction"),
        "lot_size": stock.get("lot_size"),
        "shares_per_lot": stock.get("shares_per_lot"),
        "pnl": _slim_pnl(stock.get("pnl")),
        "swing_strategy": stock.get("swing_strategy"),
        "daily": _slim_timeframe(stock.get("daily")),
        "weekly": _slim_timeframe(stock.get("weekly")),
        "combined_swing_signal": stock.get("combined_swing_signal"),
        "stock_trade_plan": _slim_stock_trade_plan(stock.get("stock_trade_plan")),
        "option_trade_plan": _slim_option_trade_plan(option_plan),
        "option_overlay": _slim_option_overlay(stock.get("option_overlay"), option_plan),
        "trade_history": _slim_trade_history(stock.get("trade_history")),
        "existing_option_positions": [
            _slim_existing_option(item) for item in (stock.get("existing_option_positions") or [])
        ],
    }
    indicator_error = _short_error(stock.get("indicator_error"))
    if indicator_error:
        slim["indicator_error"] = indicator_error
    return {key: value for key, value in slim.items() if value is not None}


def slim_option_for_ai(option: dict[str, Any]) -> dict[str, Any]:
    slim = _pick_fields(option, _OPTION_POSITION_KEEP)
    for key in ("last_price", "implied_volatility", "delta", "theta", "strike_price"):
        if key in slim:
            slim[key] = _round_num(slim[key], 4)
    plan = option.get("option_trade_plan")
    if plan:
        slim["option_trade_plan"] = _slim_option_trade_plan(plan)
    theta_effect = option.get("theta_position_effect")
    if theta_effect:
        slim["theta_position_effect"] = theta_effect
    return slim


def slim_portfolio_for_ai(portfolio_payload: dict[str, Any]) -> dict[str, Any]:
    """生成发给 DeepSeek 的精简 portfolio；全量 payload 仍写入 ``data/payloads``。"""
    risk = portfolio_payload.get("portfolio_risk") or {}
    return {
        "as_of": portfolio_payload.get("as_of"),
        "market": portfolio_payload.get("market"),
        "required_positions": portfolio_payload.get("required_positions"),
        "summary": portfolio_payload.get("summary"),
        "portfolio_risk": {
            "total_stock_market_val": risk.get("total_stock_market_val"),
            "max_single_weight_pct": risk.get("max_single_weight_pct"),
            "concentration_alerts": risk.get("concentration_alerts") or [],
        },
        "stocks": [slim_stock_for_ai(stock) for stock in portfolio_payload.get("stocks", [])],
        "options": [slim_option_for_ai(opt) for opt in portfolio_payload.get("options", [])],
    }
