"""盘中周期信号与自选综合逻辑单测。"""

from __future__ import annotations

from futu_ai_quant.config import watchlist as wl
from futu_ai_quant.strategy.signals import (
    derive_swing_signal,
    resolve_watchlist_combined_signal,
)


class TestIntradaySwingSignal:
    def test_more_sensitive_than_daily(self) -> None:
        # RSI=38 + near_lower：日K仍 HOLD，盘中可为 BUY
        daily = derive_swing_signal(38, "near_lower", "daily", macd_bias="neutral", volume_confirmed=True)
        intra = derive_swing_signal(38, "near_lower", "intraday", macd_bias="neutral", volume_confirmed=False)
        assert daily == "HOLD"
        assert intra == "BUY_SWING"

    def test_intraday_no_volume_gate(self) -> None:
        sig = derive_swing_signal(
            32,
            "below_lower",
            "intraday",
            macd_bias="neutral",
            volume_confirmed=False,
        )
        assert sig == "BUY_SWING"


class TestWatchlistCombine:
    def test_auction_uses_daily_weekly(self) -> None:
        combined = resolve_watchlist_combined_signal(
            daily={"swing_signal": "BUY_SWING"},
            weekly={"swing_signal": "HOLD"},
            use_intraday=False,
        )
        assert combined["effective_signal"] == "BUY_SWING"
        assert combined["primary_timeframe"] == "daily"

    def test_lunch_uses_intraday_with_weekly_veto(self) -> None:
        combined = resolve_watchlist_combined_signal(
            daily={"swing_signal": "HOLD"},
            weekly={"swing_signal": "SELL_SWING"},
            intraday={"swing_signal": "BUY_SWING"},
            use_intraday=True,
        )
        assert combined["effective_signal"] == "HOLD"
        assert "周线偏空" in str(combined.get("signal_note") or "")

    def test_slot_config(self, monkeypatch) -> None:
        monkeypatch.setenv("WATCHLIST_INTRADAY_SLOTS", "lunch,preclose")
        assert wl.watchlist_slot_uses_intraday("lunch") is True
        assert wl.watchlist_slot_uses_intraday("auction") is False
        assert wl.watchlist_slot_uses_intraday("manual") is False
