"""
规则化虚拟分析师层：在 LLM 之前产出结构化信号摘要。

借鉴 ai-hedge-fund 多 Agent 信号聚合思路，但全部为确定性规则，无额外 API 成本。
"""

from __future__ import annotations

from typing import Any

_SIGNAL_TO_SCORE = {"bullish": 1, "neutral": 0, "bearish": -1, "caution": -0.5}
_SWING_TO_SCORE = {
    "BUY_SWING": 1,
    "SELL_SWING": -1,
    "HOLD": 0,
    "WAIT": 0,
}


def _confidence_label(score: float) -> str:
    strength = abs(score)
    if strength >= 0.6:
        return "high"
    if strength >= 0.3:
        return "medium"
    return "low"


def _build_technical_analyst(stock: dict[str, Any]) -> dict[str, Any]:
    ensemble = (stock.get("daily") or {}).get("technical_ensemble") or {}
    signal = ensemble.get("signal", "neutral")
    confidence = float(ensemble.get("confidence") or 50) / 100.0
    strategies = ensemble.get("strategies") or {}
    bullish = sum(1 for s in strategies.values() if s.get("signal") == "bullish")
    bearish = sum(1 for s in strategies.values() if s.get("signal") == "bearish")
    reasoning = f"五策略集成 {signal}（看多{bullish}/看空{bearish}）"
    return {
        "analyst": "technical",
        "signal": signal,
        "confidence": round(confidence * 100, 1),
        "reasoning": reasoning,
    }


def _build_momentum_analyst(stock: dict[str, Any]) -> dict[str, Any]:
    ensemble = (stock.get("daily") or {}).get("technical_ensemble") or {}
    mom = (ensemble.get("strategies") or {}).get("momentum") or {}
    signal = mom.get("signal", "neutral")
    confidence = float(mom.get("confidence") or 50)
    daily = stock.get("daily") or {}
    macd = daily.get("macd_bias", "unknown")
    reasoning = f"动量策略 {signal}，日K MACD={macd}"
    return {
        "analyst": "momentum",
        "signal": signal,
        "confidence": confidence,
        "reasoning": reasoning,
    }


def _build_swing_analyst(stock: dict[str, Any]) -> dict[str, Any]:
    combined = stock.get("combined_swing_signal") or {}
    effective = combined.get("effective_signal", "HOLD")
    score = _SWING_TO_SCORE.get(effective, 0)
    note = combined.get("signal_note") or ""
    primary = combined.get("primary_signal")
    secondary = combined.get("secondary_signal")
    reasoning = f"主/次周期 {primary}/{secondary} → {effective}"
    if note:
        reasoning += f"；{note}"
    return {
        "analyst": "swing",
        "signal": effective,
        "confidence": round(abs(score) * 80 + 20, 1) if effective in ("BUY_SWING", "SELL_SWING") else 40.0,
        "reasoning": reasoning,
    }


def _build_risk_analyst(stock: dict[str, Any]) -> dict[str, Any]:
    limits = stock.get("risk_limits") or {}
    tier = limits.get("tier_max_swing_pct")
    adjusted = limits.get("adjusted_max_swing_pct")
    vol = (limits.get("volatility_metrics") or {}).get("annualized_volatility")
    corr = limits.get("avg_correlation_with_peers")

    if tier is not None and adjusted is not None and adjusted < tier * 0.85:
        signal = "caution"
        confidence = 75.0
        reasoning = f"动态限仓 {adjusted}%（分层{tier}%）"
        if vol is not None:
            reasoning += f"，年化波动={vol:.1%}"
        if corr is not None:
            reasoning += f"，组合相关性={corr:.2f}"
    else:
        signal = "neutral"
        confidence = 50.0
        reasoning = "波动率与相关性未触发额外限仓"

    return {
        "analyst": "risk",
        "signal": signal,
        "confidence": confidence,
        "reasoning": reasoning,
    }


def build_stock_analyst_signals(stock: dict[str, Any]) -> dict[str, Any]:
    """单只正股的虚拟分析师信号。"""
    analysts = [
        _build_technical_analyst(stock),
        _build_momentum_analyst(stock),
        _build_swing_analyst(stock),
        _build_risk_analyst(stock),
    ]

    scores: list[float] = []
    for item in analysts:
        sig = item["signal"]
        conf = float(item["confidence"]) / 100.0
        if sig in _SIGNAL_TO_SCORE:
            scores.append(_SIGNAL_TO_SCORE[sig] * conf)
        elif sig in _SWING_TO_SCORE:
            scores.append(_SWING_TO_SCORE[sig] * conf)

    avg_score = sum(scores) / len(scores) if scores else 0.0
    if avg_score > 0.25:
        consensus = "bullish"
    elif avg_score < -0.25:
        consensus = "bearish"
    else:
        consensus = "neutral"

    return {
        "code": stock.get("code"),
        "consensus": consensus,
        "consensus_score": round(avg_score, 4),
        "consensus_strength": _confidence_label(avg_score),
        "analysts": analysts,
    }


def attach_analyst_signals(stocks: list[dict[str, Any]]) -> dict[str, Any]:
    """为每只正股附加 ``analyst_signals``，并返回组合级摘要。"""
    summaries: list[dict[str, Any]] = []
    for stock in stocks:
        signals = build_stock_analyst_signals(stock)
        stock["analyst_signals"] = signals
        summaries.append(
            {
                "code": signals["code"],
                "consensus": signals["consensus"],
                "consensus_score": signals["consensus_score"],
            }
        )

    bullish = sum(1 for s in summaries if s["consensus"] == "bullish")
    bearish = sum(1 for s in summaries if s["consensus"] == "bearish")
    return {
        "stock_count": len(summaries),
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": len(summaries) - bullish - bearish,
        "per_stock": summaries,
    }
