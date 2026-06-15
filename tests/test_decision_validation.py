from __future__ import annotations

import pytest

from futu_ai_quant.decision.validation import (
    find_missing_recommendation_codes,
    validate_decision_schema,
)
from futu_ai_quant.planning.stock import empty_stock_trade_plan


def _sample_recommendation(code: str, *, qty: int = 0, lot_size: int = 100) -> dict:
    return {
        "code": code,
        "name": "测试",
        "action": "HOLD",
        "confidence": 0.8,
        "reasoning": "测试",
        "suggested_trigger": "无",
        "stock_trade_plan": {
            **empty_stock_trade_plan(),
            "lot_size": lot_size,
            "suggested_qty": qty,
            "suggested_lots": qty // lot_size if lot_size else 0,
        },
        "option_trade_plan": {
            "action": "none",
            "contract_code": "",
            "expire_date": "",
            "strike_price": 0,
            "contracts": 0,
            "premium_per_share": 0,
            "estimated_total_premium": 0,
        },
    }


class TestDecisionValidation:
    def test_find_missing_codes(self) -> None:
        decision = {"recommendations": [{"code": "HK.00700"}]}
        missing = find_missing_recommendation_codes(decision, ["HK.00700", "HK.09988"])
        assert missing == ["HK.09988"]

    def test_validate_complete_decision(self) -> None:
        required = ["HK.00700", "HK.09988"]
        decision = {
            "portfolio_risk_summary": "ok",
            "recommendations": [_sample_recommendation(c) for c in required],
        }
        validated = validate_decision_schema(decision, required)
        assert len(validated["recommendations"]) == 2

    def test_reject_fractional_lot(self) -> None:
        decision = {
            "portfolio_risk_summary": "ok",
            "recommendations": [
                _sample_recommendation("HK.00700", qty=150, lot_size=100),
            ],
        }
        with pytest.raises(ValueError, match="整手"):
            validate_decision_schema(decision, ["HK.00700"])

    def test_reject_missing_position(self) -> None:
        decision = {
            "portfolio_risk_summary": "ok",
            "recommendations": [_sample_recommendation("HK.00700")],
        }
        with pytest.raises(ValueError, match="未覆盖"):
            validate_decision_schema(decision, ["HK.00700", "HK.09988"])
