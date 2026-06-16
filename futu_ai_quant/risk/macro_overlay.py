"""
宏观风险 overlay：恒指/黄金走势与 FOMC 等事件，组合级收紧波段仓位上限。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from futu import KLType, RET_OK, OpenQuoteContext

from futu_ai_quant.config.settings import (
    MACRO_FED_BLACKOUT_DAYS,
    MACRO_GOLD_5D_RISE_PCT,
    MACRO_GOLD_CODE,
    MACRO_HSI_5D_DROP_PCT,
    MACRO_HSI_CODE,
    MACRO_HSI_TODAY_DROP_PCT,
    MACRO_RISK_ENABLED,
    MACRO_SWING_MULTIPLIER_ELEVATED,
    MACRO_SWING_MULTIPLIER_HIGH,
)
from futu_ai_quant.indicators.kline_cache import fetch_history_kline_cached
from futu_ai_quant.risk.macro_calendar import load_macro_calendar
from futu_ai_quant.utils.numbers import safe_float


def _return_pct(closes: list[float], days: int) -> float | None:
    if len(closes) <= days:
        return None
    start = closes[-(days + 1)]
    end = closes[-1]
    if start in (None, 0):
        return None
    return round((end - start) / start * 100, 2)


def _drawdown_from_high(closes: list[float], lookback: int = 20) -> float | None:
    if len(closes) < 2:
        return None
    window = closes[-lookback:] if len(closes) >= lookback else closes
    peak = max(window)
    if peak in (None, 0):
        return None
    return round((window[-1] - peak) / peak * 100, 2)


def fetch_index_metrics(
    quote_ctx: OpenQuoteContext | None,
    code: str,
    *,
    kline_count: int = 30,
) -> dict[str, Any]:
    """拉取指数/ETF 日 K，计算宏观参考指标。"""
    result: dict[str, Any] = {
        "code": code,
        "available": False,
        "close_history": [],
        "today_change_pct": None,
        "return_5d_pct": None,
        "drawdown_20d_pct": None,
        "error": None,
    }
    if not code or quote_ctx is None:
        result["error"] = "quote_ctx 或代码为空"
        return result

    try:
        ret, kline, _ = fetch_history_kline_cached(quote_ctx, code, KLType.K_DAY, kline_count)
        if ret != RET_OK or kline is None or kline.empty:
            result["error"] = f"K线拉取失败: {kline}"
            return result

        closes = [safe_float(v) for v in kline["close"].tolist() if safe_float(v) is not None]
        if len(closes) < 2:
            result["error"] = "K线数据不足"
            return result

        result["available"] = True
        result["close_history"] = closes[-10:]
        result["return_5d_pct"] = _return_pct(closes, 5)
        result["drawdown_20d_pct"] = _drawdown_from_high(closes, 20)

        if "open" in kline.columns and len(kline) >= 1:
            last_open = safe_float(kline.iloc[-1].get("open"))
            last_close = safe_float(kline.iloc[-1].get("close"))
            if last_open not in (None, 0) and last_close is not None:
                result["today_change_pct"] = round((last_close - last_open) / last_open * 100, 2)
    except Exception as exc:
        result["error"] = str(exc)

    return result


def _fed_meeting_active(macro_events: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
    for event in macro_events:
        if event.get("event_type") != "fed_meeting":
            continue
        event_date = date.fromisoformat(event["date"])
        start = event_date - timedelta(days=MACRO_FED_BLACKOUT_DAYS)
        end = event_date + timedelta(days=MACRO_FED_BLACKOUT_DAYS)
        if start <= today <= end:
            return {
                "label": event.get("label", "FOMC议息"),
                "event_date": event["date"],
                "window_start": start.isoformat(),
                "window_end": end.isoformat(),
            }
    return None


def evaluate_macro_risk(
    *,
    hsi: dict[str, Any] | None,
    gold: dict[str, Any] | None,
    macro_events: list[dict[str, Any]],
    as_of: date | None = None,
) -> dict[str, Any]:
    """评估宏观风险等级与波段乘数。"""
    today = as_of or date.today()
    triggers: list[str] = []
    metrics: dict[str, Any] = {}

    if hsi and hsi.get("available"):
        metrics["hsi"] = {
            "code": hsi.get("code"),
            "return_5d_pct": hsi.get("return_5d_pct"),
            "today_change_pct": hsi.get("today_change_pct"),
            "drawdown_20d_pct": hsi.get("drawdown_20d_pct"),
        }
        ret5 = hsi.get("return_5d_pct")
        if ret5 is not None and ret5 <= MACRO_HSI_5D_DROP_PCT:
            triggers.append(f"恒指5日跌{ret5}%（阈值{MACRO_HSI_5D_DROP_PCT:g}%）")
        today_chg = hsi.get("today_change_pct")
        if today_chg is not None and today_chg <= MACRO_HSI_TODAY_DROP_PCT:
            triggers.append(f"恒指日内跌{today_chg}%（阈值{MACRO_HSI_TODAY_DROP_PCT:g}%）")

    if gold and gold.get("available"):
        metrics["gold"] = {
            "code": gold.get("code"),
            "return_5d_pct": gold.get("return_5d_pct"),
        }
        gold_ret5 = gold.get("return_5d_pct")
        if gold_ret5 is not None and gold_ret5 >= MACRO_GOLD_5D_RISE_PCT:
            triggers.append(f"黄金5日涨{gold_ret5}%（避险升温，阈值{MACRO_GOLD_5D_RISE_PCT:g}%）")

    fed = _fed_meeting_active(macro_events, today)
    if fed:
        metrics["fed_meeting"] = fed
        triggers.append(f"FOMC窗口：{fed['label']}（{fed['window_start']}~{fed['window_end']}）")

    trigger_count = len(triggers)
    if trigger_count >= 2:
        level = "high"
        multiplier = MACRO_SWING_MULTIPLIER_HIGH
    elif trigger_count == 1:
        level = "elevated"
        multiplier = MACRO_SWING_MULTIPLIER_ELEVATED
    else:
        level = "normal"
        multiplier = 1.0

    summary = "宏观环境正常"
    if triggers:
        summary = f"宏观风险{level}：" + "；".join(triggers)

    return {
        "enabled": True,
        "risk_level": level,
        "swing_pct_multiplier": multiplier,
        "trigger_count": trigger_count,
        "triggers": triggers,
        "summary": summary,
        "metrics": metrics,
    }


def apply_macro_risk_to_stocks(stocks: list[dict[str, Any]], macro_risk: dict[str, Any]) -> None:
    """将宏观乘数写入 risk_limits 并收紧 adjusted_max_swing_pct。"""
    multiplier = float(macro_risk.get("swing_pct_multiplier") or 1.0)
    if multiplier >= 1.0:
        for stock in stocks:
            limits = stock.setdefault("risk_limits", {})
            limits["macro_swing_multiplier"] = 1.0
        return

    for stock in stocks:
        limits = stock.setdefault("risk_limits", {})
        tier_max = float(limits.get("tier_max_swing_pct") or 10)
        current = float(limits.get("adjusted_max_swing_pct") or tier_max)
        adjusted = max(5.0, min(tier_max, round(current * multiplier, 2)))
        limits["macro_swing_multiplier"] = multiplier
        limits["adjusted_max_swing_pct"] = adjusted
        reasoning = limits.setdefault("reasoning", {})
        if isinstance(reasoning, dict):
            reasoning["macro_swing_multiplier"] = multiplier
            reasoning["macro_adjusted_max_swing_pct"] = adjusted


def attach_macro_risk_overlay(
    quote_ctx: OpenQuoteContext | None,
    stocks: list[dict[str, Any]],
    *,
    as_of: date | None = None,
) -> dict[str, Any]:
    """拉取宏观指标并收紧组合波段上限。"""
    if not MACRO_RISK_ENABLED:
        return {"enabled": False, "risk_level": "normal", "swing_pct_multiplier": 1.0}

    calendar = load_macro_calendar()
    hsi = fetch_index_metrics(quote_ctx, MACRO_HSI_CODE)
    gold = (
        fetch_index_metrics(quote_ctx, MACRO_GOLD_CODE)
        if MACRO_GOLD_CODE
        else {"available": False, "code": None}
    )

    macro_risk = evaluate_macro_risk(
        hsi=hsi,
        gold=gold if MACRO_GOLD_CODE else None,
        macro_events=calendar,
        as_of=as_of,
    )
    apply_macro_risk_to_stocks(stocks, macro_risk)
    return macro_risk
