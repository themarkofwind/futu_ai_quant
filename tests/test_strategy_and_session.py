from __future__ import annotations

from datetime import datetime

import pytest

from datetime import timedelta, timezone

from futu_ai_quant.market.session import (
    evaluate_volume_confirmed,
    hk_session_volume_fraction,
    is_hk_trading_session,
    is_trading_session,
    is_us_trading_session,
    market_of_code,
    resolve_analysis_interval,
)
from futu_ai_quant.strategy.profile import build_swing_strategy_profile, classify_loss_tier
from futu_ai_quant.strategy.signals import derive_swing_signal, resolve_effective_swing_signal


class TestLossTier:
    def test_deep_loss(self) -> None:
        assert classify_loss_tier(-60.0) == "deep_loss"

    def test_moderate_loss(self) -> None:
        assert classify_loss_tier(-10.0) == "moderate_loss"

    def test_profitable(self) -> None:
        assert classify_loss_tier(5.0) == "profitable"

    def test_unknown(self) -> None:
        assert classify_loss_tier(None) == "unknown"


class TestSwingStrategyProfile:
    def test_deep_loss_prefers_weekly(self) -> None:
        profile = build_swing_strategy_profile(-55.0)
        assert profile["primary_timeframe"] == "weekly"
        assert profile["allow_sell_put"] is False

    def test_moderate_loss_allows_sell_put(self) -> None:
        profile = build_swing_strategy_profile(-20.0)
        assert profile["primary_timeframe"] == "daily"
        assert profile["allow_sell_put"] is True


class TestSwingSignals:
    def test_buy_swing_weekly(self) -> None:
        signal = derive_swing_signal(
            rsi=35.0,
            boll_position="near_lower",
            timeframe="weekly",
            macd_bias="bullish",
            volume_confirmed=True,
        )
        assert signal == "BUY_SWING"

    def test_macd_conflict_downgrades_buy(self) -> None:
        signal = derive_swing_signal(
            rsi=30.0,
            boll_position="below_lower",
            timeframe="daily",
            macd_bias="death_cross",
            volume_confirmed=True,
        )
        assert signal == "HOLD"

    def test_secondary_sell_blocks_primary_buy(self) -> None:
        swing = build_swing_strategy_profile(-20.0)
        combined = resolve_effective_swing_signal(
            "BUY_SWING",
            "SELL_SWING",
            swing,
            primary_timeframe="daily",
        )
        assert combined["effective_signal"] == "HOLD"
        assert combined["signal_note"]


class TestMarketSession:
    def test_weekend_not_trading(self) -> None:
        saturday = datetime(2026, 6, 13, 10, 0, 0)
        assert is_hk_trading_session(saturday) is False

    def test_morning_session(self) -> None:
        morning = datetime(2026, 6, 15, 10, 30, 0)
        assert is_hk_trading_session(morning) is True

    def test_us_session_naive_eastern(self) -> None:
        # 周一 10:00 美东（按已是美东时间处理）
        assert is_us_trading_session(datetime(2026, 6, 15, 10, 0, 0)) is True
        # 周一 08:00 美东（盘前）
        assert is_us_trading_session(datetime(2026, 6, 15, 8, 0, 0)) is False
        # 周六
        assert is_us_trading_session(datetime(2026, 6, 13, 10, 0, 0)) is False

    def test_us_session_dst_summer_beijing(self) -> None:
        beijing_tz = timezone(timedelta(hours=8))
        # 夏令时：北京时间周二 22:00 == 美东周二 10:00（开盘中）
        beijing = datetime(2026, 6, 16, 22, 0, 0, tzinfo=beijing_tz)
        assert is_us_trading_session(beijing) is True
        # 北京时间周二 21:00 == 美东 09:00（尚未开盘）
        beijing_pre = datetime(2026, 6, 16, 21, 0, 0, tzinfo=beijing_tz)
        assert is_us_trading_session(beijing_pre) is False

    def test_market_of_code(self) -> None:
        assert market_of_code("US.AAPL") == "US"
        assert market_of_code("HK.09988") == "HK"
        assert market_of_code("09988") == "HK"

    def test_is_trading_session_dispatch(self) -> None:
        morning_hk = datetime(2026, 6, 15, 10, 30, 0)
        assert is_trading_session("HK", morning_hk) is True
        us_open = datetime(2026, 6, 15, 10, 0, 0)
        assert is_trading_session("US", us_open) is True

    def test_volume_session_fraction_midday(self) -> None:
        noon = datetime(2026, 6, 15, 12, 30, 0)
        fraction = hk_session_volume_fraction(noon)
        assert 0 < fraction < 1.0

    def test_resolve_analysis_interval_fixed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "futu_ai_quant.market.session.ANALYSIS_INTERVAL_SEC",
            600,
        )
        sec, reason = resolve_analysis_interval()
        assert sec == 600
        assert "固定间隔" in reason


class TestVolumeFilter:
    def test_weekly_skips_session_adjust(self) -> None:
        confirmed, ratio, fraction, note = evaluate_volume_confirmed(1.5, "weekly")
        assert confirmed is True
        assert ratio == 1.5
        assert fraction == 1.0
