from __future__ import annotations

import time
from typing import Any

from futu_ai_quant.config.settings import PORTFOLIO_MAX_SINGLE_WEIGHT_PCT
from futu_ai_quant.domain.positions import resolve_option_underlying_code
from futu_ai_quant.planning.option import empty_option_trade_plan
from futu_ai_quant.utils.numbers import safe_float


def summarize_existing_option_position(option: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": option.get("code"),
        "name": option.get("name"),
        "position_direction": option.get("position_direction"),
        "position_side": option.get("position_side"),
        "qty": option.get("qty"),
        "abs_qty": option.get("abs_qty"),
        "strike_price": option.get("strike_price"),
        "expire_time": option.get("expire_time"),
        "option_type": option.get("option_type"),
        "last_price": option.get("last_price"),
        "implied_volatility": option.get("implied_volatility"),
        "delta": option.get("delta"),
        "theta": option.get("theta"),
        "stock_owner": option.get("stock_owner"),
        "option_trade_plan": option.get("option_trade_plan"),
    }


def attach_stock_option_context(
    stocks: list[dict[str, Any]],
    options: list[dict[str, Any]],
) -> None:
    """将已有期权挂载到对应正股，并标注建议卖权方案来源。"""
    existing_by_stock: dict[str, list[dict[str, Any]]] = {}
    for opt in options:
        underlying = resolve_option_underlying_code(opt)
        if not underlying:
            continue
        existing_by_stock.setdefault(underlying, []).append(
            summarize_existing_option_position(opt)
        )

    for stock in stocks:
        stock_code = stock["code"]
        stock["existing_option_positions"] = existing_by_stock.get(stock_code, [])
        held_short_codes = {
            str(item.get("code"))
            for item in stock["existing_option_positions"]
            if str(item.get("position_side", "")).upper() == "SHORT"
        }

        suggested = stock.get("option_trade_plan")
        if not suggested:
            continue

        suggested = {**suggested, "plan_source": "suggested"}
        contract_code = str(suggested.get("contract_code") or "")
        if contract_code and contract_code in held_short_codes:
            suggested = {
                **empty_option_trade_plan(),
                "plan_source": "suggested_skipped",
                "skip_reason": f"已有卖方持仓 {contract_code}，不再重复建议",
                "skipped_suggestion": suggested,
            }
        stock["option_trade_plan"] = suggested


def build_portfolio_risk_overlay(stocks: list[dict[str, Any]]) -> dict[str, Any]:
    total_mv = sum(safe_float(stock.get("market_val")) or 0.0 for stock in stocks)
    position_weights: list[dict[str, Any]] = []
    concentration_alerts: list[str] = []

    for stock in stocks:
        market_val = safe_float(stock.get("market_val")) or 0.0
        weight_pct = round(market_val / total_mv * 100, 2) if total_mv > 0 else 0.0
        position_weights.append(
            {
                "code": stock.get("code"),
                "name": stock.get("name"),
                "market_val": market_val,
                "weight_pct": weight_pct,
                "loss_tier": (stock.get("swing_strategy") or {}).get("loss_tier"),
            }
        )
        if total_mv > 0 and weight_pct > PORTFOLIO_MAX_SINGLE_WEIGHT_PCT:
            concentration_alerts.append(
                f"{stock.get('code')} 市值占比 {weight_pct}% "
                f"超过上限 {PORTFOLIO_MAX_SINGLE_WEIGHT_PCT:g}%"
            )

    position_weights.sort(key=lambda item: item.get("weight_pct") or 0, reverse=True)
    return {
        "total_stock_market_val": round(total_mv, 2),
        "max_single_weight_pct": PORTFOLIO_MAX_SINGLE_WEIGHT_PCT,
        "position_weights": position_weights,
        "concentration_alerts": concentration_alerts,
    }


def build_portfolio_payload(
    stocks: list[dict[str, Any]],
    options: list[dict[str, Any]],
    *,
    dynamic_risk: dict[str, Any] | None = None,
    analyst_summary: dict[str, Any] | None = None,
    macro_risk: dict[str, Any] | None = None,
) -> dict[str, Any]:
    required_positions = [
        {
            "code": item["code"],
            "name": item.get("name", ""),
            "asset_type": "stock",
            "position_direction": item.get("position_direction"),
            "loss_tier": (item.get("swing_strategy") or {}).get("loss_tier"),
            "lot_size": item.get("lot_size"),
            "shares_per_lot": item.get("shares_per_lot"),
        }
        for item in stocks
    ] + [
        {
            "code": item["code"],
            "name": item.get("name", ""),
            "asset_type": "option",
            "position_direction": item.get("position_direction"),
        }
        for item in options
    ]

    payload: dict[str, Any] = {
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
        "market": "HK",
        "stocks": stocks,
        "options": options,
        "portfolio_risk": build_portfolio_risk_overlay(stocks),
        "required_positions": required_positions,
        "summary": {
            "stock_count": len(stocks),
            "option_count": len(options),
            "total_position_count": len(required_positions),
            "total_stock_market_val": sum(s.get("market_val") or 0 for s in stocks),
            "total_option_market_val": sum(o.get("market_val") or 0 for o in options),
        },
    }
    if dynamic_risk is not None:
        payload["portfolio_risk"]["dynamic_risk"] = dynamic_risk
    if macro_risk is not None:
        payload["macro_risk"] = macro_risk
    if analyst_summary is not None:
        payload["virtual_analysts"] = analyst_summary
    return payload


def collect_required_codes(portfolio_payload: dict[str, Any]) -> list[str]:
    return [item["code"] for item in portfolio_payload.get("required_positions", [])]
