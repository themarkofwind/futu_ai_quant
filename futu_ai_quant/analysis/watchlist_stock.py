"""自选股单票分析：支持按推送槽位启用盘中 K 线。"""

from __future__ import annotations

from typing import Any

from futu import OpenQuoteContext

from futu_ai_quant.analysis.data_quality import attach_data_quality
from futu_ai_quant.brokers.futu.quotes import enrich_stock_pnl
from futu_ai_quant.config.settings import KLINE_COUNT, WEEKLY_KLINE_COUNT
from futu_ai_quant.config.watchlist import (
    watchlist_intraday_bar,
    watchlist_intraday_count,
    watchlist_intraday_kltype,
    watchlist_slot_uses_intraday,
)
from futu_ai_quant.indicators.technical import compute_timeframe_indicators
from futu_ai_quant.market.lot import resolve_lot_size_detail
from futu_ai_quant.planning.option import empty_option_trade_plan
from futu_ai_quant.planning.stock import build_stock_trade_plan
from futu_ai_quant.strategy.profile import build_watchlist_strategy_profile
from futu_ai_quant.strategy.signals import resolve_watchlist_combined_signal


def analyze_watchlist_stock(
    quote_ctx: OpenQuoteContext,
    stock: dict[str, Any],
    snapshot: dict[str, Any] | None,
    *,
    slot_key: str | None = None,
) -> dict[str, Any]:
    """
    自选观察分析。

    - auction / manual：日K 主、周K 次
    - lunch / preclose：盘中K（默认 30m）主、日K 次，周K 强反向则降级
    """
    from futu import KLType

    pnl = enrich_stock_pnl(stock, snapshot)
    lot_size, lot_confirmed = resolve_lot_size_detail(snapshot, stock)
    stock = {
        **stock,
        "lot_size": lot_size,
        "shares_per_lot": lot_size,
        "lot_confirmed": lot_confirmed,
    }
    swing_strategy = build_watchlist_strategy_profile()
    use_intraday = watchlist_slot_uses_intraday(slot_key)

    daily = compute_timeframe_indicators(quote_ctx, stock["code"], KLType.K_DAY, KLINE_COUNT)
    weekly = compute_timeframe_indicators(
        quote_ctx, stock["code"], KLType.K_WEEK, WEEKLY_KLINE_COUNT
    )

    intraday: dict[str, Any] | None = None
    if use_intraday:
        ktype = watchlist_intraday_kltype()
        intraday = compute_timeframe_indicators(
            quote_ctx,
            stock["code"],
            ktype,
            watchlist_intraday_count(),
        )
        swing_strategy = {
            **swing_strategy,
            "primary_timeframe": "intraday",
            "secondary_timeframe": "daily",
            "guidance": "盘中观察：分钟K主导、日K确认，周K强反向则观望",
            "intraday_enabled": True,
            "intraday_bar": watchlist_intraday_bar(),
        }

    combined = resolve_watchlist_combined_signal(
        daily=daily,
        weekly=weekly,
        intraday=intraday,
        use_intraday=bool(use_intraday and intraday and not intraday.get("error")),
    )

    enriched: dict[str, Any] = {
        **stock,
        "pnl": pnl,
        "swing_strategy": swing_strategy,
        "daily": daily,
        "weekly": weekly,
        "combined_swing_signal": combined,
        "watchlist_slot": slot_key,
        "use_intraday": use_intraday,
    }
    if intraday is not None:
        enriched["intraday"] = intraday

    attach_data_quality(enriched, snapshot=snapshot, lot_confirmed=lot_confirmed)
    combined = enriched["combined_swing_signal"]

    # 盘中 ATR 优先用于价带，使 13:05/15:30 区间更贴近日间波动
    if use_intraday and intraday and not intraday.get("error") and intraday.get("atr") is not None:
        daily_for_plan = {**daily, "atr": intraday.get("atr"), "technical_close": intraday.get("technical_close")}
        enriched_for_plan = {**enriched, "daily": daily_for_plan}
    else:
        enriched_for_plan = enriched

    stock_trade_plan = build_stock_trade_plan(
        enriched_for_plan,
        swing_strategy,
        combined,
        snapshot,
        pnl,
    )
    if use_intraday and stock_trade_plan.get("trade_note"):
        note = str(stock_trade_plan["trade_note"])
        if "盘中" not in note:
            stock_trade_plan["trade_note"] = f"盘中周期｜{note}"

    return {
        **enriched,
        "combined_swing_signal": combined,
        "stock_trade_plan": stock_trade_plan,
        "option_trade_plan": empty_option_trade_plan(),
        "option_overlay": {"scan_note": "自选观察跳过卖权扫描"},
        "indicator_error": daily.get("error")
        or weekly.get("error")
        or (intraday or {}).get("error"),
    }
