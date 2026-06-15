from __future__ import annotations

from futu_ai_quant.domain.positions import (
    build_position_direction,
    infer_option_right,
    is_option_code,
    resolve_position_side,
)
from futu_ai_quant.market.lot import calc_full_lot_trade_qty, round_down_to_lot
from futu_ai_quant.utils.numbers import safe_float


class TestOptionCode:
    def test_hk_option_code(self) -> None:
        assert is_option_code("HK.ALB260629C120000") is True

    def test_stock_code(self) -> None:
        assert is_option_code("HK.09988") is False


class TestPositionSide:
    def test_short_from_negative_qty(self) -> None:
        assert resolve_position_side("", -2.0) == "SHORT"

    def test_option_right_from_code(self) -> None:
        assert infer_option_right("HK.ALB260629C120000", "") == "CALL"
        assert infer_option_right("HK.ALB260629P120000", "") == "PUT"

    def test_sell_call_direction_label(self) -> None:
        label = build_position_direction("SHORT", -1, "HK.ALB260629C120000", is_option=True)
        assert "卖出" in label
        assert "Call" in label


class TestLotSizing:
    def test_round_down_to_lot(self) -> None:
        assert round_down_to_lot(550, 100) == 500

    def test_calc_full_lot_sell(self) -> None:
        qty, lots, note = calc_full_lot_trade_qty(
            holding_qty=1000,
            tradable_qty=800,
            lot_size=100,
            max_pct=10,
            for_sell=True,
        )
        assert qty == 100
        assert lots == 1
        assert note is None

    def test_calc_full_lot_insufficient(self) -> None:
        qty, lots, note = calc_full_lot_trade_qty(
            holding_qty=50,
            tradable_qty=50,
            lot_size=100,
            max_pct=10,
            for_sell=True,
        )
        assert qty == 0
        assert note is not None


class TestSafeFloat:
    def test_none(self) -> None:
        assert safe_float(None) is None

    def test_string_number(self) -> None:
        assert safe_float("12.5") == 12.5

    def test_invalid(self) -> None:
        assert safe_float("abc") is None
