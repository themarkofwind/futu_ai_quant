"""日内做 T 目标价差（费用校正）单元测试。"""

from __future__ import annotations

from futu_ai_quant.strategy.intraday_t_cost import (
    boll_width_target_spread,
    estimate_round_trip_t_fees,
    min_target_spread_from_fees,
    resolve_entry_target_spread,
    resolve_intraday_t_target_spread,
)


class TestIntradayTCost:
    def test_jiangtong_fees_and_min_spread(self) -> None:
        fees = estimate_round_trip_t_fees("HK.00358", 30.0, 1000)
        assert 50 < fees < 100
        min_spread = min_target_spread_from_fees(fees, 1000, cost_ratio=2.0)
        assert min_spread < 1.2

    def test_high_price_hk_raises_min_spread(self) -> None:
        fees = estimate_round_trip_t_fees("HK.00700", 400.0, 1000)
        min_spread = min_target_spread_from_fees(fees, 1000, cost_ratio=2.0)
        assert min_spread > 1.2

    def test_resolve_uses_max_of_manual_and_fee_floor(self) -> None:
        class _Quote:
            pass

        import futu_ai_quant.strategy.intraday_t_cost as cost_mod

        original = cost_mod.fetch_snapshot_map
        try:
            cost_mod.fetch_snapshot_map = lambda *a, **k: {
                "HK.00700": {"last_price": 400.0},
            }
            spread, note = resolve_intraday_t_target_spread(
                _Quote(),
                "HK.00700",
                lot_size=1000,
                manual_spread=1.2,
                cost_ratio=2.0,
                auto=True,
            )
        finally:
            cost_mod.fetch_snapshot_map = original

        assert spread > 1.2
        assert "已高于配置值" in note

    def test_resolve_keeps_manual_when_auto_off(self) -> None:
        class _Quote:
            pass

        spread, note = resolve_intraday_t_target_spread(
            _Quote(),
            "HK.00700",
            lot_size=1000,
            manual_spread=1.2,
            auto=False,
        )
        assert spread == 1.2
        assert "未启用" in note

    def test_boll_width_target_spread(self) -> None:
        assert boll_width_target_spread(130.0, 126.0, ratio=0.45) == 1.8
        assert boll_width_target_spread(130.0, 126.0, ratio=0.0) == 0.0
        assert boll_width_target_spread(None, 126.0, ratio=0.45) == 0.0

    def test_resolve_entry_uses_max_of_base_and_boll(self) -> None:
        assert resolve_entry_target_spread(1.2, boll_upper=145.0, boll_lower=138.0) == 3.15
        assert resolve_entry_target_spread(2.5, boll_upper=130.0, boll_lower=128.0) == 2.5
