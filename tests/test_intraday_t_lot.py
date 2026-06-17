"""日内做 T 股数解析单元测试。"""

from __future__ import annotations

import pandas as pd

from futu_ai_quant.market.lot import calc_full_lot_trade_qty
from futu_ai_quant.strategy.intraday_t_lot import find_stock_position, resolve_intraday_t_lot_size


class TestFindStockPosition:
    def test_finds_long_stock(self) -> None:
        df = pd.DataFrame(
            [{"code": "HK.00358", "qty": 2000, "can_sell_qty": 2000}]
        )
        pos = find_stock_position(df, "HK.00358")
        assert pos is not None
        assert pos["qty"] == 2000

    def test_skips_option(self) -> None:
        df = pd.DataFrame(
            [{"code": "HK.ALB260629C120000", "qty": 10, "can_sell_qty": 10}]
        )
        assert find_stock_position(df, "HK.ALB260629C120000") is None


class TestJiangtongLotPct:
    def test_50pct_two_lots(self) -> None:
        qty, lots, note = calc_full_lot_trade_qty(
            holding_qty=2000,
            tradable_qty=2000,
            lot_size=1000,
            max_pct=50,
            for_sell=True,
        )
        assert qty == 1000
        assert lots == 1
        assert note is None


class TestResolveIntradayTLotSize:
    def test_auto_from_position(self) -> None:
        positions = pd.DataFrame(
            [{"code": "HK.00358", "qty": 2000, "can_sell_qty": 2000}]
        )

        class _Trade:
            pass

        class _Quote:
            pass

        import futu_ai_quant.strategy.intraday_t_lot as lot_mod

        original_get = lot_mod.get_position_list
        original_snap = lot_mod.fetch_snapshot_map
        try:
            lot_mod.get_position_list = lambda *a, **k: (0, positions)
            lot_mod.fetch_snapshot_map = lambda *a, **k: {
                "HK.00358": {"lot_size": 1000},
            }
            qty, note = resolve_intraday_t_lot_size(
                _Quote(),
                _Trade(),
                "HK.00358",
                lot_pct=50,
                fallback_lot_size=100,
            )
        finally:
            lot_mod.get_position_list = original_get
            lot_mod.fetch_snapshot_map = original_snap

        assert qty == 1000
        assert "50%" in note

    def test_fallback_when_no_position(self) -> None:
        import futu_ai_quant.strategy.intraday_t_lot as lot_mod

        original_get = lot_mod.get_position_list

        class _Ctx:
            pass

        try:
            lot_mod.get_position_list = lambda *a, **k: (0, pd.DataFrame())
            qty, note = resolve_intraday_t_lot_size(
                _Ctx(),
                _Ctx(),
                "HK.09988",
                lot_pct=30,
                fallback_lot_size=500,
            )
        finally:
            lot_mod.get_position_list = original_get

        assert qty == 500
        assert "未找到" in note
