from __future__ import annotations

from typing import Any


def find_missing_recommendation_codes(
    decision: dict[str, Any],
    required_codes: list[str],
) -> list[str]:
    returned_codes = {
        rec.get("code")
        for rec in decision.get("recommendations", [])
        if isinstance(rec, dict) and rec.get("code")
    }
    return [code for code in required_codes if code not in returned_codes]


def validate_decision_schema(
    decision: dict[str, Any],
    required_codes: list[str] | None = None,
    stocks_by_code: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if "portfolio_risk_summary" not in decision:
        raise ValueError("缺少 portfolio_risk_summary 字段")
    if "recommendations" not in decision or not isinstance(decision["recommendations"], list):
        raise ValueError("缺少 recommendations 字段或类型错误")

    for idx, rec in enumerate(decision["recommendations"]):
        required = (
            "code",
            "name",
            "action",
            "confidence",
            "reasoning",
            "suggested_trigger",
            "stock_trade_plan",
            "option_trade_plan",
        )
        missing = [field for field in required if field not in rec]
        if missing:
            raise ValueError(f"recommendations[{idx}] 缺少字段: {missing}")

        stock_plan = rec.get("stock_trade_plan")
        option_plan = rec.get("option_trade_plan")
        if not isinstance(stock_plan, dict):
            raise ValueError(f"recommendations[{idx}] stock_trade_plan 必须为对象")
        if not isinstance(option_plan, dict):
            raise ValueError(f"recommendations[{idx}] option_trade_plan 必须为对象")

        lot_size = int(stock_plan.get("lot_size") or 0)
        suggested_qty = int(stock_plan.get("suggested_qty") or 0)
        suggested_lots = int(stock_plan.get("suggested_lots") or 0)
        if suggested_qty > 0:
            if lot_size <= 0:
                raise ValueError(f"recommendations[{idx}] 有交易数量但缺少 lot_size")
            if suggested_qty % lot_size != 0:
                raise ValueError(
                    f"recommendations[{idx}] suggested_qty={suggested_qty} "
                    f"不是整手（lot_size={lot_size}）"
                )
            if suggested_lots * lot_size != suggested_qty:
                raise ValueError(
                    f"recommendations[{idx}] suggested_lots({suggested_lots}) 与 "
                    f"suggested_qty({suggested_qty}) 不自洽"
                )

        if stocks_by_code and rec.get("code") in stocks_by_code:
            ref_plan = stocks_by_code[rec["code"]].get("stock_trade_plan") or {}
            ref_qty = int(ref_plan.get("suggested_qty") or 0)
            if ref_qty > 0 and suggested_qty != ref_qty:
                raise ValueError(
                    f"recommendations[{idx}] suggested_qty 应为预计算的 {ref_qty}，"
                    f"实际为 {suggested_qty}"
                )

        if option_plan.get("action") not in (None, "none"):
            if not option_plan.get("contract_code") or not option_plan.get("expire_date"):
                raise ValueError(
                    f"recommendations[{idx}] 期权操作缺少 contract_code 或 expire_date"
                )

    if required_codes:
        missing_codes = find_missing_recommendation_codes(decision, required_codes)
        if missing_codes:
            raise ValueError(f"recommendations 未覆盖全部持仓，缺少: {missing_codes}")
        if len(decision["recommendations"]) != len(required_codes):
            raise ValueError(
                f"recommendations 数量应为 {len(required_codes)}，"
                f"实际为 {len(decision['recommendations'])}"
            )
    return decision
