from __future__ import annotations

from futu_ai_quant.market.fees import estimate_hk_stock_trade_fees, swing_trade_meets_cost_threshold
from futu_ai_quant.sim.fees import FeeBreakdown, HKCostModel


class TestHKFees:
    def test_sell_includes_stamp_duty(self) -> None:
        sell_fees = estimate_hk_stock_trade_fees("sell", 100_000)
        buy_fees = estimate_hk_stock_trade_fees("buy", 100_000)
        assert sell_fees > buy_fees

    def test_swing_cost_threshold_passes_with_atr(self) -> None:
        ok, note = swing_trade_meets_cost_threshold(
            direction="buy",
            suggested_qty=500,
            market_price=100.0,
            atr_market=5.0,
        )
        assert ok is True
        assert note is None

    def test_sim_cost_model_total(self) -> None:
        fees = HKCostModel().calc_stock_fees("sell", 50_000)
        assert isinstance(fees, FeeBreakdown)
        assert fees.total == round(fees.commission + fees.platform_fee + fees.stamp_duty, 4)
