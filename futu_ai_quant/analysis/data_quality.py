"""正股数据质量评估与波段门控。"""

from __future__ import annotations

from typing import Any


def collect_data_quality_issues(
    *,
    daily: dict[str, Any],
    weekly: dict[str, Any],
    snapshot: dict[str, Any] | None,
    lot_confirmed: bool,
) -> list[str]:
    issues: list[str] = []
    if daily.get("error"):
        issues.append(f"日K: {daily['error']}")
    if weekly.get("error"):
        issues.append(f"周K: {weekly['error']}")
    if snapshot is None:
        issues.append("行情快照缺失")
    if not lot_confirmed:
        issues.append("每手股数未从行情确认，禁止生成交易数量")
    return issues


def apply_data_quality_to_combined_signal(stock: dict[str, Any], summary: str) -> None:
    combined = dict(stock.get("combined_swing_signal") or {})
    effective = combined.get("effective_signal", "HOLD")
    if effective in ("BUY_SWING", "SELL_SWING"):
        combined["data_quality_gated_from"] = effective
        combined["effective_signal"] = "WAIT"
    prior = combined.get("signal_note")
    combined["signal_note"] = f"{prior}；{summary}" if prior else summary
    stock["combined_swing_signal"] = combined


def attach_data_quality(
    stock: dict[str, Any],
    *,
    snapshot: dict[str, Any] | None,
    lot_confirmed: bool,
) -> None:
    """根据指标/快照/lot 写入 ``data_quality``，必要时降级有效信号。"""
    daily = stock.get("daily") or {}
    weekly = stock.get("weekly") or {}

    issues = collect_data_quality_issues(
        daily=daily,
        weekly=weekly,
        snapshot=snapshot,
        lot_confirmed=lot_confirmed,
    )
    if not issues:
        stock["data_quality"] = {"status": "ok", "issues": []}
        return

    summary = "数据质量不足：" + "；".join(issues)
    stock["data_quality"] = {
        "status": "degraded",
        "issues": issues,
        "summary": summary,
    }
    apply_data_quality_to_combined_signal(stock, summary)


def trade_plan_blocked_by_data_quality(stock: dict[str, Any]) -> bool:
    return (stock.get("data_quality") or {}).get("status") == "degraded"


def apply_data_quality_to_trade_plan(plan: dict[str, Any], stock: dict[str, Any]) -> None:
    if not trade_plan_blocked_by_data_quality(stock):
        return
    summary = (stock.get("data_quality") or {}).get("summary", "数据质量不足")
    plan.update(
        {
            "direction": "none",
            "suggested_qty": 0,
            "suggested_lots": 0,
            "pct_of_holding": 0.0,
            "trigger_price_low": None,
            "trigger_price_high": None,
            "watch_triggers": [],
            "trade_note": summary,
        }
    )
