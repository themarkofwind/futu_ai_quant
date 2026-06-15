"""
单只正股的完整分析流水线。

``analyze_stock_position`` / ``compute_stock_indicators`` 串联：
盈亏 → 分层策略 → 日K/周K 指标 → 卖权扫描 → 综合信号 → 正股/期权交易计划。
"""

from __future__ import annotations

from typing import Any

from futu import KLType, OpenQuoteContext

from futu_ai_quant.brokers.futu.options import scan_sell_option_candidates
from futu_ai_quant.brokers.futu.quotes import enrich_stock_pnl
from futu_ai_quant.config.settings import KLINE_COUNT, WEEKLY_KLINE_COUNT
from futu_ai_quant.indicators.technical import compute_timeframe_indicators
from futu_ai_quant.market.lot import resolve_lot_size
from futu_ai_quant.planning.option import build_option_trade_plan_for_stock
from futu_ai_quant.planning.stock import build_stock_trade_plan
from futu_ai_quant.strategy.profile import build_swing_strategy_profile
from futu_ai_quant.strategy.signals import resolve_effective_swing_signal


def analyze_stock_position(
    quote_ctx: OpenQuoteContext,
    stock: dict[str, Any],
    snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    pnl = enrich_stock_pnl(stock, snapshot)
    lot_size = resolve_lot_size(snapshot, stock)
    stock = {**stock, "lot_size": lot_size, "shares_per_lot": lot_size}
    swing_strategy = build_swing_strategy_profile(pnl.get("pl_ratio"))

    daily = compute_timeframe_indicators(quote_ctx, stock["code"], KLType.K_DAY, KLINE_COUNT)
    weekly = compute_timeframe_indicators(
        quote_ctx, stock["code"], KLType.K_WEEK, WEEKLY_KLINE_COUNT
    )

    primary = swing_strategy["primary_timeframe"]
    primary_signal = weekly["swing_signal"] if primary == "weekly" else daily["swing_signal"]
    secondary_signal = daily["swing_signal"] if primary == "weekly" else weekly["swing_signal"]

    option_overlay = scan_sell_option_candidates(
        quote_ctx,
        {**stock, "pnl": pnl, "daily": daily},
        swing_strategy,
    )
    combined_swing_signal = resolve_effective_swing_signal(
        primary_signal,
        secondary_signal,
        swing_strategy,
        primary_timeframe=primary,
    )
    stock_trade_plan = build_stock_trade_plan(
        {**stock, "daily": daily, "weekly": weekly},
        swing_strategy,
        combined_swing_signal,
        snapshot,
        pnl,
    )
    option_trade_plan = build_option_trade_plan_for_stock(
        stock,
        option_overlay,
        swing_strategy,
        combined_swing_signal,
    )

    return {
        **stock,
        "pnl": pnl,
        "swing_strategy": swing_strategy,
        "daily": daily,
        "weekly": weekly,
        "combined_swing_signal": combined_swing_signal,
        "stock_trade_plan": stock_trade_plan,
        "option_trade_plan": option_trade_plan,
        "option_overlay": option_overlay,
        "indicator_error": daily.get("error") or weekly.get("error"),
    }


def compute_stock_indicators(
    quote_ctx: OpenQuoteContext,
    stock: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return analyze_stock_position(quote_ctx, stock, snapshot)
