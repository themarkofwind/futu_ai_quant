"""
Futu OpenD 正股行情接口封装。

API 映射
--------
- ``fetch_snapshot_map`` → ``get_market_snapshot``（每批最多 200 代码）
- ``enrich_stock_pnl``：合并持仓成本与快照现价（纯计算，无 API）
"""

from __future__ import annotations

from typing import Any

from futu import RET_OK, OpenQuoteContext

from futu_ai_quant.utils.logging import log
from futu_ai_quant.utils.numbers import safe_float
from futu_ai_quant.utils.retry import retry_call


def fetch_snapshot_map(
    quote_ctx: OpenQuoteContext,
    codes: list[str],
) -> dict[str, dict[str, Any]]:
    """批量 get_market_snapshot，每批最多 200 代码。"""
    snapshot_map: dict[str, dict[str, Any]] = {}
    if not codes:
        return snapshot_map

    batch_size = 200
    for idx in range(0, len(codes), batch_size):
        batch = codes[idx : idx + batch_size]
        try:
            ret, snapshot = retry_call(
                lambda: quote_ctx.get_market_snapshot(batch),
                label=f"快照 batch@{idx}",
                expect_ret_ok=True,
            )
            if ret != RET_OK or snapshot is None or snapshot.empty:
                log("快照", f"批量快照失败: {snapshot}")
                continue
            for _, row in snapshot.iterrows():
                snapshot_map[str(row["code"])] = row.to_dict()
        except Exception as exc:
            log("快照", f"快照拉取异常: {exc}")
    return snapshot_map


def enrich_stock_pnl(stock: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """合并持仓与快照，生成 pnl 子结构。"""
    nominal = safe_float(stock.get("nominal_price"))
    cost = safe_float(stock.get("cost_price"))
    pl_ratio = safe_float(stock.get("pl_ratio"))

    market_price = nominal
    prev_close = None
    today_change_pct = None
    if snapshot:
        market_price = safe_float(snapshot.get("last_price")) or nominal
        prev_close = safe_float(snapshot.get("prev_close_price"))
        if market_price is not None and prev_close not in (None, 0):
            today_change_pct = round((market_price - prev_close) / prev_close * 100, 2)

    cost_gap_pct = None
    if cost not in (None, 0) and market_price is not None:
        cost_gap_pct = round((cost - market_price) / cost * 100, 2)

    return {
        "nominal_price": nominal,
        "market_price": market_price,
        "cost_price": cost,
        "pl_ratio": pl_ratio,
        "pl_val": safe_float(stock.get("pl_val")),
        "today_pl_val": safe_float(stock.get("today_pl_val")),
        "cost_gap_pct": cost_gap_pct,
        "today_change_pct": today_change_pct,
        "prev_close_price": prev_close,
    }

