from __future__ import annotations

from pathlib import Path

import pytest

from futu_ai_quant.sim.engine import LocalSimEngine
from futu_ai_quant.sim.fees import HKCostModel
from futu_ai_quant.sim.portfolio import PaperPortfolio


@pytest.fixture
def portfolio(tmp_path: Path) -> PaperPortfolio:
    p = PaperPortfolio(path=tmp_path / "portfolio.json")
    p.init_from_cash(1_000_000)
    return p


@pytest.fixture
def engine(portfolio: PaperPortfolio) -> LocalSimEngine:
    return LocalSimEngine(portfolio, HKCostModel())


class TestPaperPortfolio:
    def test_buy_and_sell_stock(self, engine: LocalSimEngine, portfolio: PaperPortfolio) -> None:
        trade = engine.execute_stock(
            code="HK.00700",
            name="腾讯",
            side="buy",
            qty=100,
            price=300.0,
            lot_size=100,
            decision_id="test",
            reason="unit test",
        )
        assert trade is not None
        assert portfolio.get_stock("HK.00700")["qty"] == 100

        sell = engine.execute_stock(
            code="HK.00700",
            name="腾讯",
            side="sell",
            qty=100,
            price=310.0,
            lot_size=100,
            decision_id="test",
            reason="unit test sell",
        )
        assert sell is not None
        assert portfolio.get_stock("HK.00700") is None

    def test_insufficient_cash_rejects_buy(self, engine: LocalSimEngine) -> None:
        trade = engine.execute_stock(
            code="HK.09988",
            name="阿里",
            side="buy",
            qty=10000,
            price=200.0,
            lot_size=100,
            decision_id="test",
            reason="too big",
        )
        assert trade is None
