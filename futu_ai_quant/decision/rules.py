from __future__ import annotations

from typing import Any

from futu_ai_quant.analysis.portfolio import build_portfolio_risk_overlay
from futu_ai_quant.config.settings import TRADE_RECENT_STOCK_COUNT
from futu_ai_quant.planning.option import empty_option_trade_plan
from futu_ai_quant.planning.stock import empty_stock_trade_plan, format_watch_triggers
from futu_ai_quant.utils.numbers import safe_float


def infer_stock_action(stock: dict[str, Any]) -> str:
    direction = str((stock.get("stock_trade_plan") or {}).get("direction", "none")).lower()
    if direction == "buy":
        return "BUY"
    if direction == "sell":
        return "SELL"
    return "HOLD"


def infer_option_action(option: dict[str, Any]) -> str:
    plan = option.get("option_trade_plan") or {}
    action = str(plan.get("action", "none")).lower()
    if action == "roll":
        return "ROLL"
    return "HOLD"


def build_rules_reasoning(stock: dict[str, Any]) -> str:
    combined = stock.get("combined_swing_signal") or {}
    swing = stock.get("swing_strategy") or {}
    daily = stock.get("daily") or {}
    weekly = stock.get("weekly") or {}
    parts = [
        f"{swing.get('loss_tier', '?')} 仓位，{swing.get('primary_timeframe', '?')}K主导",
        f"有效信号={combined.get('effective_signal')}",
        f"日K={daily.get('swing_signal')} 周K={weekly.get('swing_signal')}",
    ]
    if combined.get("signal_note"):
        parts.append(str(combined["signal_note"]))
    existing = stock.get("existing_option_positions") or []
    if existing:
        held = "；".join(
            f"{item.get('code')}({item.get('position_direction')})"
            for item in existing
        )
        parts.append(f"已有期权持仓: {held}")
    suggested = stock.get("option_trade_plan") or {}
    if suggested.get("action") not in (None, "none"):
        parts.append(f"建议新开期权: {suggested.get('label')}")
    elif suggested.get("skip_reason"):
        parts.append(str(suggested["skip_reason"]))
    trade_hist = stock.get("trade_history") or {}
    if trade_hist.get("swing_hint"):
        parts.append(str(trade_hist["swing_hint"]))
    elif (trade_hist.get("recent_swing_window") or {}).get("stock_trade_count", 0):
        limit = trade_hist.get("recent_stock_trade_limit", TRADE_RECENT_STOCK_COUNT)
        parts.append(
            f"最近{limit}笔内正股成交"
            f"{trade_hist['recent_swing_window']['stock_trade_count']}笔"
        )
    return "；".join(parts)


def build_rules_portfolio_summary(stocks: list[dict[str, Any]], options: list[dict[str, Any]]) -> str:
    tiers: dict[str, int] = {}
    for stock in stocks:
        tier = (stock.get("swing_strategy") or {}).get("loss_tier", "unknown")
        tiers[tier] = tiers.get(tier, 0) + 1
    tier_text = "，".join(f"{k}={v}" for k, v in sorted(tiers.items()))
    risk = build_portfolio_risk_overlay(stocks)
    alert_text = ""
    if risk.get("concentration_alerts"):
        alert_text = "；集中度预警: " + "；".join(risk["concentration_alerts"])
    return (
        f"规则引擎决策：正股 {len(stocks)} 只（{tier_text}），"
        f"期权持仓 {len(options)} 个；建议以 combined_swing_signal.effective_signal 与预计算 trade_plan 为准。"
        f"{alert_text}"
    )


def serialize_trade_plan_for_decision(stock: dict[str, Any]) -> dict[str, Any]:
    plan = stock.get("stock_trade_plan") or empty_stock_trade_plan()
    return {
        "direction": plan.get("direction", "none"),
        "suggested_qty": int(plan.get("suggested_qty") or 0),
        "suggested_lots": int(plan.get("suggested_lots") or 0),
        "lot_size": plan.get("lot_size"),
        "pct_of_holding": plan.get("pct_of_holding", 0.0),
        "trigger_price_low": plan.get("trigger_price_low"),
        "trigger_price_high": plan.get("trigger_price_high"),
        "watch_triggers": plan.get("watch_triggers") or [],
    }


def serialize_option_plan_for_decision(stock: dict[str, Any]) -> dict[str, Any]:
    plan = stock.get("option_trade_plan") or empty_option_trade_plan()
    if plan.get("action") in (None, "none"):
        return empty_option_trade_plan()
    return {
        "action": plan.get("action"),
        "contract_code": plan.get("contract_code", ""),
        "expire_date": plan.get("expire_date") or plan.get("expire_time", ""),
        "strike_price": plan.get("strike_price", 0),
        "contracts": int(plan.get("contracts") or 0),
        "premium_per_share": plan.get("premium_per_share", 0),
        "estimated_total_premium": plan.get("estimated_total_premium", 0),
        "plan_source": plan.get("plan_source", "suggested"),
    }


def build_rules_decision(
    stocks: list[dict[str, Any]],
    options: list[dict[str, Any]],
) -> dict[str, Any]:
    recommendations: list[dict[str, Any]] = []

    for stock in stocks:
        stock_plan = serialize_trade_plan_for_decision(stock)
        option_plan = serialize_option_plan_for_decision(stock)
        action = infer_stock_action(stock)
        trigger_low = stock_plan.get("trigger_price_low")
        trigger_high = stock_plan.get("trigger_price_high")
        watch_text = format_watch_triggers(stock_plan)
        if trigger_low is not None and trigger_high is not None:
            suggested_trigger = f"{trigger_low}-{trigger_high}"
        elif watch_text:
            suggested_trigger = watch_text
        else:
            suggested_trigger = "无"
        recommendations.append(
            {
                "code": stock["code"],
                "name": stock.get("name", ""),
                "action": action,
                "confidence": 0.8 if action != "HOLD" else 0.65,
                "reasoning": build_rules_reasoning(stock),
                "suggested_trigger": suggested_trigger,
                "stock_trade_plan": stock_plan,
                "option_trade_plan": option_plan,
                "decision_source": "rules",
            }
        )

    for option in options:
        option_plan = option.get("option_trade_plan") or empty_option_trade_plan()
        recommendations.append(
            {
                "code": option["code"],
                "name": option.get("name", ""),
                "action": infer_option_action(option),
                "confidence": 0.7,
                "reasoning": (
                    f"已有期权持仓 {option.get('position_direction')}；"
                    f"{option_plan.get('label', '持有观望')}"
                ),
                "suggested_trigger": "无",
                "stock_trade_plan": empty_stock_trade_plan(),
                "option_trade_plan": {
                    "action": option_plan.get("action", "none"),
                    "contract_code": option.get("code", ""),
                    "expire_date": option.get("expire_time") or option.get("expire_date", ""),
                    "strike_price": option.get("strike_price", 0),
                    "contracts": int(abs(safe_float(option.get("qty")) or 0)),
                    "premium_per_share": option.get("last_price", 0),
                    "estimated_total_premium": None,
                    "plan_source": "existing",
                },
                "decision_source": "rules",
            }
        )

    return {
        "portfolio_risk_summary": build_rules_portfolio_summary(stocks, options),
        "recommendations": recommendations,
        "decision_source": "rules",
    }
