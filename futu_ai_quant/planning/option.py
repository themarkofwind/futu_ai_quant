from __future__ import annotations

from typing import Any

from futu_ai_quant.domain.positions import resolve_position_side
from futu_ai_quant.indicators.iv import calc_max_covered_calls, cap_option_contracts
from futu_ai_quant.market.lot import resolve_lot_size
from futu_ai_quant.utils.numbers import safe_float


def build_option_trade_plan_for_stock(
    stock: dict[str, Any],
    option_overlay: dict[str, Any],
    swing_strategy: dict[str, Any],
    combined_signal: dict[str, Any],
) -> dict[str, Any] | None:
    qty = safe_float(stock.get("qty")) or 0.0
    signal = combined_signal.get("effective_signal", combined_signal.get("primary_signal", "HOLD"))
    lot_size = resolve_lot_size(None, stock)
    candidates = option_overlay.get("sell_call_candidates") or []
    put_candidates = option_overlay.get("sell_put_candidates") or []

    if signal in ("SELL_SWING", "HOLD") and candidates and swing_strategy.get("prefer_sell_call"):
        best = candidates[0]
        contract_size = int(safe_float(best.get("contract_size")) or lot_size or 100)
        max_contracts = calc_max_covered_calls(qty, contract_size)
        if max_contracts <= 0:
            return None
        contracts = cap_option_contracts(max_contracts)
        premium = safe_float(best.get("last_price")) or 0.0
        iv_suffix = ""
        if best.get("iv_rank") is not None:
            iv_suffix = f" 历史IV Rank={best.get('iv_rank')}"
        elif best.get("iv_relative") is not None:
            iv_suffix = f" 当次相对IV={best.get('iv_relative')}"
        return {
            "action": "sell_call",
            "plan_source": "suggested",
            "contract_code": best.get("code"),
            "expire_date": best.get("expire_time"),
            "strike_price": best.get("strike_price"),
            "days_to_expiry": best.get("days_to_expiry"),
            "delta": best.get("delta"),
            "contracts": contracts,
            "contract_size": contract_size,
            "shares_per_lot": lot_size,
            "max_covered_contracts": max_contracts,
            "implied_volatility": best.get("implied_volatility"),
            "iv_relative": best.get("iv_relative"),
            "iv_rank": best.get("iv_rank"),
            "iv_rank_note": best.get("iv_rank_note"),
            "premium_per_share": premium,
            "estimated_total_premium": round(premium * contract_size * contracts, 2),
            "label": (
                f"卖出 {contracts} 张（备兑 {contracts * contract_size} 股，每张 {contract_size} 股）"
                f"{best.get('expire_time')} 到期 {best.get('strike_price')} Call"
                f"{iv_suffix}"
            ),
        }

    if (
        signal == "BUY_SWING"
        and put_candidates
        and swing_strategy.get("allow_sell_put")
    ):
        best = put_candidates[0]
        contract_size = int(safe_float(best.get("contract_size")) or lot_size or 100)
        contracts = 1
        premium = safe_float(best.get("last_price")) or 0.0
        iv_suffix = ""
        if best.get("iv_rank") is not None:
            iv_suffix = f" 历史IV Rank={best.get('iv_rank')}"
        elif best.get("iv_relative") is not None:
            iv_suffix = f" 当次相对IV={best.get('iv_relative')}"
        return {
            "action": "sell_put",
            "plan_source": "suggested",
            "contract_code": best.get("code"),
            "expire_date": best.get("expire_time"),
            "strike_price": best.get("strike_price"),
            "days_to_expiry": best.get("days_to_expiry"),
            "delta": best.get("delta"),
            "contracts": contracts,
            "contract_size": contract_size,
            "implied_volatility": best.get("implied_volatility"),
            "iv_relative": best.get("iv_relative"),
            "iv_rank": best.get("iv_rank"),
            "iv_rank_note": best.get("iv_rank_note"),
            "premium_per_share": premium,
            "estimated_total_premium": round(premium * contract_size * contracts, 2),
            "label": (
                f"卖出 {contracts} 张 {best.get('expire_time')} 到期 "
                f"{best.get('strike_price')} Put"
                f"{iv_suffix}"
            ),
        }

    return None


def build_option_position_trade_plan(option: dict[str, Any]) -> dict[str, Any]:
    side = resolve_position_side(
        str(option.get("position_side", "")),
        safe_float(option.get("qty")) or 0.0,
    )
    contracts = int(abs(safe_float(option.get("qty")) or 0))
    days = safe_float(option.get("days_to_expiry"))
    plan: dict[str, Any] = {
        "action": "none",
        "contract_code": option.get("code"),
        "expire_date": option.get("expire_time") or option.get("expire_date"),
        "strike_price": option.get("strike_price"),
        "contracts": contracts,
        "premium_per_share": safe_float(option.get("last_price")),
        "estimated_total_premium": None,
        "label": "持有观望",
    }

    if side == "SHORT" and days is not None and days <= 21:
        plan.update(
            {
                "action": "roll",
                "label": (
                    f"距到期 {int(days)} 天，可考虑买回平仓并卖出更远月份同方向合约"
                ),
            }
        )
    elif side == "SHORT":
        plan.update(
            {
                "action": "hold_short",
                "label": "卖方持有，等待时间价值衰减",
            }
        )

    return plan


def empty_option_trade_plan() -> dict[str, Any]:
    return {
        "action": "none",
        "contract_code": "",
        "expire_date": "",
        "strike_price": 0,
        "contracts": 0,
        "premium_per_share": 0,
        "estimated_total_premium": 0,
        "plan_source": "none",
    }
