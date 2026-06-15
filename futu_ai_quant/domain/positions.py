from __future__ import annotations

import re
from typing import Any

import pandas as pd
from futu import OpenQuoteContext, RET_OK

from futu_ai_quant.config.settings import HK_OPTION_CODE_PATTERN
from futu_ai_quant.utils.logging import log
from futu_ai_quant.utils.numbers import safe_float


def resolve_position_side(position_side: str, qty: float) -> str:
    side = str(position_side or "").upper()
    if side in ("LONG", "SHORT"):
        return side
    if qty < 0:
        return "SHORT"
    if qty > 0:
        return "LONG"
    return "UNKNOWN"


def infer_option_right(code: str, option_type: str = "") -> str | None:
    normalized = str(option_type or "").upper()
    if normalized in ("CALL", "PUT"):
        return normalized
    if not code or "." not in code:
        return None
    symbol = code.split(".", 1)[1]
    match = re.search(r"([CP])\d+$", symbol, re.IGNORECASE)
    if not match:
        return None
    return "CALL" if match.group(1).upper() == "C" else "PUT"


def build_position_direction(
    position_side: str,
    qty: float,
    code: str,
    option_type: str = "",
    is_option: bool = False,
) -> str:
    side = resolve_position_side(position_side, qty)
    side_label = {"LONG": "买入", "SHORT": "卖出", "UNKNOWN": "未知方向"}.get(side, "未知方向")

    if not is_option:
        return "买入持仓" if side == "LONG" else "卖空持仓" if side == "SHORT" else "未知方向持仓"

    right = infer_option_right(code, option_type)
    right_label = {"CALL": "Call", "PUT": "Put"}.get(right or "", "期权")
    return f"{side_label}{right_label}"


def enrich_option_context(option: dict[str, Any]) -> dict[str, Any]:
    enriched = {**option}
    qty = safe_float(enriched.get("qty")) or 0.0
    side = resolve_position_side(str(enriched.get("position_side", "")), qty)
    option_type = str(enriched.get("option_type", ""))

    enriched["abs_qty"] = abs(qty)
    enriched["position_side"] = side if side != "UNKNOWN" else enriched.get("position_side", "N/A")
    enriched["position_direction"] = build_position_direction(
        str(enriched.get("position_side", "")),
        qty,
        str(enriched.get("code", "")),
        option_type=option_type,
        is_option=True,
    )

    theta = safe_float(enriched.get("theta"))
    if side == "SHORT":
        enriched["theta_position_effect"] = (
            "空头卖方受益于时间价值衰减；若维持虚值，临近到期权利金有望加速收敛为盈利"
        )
        if theta is not None:
            enriched["theta_daily_benefit_estimate"] = round(abs(theta) * enriched["abs_qty"], 6)
    elif side == "LONG":
        enriched["theta_position_effect"] = (
            "多头买方承担时间价值衰减；临近到期剩余权利金损耗加速，虚值合约面临归零风险"
        )
        if theta is not None:
            enriched["theta_daily_cost_estimate"] = round(abs(theta) * enriched["abs_qty"], 6)
    else:
        enriched["theta_position_effect"] = "持仓方向未知，请结合 position_side 与 qty 判断 Theta 影响"

    return enriched


def is_option_code(code: str) -> bool:
    if not code or "." not in code:
        return False
    symbol = code.split(".", 1)[1]
    if HK_OPTION_CODE_PATTERN.match(symbol):
        return True
    # 兜底：含 C/P 行权标识且长度明显大于普通正股代码
    return bool(re.search(r"[CP]\d{3,}$", symbol)) and len(symbol) > 8


def classify_positions(
    positions: pd.DataFrame,
    quote_ctx: OpenQuoteContext | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    stocks: list[dict[str, Any]] = []
    options: list[dict[str, Any]] = []
    option_valid_map: dict[str, bool] = {}

    if positions.empty:
        return stocks, options

    candidate_codes = positions["code"].tolist()
    if quote_ctx is not None:
        try:
            ret, snapshot = quote_ctx.get_market_snapshot(candidate_codes)
            if ret == RET_OK and not snapshot.empty:
                for _, row in snapshot.iterrows():
                    option_valid_map[row["code"]] = bool(row.get("option_valid", False))
        except Exception as exc:
            log("分类", f"快照辅助分类失败，将仅使用代码规则: {exc}")

    for _, row in positions.iterrows():
        code = str(row.get("code", ""))
        qty = safe_float(row.get("qty")) or 0.0
        if qty == 0:
            continue

        position_side = str(row.get("position_side", "N/A"))
        is_option = option_valid_map.get(code, False) or is_option_code(code)
        item = {
            "code": code,
            "name": str(row.get("stock_name", "")),
            "qty": qty,
            "can_sell_qty": safe_float(row.get("can_sell_qty")),
            "cost_price": safe_float(row.get("cost_price")),
            "nominal_price": safe_float(row.get("nominal_price")),
            "market_val": safe_float(row.get("market_val")),
            "pl_ratio": safe_float(row.get("pl_ratio")),
            "pl_val": safe_float(row.get("pl_val")),
            "today_pl_val": safe_float(row.get("today_pl_val")),
            "position_side": resolve_position_side(position_side, qty),
            "position_type": str(row.get("position_type", "N/A")),
            "strategy_type": str(row.get("strategy_type", "N/A")),
            "position_direction": build_position_direction(
                position_side,
                qty,
                code,
                is_option=is_option,
            ),
        }

        if is_option:
            options.append(enrich_option_context(item))
        else:
            stocks.append(item)

    return stocks, options


def resolve_option_underlying_code(option: dict[str, Any]) -> str:
    owner = str(option.get("stock_owner") or "").strip()
    if owner:
        return owner
    code = str(option.get("code", ""))
    if not code or "." not in code:
        return ""
    symbol = code.split(".", 1)[1]
    match = re.match(r"^([A-Z]+)", symbol)
    if not match:
        return ""
    # 常见港股期权代码前缀 -> 正股（与 sim_trader 保持一致时可扩展）
    prefix_map = {
        "ALB": "HK.09988",
        "TCH": "HK.00700",
        "KST": "HK.01024",
        "ALC": "HK.02600",
        "JXC": "HK.00358",
    }
    return prefix_map.get(match.group(1), "")
