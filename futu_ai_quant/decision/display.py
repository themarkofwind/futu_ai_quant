"""决策展示：中文名称、技术指标概要、终端可读摘要。"""

from __future__ import annotations

from typing import Any

from futu_ai_quant.market.symbol_names import display_name


def _fmt_num(value: Any, digits: int = 2) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def build_technical_summary(stock: dict[str, Any]) -> str:
    """从正股指标字段生成简短技术面说明。"""
    parts: list[str] = []

    swing = stock.get("swing_strategy") or {}
    tier = swing.get("loss_tier")
    if tier:
        parts.append(f"分层={tier}")

    combined = stock.get("combined_swing_signal") or {}
    if combined.get("effective_signal"):
        parts.append(f"有效信号={combined['effective_signal']}")
    if combined.get("signal_note"):
        parts.append(str(combined["signal_note"]))

    pnl = stock.get("pnl") or {}
    if pnl.get("pl_ratio") is not None:
        parts.append(f"盈亏={_fmt_num(pnl['pl_ratio'])}%")
    if pnl.get("market_price") is not None:
        parts.append(f"现价={_fmt_num(pnl['market_price'])}")

    for key, label in (("daily", "日K"), ("weekly", "周K")):
        frame = stock.get(key) or {}
        if frame.get("error"):
            parts.append(f"{label}=数据缺失")
            continue
        signal = frame.get("swing_signal", "WAIT")
        chunk = [f"{label} {signal}"]
        if frame.get("rsi") is not None:
            chunk.append(f"RSI={_fmt_num(frame['rsi'])}")
        if frame.get("macd_bias"):
            chunk.append(f"MACD={frame['macd_bias']}")
        if frame.get("boll_position"):
            chunk.append(f"布林={frame['boll_position']}")
        if frame.get("volume_ratio") is not None:
            chunk.append(f"量比={_fmt_num(frame['volume_ratio'])}")
        if frame.get("volume_confirmed") is True:
            chunk.append("量能确认")
        parts.append(" ".join(chunk))

    trade_plan = stock.get("stock_trade_plan") or {}
    if trade_plan.get("direction") not in (None, "none"):
        parts.append(
            f"预计算波段={trade_plan['direction']} "
            f"{trade_plan.get('suggested_lots', 0)}手"
        )

    opt_plan = stock.get("option_trade_plan") or {}
    if opt_plan.get("action") not in (None, "none"):
        parts.append(f"期权建议={opt_plan.get('label') or opt_plan.get('action')}")

    hist = stock.get("trade_history") or {}
    if hist.get("swing_hint"):
        parts.append(str(hist["swing_hint"]))

    return "；".join(parts)


def build_option_technical_summary(option: dict[str, Any]) -> str:
    parts: list[str] = []
    if option.get("position_direction"):
        parts.append(str(option["position_direction"]))
    for key, label in (
        ("last_price", "现价"),
        ("implied_volatility", "IV"),
        ("delta", "Delta"),
        ("theta", "Theta"),
        ("strike_price", "行权价"),
    ):
        if option.get(key) is not None:
            parts.append(f"{label}={_fmt_num(option[key], 4)}")
    if option.get("expire_time"):
        parts.append(f"到期={option['expire_time']}")
    return "；".join(parts) if parts else "期权持仓"


def enrich_decision_for_display(
    decision: dict[str, Any],
    *,
    stocks_by_code: dict[str, dict[str, Any]],
    options_by_code: dict[str, dict[str, Any]],
    symbol_names: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """为每条建议补充展示名与技术面概要，并覆盖 ``name`` 为优先中文名。"""
    for rec in decision.get("recommendations", []):
        if not isinstance(rec, dict):
            continue
        code = str(rec.get("code") or "")
        entry = symbol_names.get(code, {})
        rec["name_zh"] = str(entry.get("name_zh") or "")
        rec["name_en"] = str(entry.get("name_en") or "")
        rec["display_name"] = display_name(entry, code=code)
        rec["name"] = rec["display_name"]

        stock = stocks_by_code.get(code)
        if stock:
            rec["technical_summary"] = build_technical_summary(stock)
        elif code in options_by_code:
            rec["technical_summary"] = build_option_technical_summary(options_by_code[code])
        else:
            rec["technical_summary"] = rec.get("technical_summary") or ""
    return decision


def format_decision_summary(decision: dict[str, Any]) -> str:
    """生成终端可读的操作建议摘要（含技术面概要）。"""
    lines: list[str] = []
    summary = str(decision.get("portfolio_risk_summary") or "").strip()
    if summary:
        lines.append("【组合风险】")
        lines.append(summary)
        lines.append("")

    lines.append("【操作建议】")
    for rec in decision.get("recommendations", []):
        if not isinstance(rec, dict):
            continue
        code = rec.get("code", "")
        name = rec.get("display_name") or rec.get("name") or code
        action = rec.get("action", "HOLD")
        confidence = rec.get("confidence")
        conf_text = f" 置信度={confidence:.0%}" if isinstance(confidence, (int, float)) else ""

        lines.append(f"- {code} {name} → {action}{conf_text}")

        tech = str(rec.get("technical_summary") or "").strip()
        if tech:
            lines.append(f"  技术面：{tech}")

        reasoning = str(rec.get("reasoning") or "").strip()
        if reasoning:
            lines.append(f"  研判：{reasoning}")

        stock_plan = rec.get("stock_trade_plan") or {}
        if stock_plan.get("direction") not in (None, "none"):
            lines.append(
                f"  正股：{stock_plan['direction']} "
                f"{stock_plan.get('suggested_lots', 0)}手 "
                f"({stock_plan.get('suggested_qty', 0)}股) "
                f"触发 {rec.get('suggested_trigger', '-')}"
            )

        opt_plan = rec.get("option_trade_plan") or {}
        if opt_plan.get("action") not in (None, "none"):
            lines.append(
                f"  期权：{opt_plan.get('action')} "
                f"{opt_plan.get('contract_code', '')} "
                f"×{opt_plan.get('contracts', 0)}"
            )
        lines.append("")

    return "\n".join(lines).rstrip()
