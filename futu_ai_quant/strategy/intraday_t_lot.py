"""日内做 T 单次股数：按持仓比例折算整手。"""

from __future__ import annotations

from typing import Any

import pandas as pd
from futu import RET_OK, OpenQuoteContext, OpenSecTradeContext

from futu_ai_quant.brokers.futu.positions import get_position_list, trd_market_for_code
from futu_ai_quant.brokers.futu.quotes import fetch_snapshot_map
from futu_ai_quant.domain.positions import is_option_code
from futu_ai_quant.market.lot import calc_full_lot_trade_qty, resolve_lot_size_detail
from futu_ai_quant.market.session import market_of_code
from futu_ai_quant.strategy.intraday_t_settings import INTRADAY_T_LOT_SIZE
from futu_ai_quant.utils.numbers import safe_float


def find_stock_position(positions: pd.DataFrame, code: str) -> dict[str, float] | None:
    """从持仓表查找正股多头仓位。"""
    if positions is None or positions.empty:
        return None

    rows = positions[positions["code"].astype(str) == code]
    if rows.empty:
        return None

    row = rows.iloc[0]
    if is_option_code(code):
        return None

    qty = safe_float(row.get("qty")) or 0.0
    if qty <= 0:
        return None

    can_sell = safe_float(row.get("can_sell_qty"))
    return {
        "qty": qty,
        "can_sell_qty": can_sell if can_sell is not None else qty,
    }


def resolve_intraday_t_lot_size(
    quote_ctx: OpenQuoteContext,
    trade_ctx: OpenSecTradeContext,
    code: str,
    *,
    lot_pct: float,
    fallback_lot_size: int = INTRADAY_T_LOT_SIZE,
) -> tuple[int, str]:
    """
    按持仓比例计算单次做 T 整手股数。

    返回 (lot_size, 说明)。无持仓或不足一手时回退 ``fallback_lot_size``。
    """
    if lot_pct <= 0:
        return fallback_lot_size, f"未启用持仓比例，使用固定 {fallback_lot_size} 股"

    market = market_of_code(code)
    ret, positions = get_position_list(trade_ctx, market=market)
    if ret != RET_OK or not isinstance(positions, pd.DataFrame):
        return fallback_lot_size, f"持仓查询失败，回退固定 {fallback_lot_size} 股"

    holding = find_stock_position(positions, code)
    if holding is None:
        return (
            fallback_lot_size,
            f"未找到 {code} 正股持仓，回退固定 {fallback_lot_size} 股",
        )

    snapshot = fetch_snapshot_map(quote_ctx, [code]).get(code, {})
    lot_size, lot_confirmed = resolve_lot_size_detail(snapshot, None)
    if not lot_confirmed:
        return fallback_lot_size, f"{code} 每手股数未知，回退固定 {fallback_lot_size} 股"

    qty, lots, note = calc_full_lot_trade_qty(
        holding_qty=holding["qty"],
        tradable_qty=holding["can_sell_qty"],
        lot_size=lot_size,
        max_pct=lot_pct,
        for_sell=True,
    )
    if qty <= 0:
        detail = note or "折算不足一手"
        return fallback_lot_size, f"{detail}，回退固定 {fallback_lot_size} 股"

    holding_lots = int(holding["qty"]) // lot_size
    parts = [
        f"持仓 {int(holding['qty'])} 股（{holding_lots} 手）",
        f"可卖 {int(holding['can_sell_qty'])} 股",
        f"每手 {lot_size} 股",
        f"比例 {lot_pct:g}%",
        f"做T {lots} 手 = {qty} 股",
    ]
    if note:
        parts.append(note)
    return qty, " | ".join(parts)


def resolve_lot_sizes_for_codes(
    quote_ctx: OpenQuoteContext,
    trade_ctx: OpenSecTradeContext,
    codes: list[str],
    *,
    lot_pct: float,
    fallback_lot_size: int = INTRADAY_T_LOT_SIZE,
) -> dict[str, tuple[int, str]]:
    """批量解析多标的做 T 股数。"""
    result: dict[str, tuple[int, str]] = {}
    for code in codes:
        result[code] = resolve_intraday_t_lot_size(
            quote_ctx,
            trade_ctx,
            code,
            lot_pct=lot_pct,
            fallback_lot_size=fallback_lot_size,
        )
    return result
