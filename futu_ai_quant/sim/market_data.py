from __future__ import annotations

from typing import Any

from futu import OpenQuoteContext

from futu_ai_quant.brokers.futu.quotes import fetch_snapshot_map
from futu_ai_quant.domain.positions import is_option_code
from futu_ai_quant.sim.options import fetch_option_quote_map, resolve_contract_size
from futu_ai_quant.sim.portfolio import PaperPortfolio
from futu_ai_quant.utils.numbers import safe_float


def fetch_market_data(
    quote_ctx: OpenQuoteContext,
    codes: list[str],
) -> tuple[dict[str, float], dict[str, dict[str, Any]]]:
    stock_codes = [code for code in codes if not is_option_code(code)]
    option_codes = [code for code in codes if is_option_code(code)]
    prices: dict[str, float] = {}
    if stock_codes:
        snapshot_map = fetch_snapshot_map(quote_ctx, list(dict.fromkeys(stock_codes)))
        for code, row in snapshot_map.items():
            price = safe_float(row.get("last_price"))
            if price is not None:
                prices[code] = price
    option_quote_map = fetch_option_quote_map(quote_ctx, option_codes)
    for code, meta in option_quote_map.items():
        if meta.get("price") is not None:
            prices[code] = meta["price"]
    return prices, option_quote_map


def mark_to_market(
    portfolio: PaperPortfolio,
    prices: dict[str, float],
    option_quote_map: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    stock_mv = 0.0
    stock_cost = 0.0
    stock_unrealized = 0.0
    stock_positions: list[dict[str, Any]] = []

    for code, pos in portfolio.data["stocks"].items():
        qty = int(pos["qty"])
        price = prices.get(code, safe_float(pos.get("cost_price")) or 0.0)
        cost = safe_float(pos.get("cost_price")) or 0.0
        mv = price * qty
        unrealized = (price - cost) * qty
        stock_mv += mv
        stock_cost += cost * qty
        stock_unrealized += unrealized
        stock_positions.append(
            {
                "code": code,
                "qty": qty,
                "price": price,
                "market_value": round(mv, 2),
                "unrealized_pnl": round(unrealized, 2),
            }
        )

    option_mv = 0.0
    option_unrealized = 0.0
    option_positions: list[dict[str, Any]] = []
    for code, pos in portfolio.data["options"].items():
        qty = int(pos["qty"])
        contract_size = resolve_contract_size(code, option_quote_map, portfolio)
        price = prices.get(code, safe_float(pos.get("cost_price")) or 0.0)
        cost = safe_float(pos.get("cost_price")) or 0.0
        signed_mv = price * contract_size * qty
        if qty < 0:
            unrealized = (cost - price) * contract_size * abs(qty)
        else:
            unrealized = (price - cost) * contract_size * abs(qty)
        option_mv += signed_mv
        option_unrealized += unrealized
        option_positions.append(
            {
                "code": code,
                "qty": qty,
                "price": price,
                "market_value": round(signed_mv, 2),
                "unrealized_pnl": round(unrealized, 2),
            }
        )

    cash = float(portfolio.data["cash_hkd"])
    total_nav = cash + stock_mv + option_mv
    return {
        "cash_hkd": round(cash, 2),
        "stock_market_value": round(stock_mv, 2),
        "option_market_value": round(option_mv, 2),
        "total_nav": round(total_nav, 2),
        "stock_unrealized_pnl": round(stock_unrealized, 2),
        "option_unrealized_pnl": round(option_unrealized, 2),
        "total_unrealized_pnl": round(stock_unrealized + option_unrealized, 2),
        "realized_pnl": portfolio.data["stats"]["realized_pnl"],
        "total_fees": portfolio.data["stats"]["total_fees"],
        "stock_positions": stock_positions,
        "option_positions": option_positions,
        "pending_orders": len(portfolio.data["pending_orders"]),
    }
