from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from futu_ai_quant.decision.validation import validate_decision_schema
from futu_ai_quant.market.triggers import price_in_trigger
from futu_ai_quant.pipeline.cycle import _resolve_decision
from futu_ai_quant.sim.engine import LocalSimEngine
from futu_ai_quant.utils.files import atomic_write_text


class TestAtomicWrite:
    def test_atomic_write_text(self, tmp_path: Path) -> None:
        target = tmp_path / "data.json"
        atomic_write_text(target, '{"ok": true}')
        assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
        assert not target.with_suffix(".json.tmp").exists()


class TestPriceInTrigger:
    def test_inside_range(self) -> None:
        assert price_in_trigger(100.0, 95.0, 105.0) is True

    def test_outside_range(self) -> None:
        assert price_in_trigger(110.0, 95.0, 105.0) is False

    def test_none_price(self) -> None:
        assert price_in_trigger(None, 95.0, 105.0) is False


class TestRollOpenAction:
    def test_put_roll(self) -> None:
        assert LocalSimEngine._roll_open_action({"option_type": "PUT"}, "HK.X") == "sell_put"

    def test_call_from_code(self) -> None:
        assert (
            LocalSimEngine._roll_open_action({"code": "HK.ALB260629C120000"}, "HK.X")
            == "sell_call"
        )


def _minimal_stock(code: str = "HK.00700") -> dict:
    empty_plan = {
        "direction": "none",
        "suggested_qty": 0,
        "suggested_lots": 0,
        "lot_size": 100,
        "trigger_price_low": None,
        "trigger_price_high": None,
    }
    empty_opt = {"action": "none"}
    return {
        "code": code,
        "name": "腾讯",
        "swing_strategy": {"loss_tier": "profitable", "primary_timeframe": "weekly"},
        "combined_swing_signal": {"effective_signal": "HOLD"},
        "daily": {"swing_signal": "HOLD"},
        "weekly": {"swing_signal": "HOLD"},
        "stock_trade_plan": empty_plan,
        "option_trade_plan": empty_opt,
        "pnl": {"pl_ratio": 10.0},
    }


def _minimal_decision_payload(codes: list[str]) -> dict:
    return {
        "summary": {"stock_count": len(codes)},
        "stocks": [_minimal_stock(c) for c in codes if not c.startswith("HK.ALB")],
        "options": [],
        "required_positions": [{"code": c} for c in codes],
    }


class TestResolveDecision:
    def test_rules_when_no_ai(self) -> None:
        stocks = [_minimal_stock()]
        options: list = []
        payload = _minimal_decision_payload(["HK.00700"])
        decision, source = _resolve_decision(
            use_ai=False,
            ai_client=None,
            payload=payload,
            stocks=stocks,
            options=options,
            required_codes=["HK.00700"],
            stocks_by_code={"HK.00700": stocks[0]},
        )
        assert source == "rules"
        assert len(decision["recommendations"]) == 1

    def test_ai_fallback_on_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stocks = [_minimal_stock()]
        options: list = []
        payload = _minimal_decision_payload(["HK.00700"])

        def boom(_client, _payload):
            raise ValueError("API down")

        monkeypatch.setattr(
            "futu_ai_quant.pipeline.cycle.call_llm_decision",
            boom,
        )
        client = MagicMock()
        decision, source = _resolve_decision(
            use_ai=True,
            ai_client=client,
            payload=payload,
            stocks=stocks,
            options=options,
            required_codes=["HK.00700"],
            stocks_by_code={"HK.00700": stocks[0]},
        )
        assert source == "rules_fallback"
        validate_decision_schema(decision, ["HK.00700"], {"HK.00700": stocks[0]})
