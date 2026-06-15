from __future__ import annotations

from typing import Any

from futu_ai_quant.utils.numbers import safe_float


def resolve_lot_size(snapshot: dict[str, Any] | None, stock: dict[str, Any] | None = None) -> int:
    """从行情快照读取每手股数，港股交易须按整手下单。"""
    for source in (snapshot, stock):
        if not source:
            continue
        lot_size = safe_float(source.get("lot_size"))
        if lot_size is not None and int(lot_size) > 0:
            return int(lot_size)
    return 100


def calc_full_lot_trade_qty(
    holding_qty: float,
    tradable_qty: float,
    lot_size: int,
    max_pct: float,
    for_sell: bool,
) -> tuple[int, int, str | None]:
    """
    计算整手交易数量。
    返回 (suggested_qty, suggested_lots, note)；不足一手时 suggested_qty=0。
    """
    if lot_size <= 0:
        return 0, 0, "每手股数未知，无法计算整手仓位"

    holding = int(abs(holding_qty))
    tradable = int(abs(tradable_qty))
    max_by_pct = round_down_to_lot(holding * max_pct / 100.0, lot_size)

    if for_sell:
        capacity = min(max_by_pct, round_down_to_lot(tradable, lot_size))
    else:
        capacity = max_by_pct

    if capacity <= 0:
        note = (
            f"按 {max_pct:g}% 波段比例折算不足一手（每手 {lot_size} 股），"
            "为避免碎股暂不自动建议交易"
        )
        return 0, 0, note

    lots = capacity // lot_size
    return capacity, lots, None


def round_down_to_lot(shares: float, lot_size: int) -> int:
    share_count = int(abs(shares))
    if lot_size <= 0:
        return share_count
    return (share_count // lot_size) * lot_size
