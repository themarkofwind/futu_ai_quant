"""数据质量门控、lot 可信度与 K 线本轮缓存测试。"""

from __future__ import annotations

import time

import pandas as pd
import pytest
from futu import KLType, RET_OK

from futu_ai_quant.analysis.data_quality import attach_data_quality
from futu_ai_quant.indicators import kline_cache
from futu_ai_quant.market.lot import resolve_lot_size_detail
from futu_ai_quant.planning.stock import build_stock_trade_plan


def test_resolve_lot_size_detail_confirmed_from_snapshot() -> None:
    lot, confirmed = resolve_lot_size_detail({"lot_size": 200}, None)
    assert lot == 200
    assert confirmed is True


def test_resolve_lot_size_detail_unconfirmed_fallback() -> None:
    lot, confirmed = resolve_lot_size_detail(None, None)
    assert lot == 100
    assert confirmed is False


def test_data_quality_degrades_signal_and_blocks_trade_plan() -> None:
    stock = {
        "code": "HK.06675",
        "qty": 1000,
        "can_sell_qty": 1000,
        "lot_size": 100,
        "lot_confirmed": False,
        "daily": {"error": "K线数据为空", "swing_signal": "WAIT"},
        "weekly": {"swing_signal": "WAIT"},
        "swing_strategy": {"max_swing_position_pct": 20, "loss_tier": "moderate_loss"},
        "combined_swing_signal": {
            "effective_signal": "SELL_SWING",
            "primary_signal": "SELL_SWING",
            "secondary_signal": "HOLD",
        },
        "pnl": {"market_price": 18.36},
    }
    attach_data_quality(stock, snapshot=None, lot_confirmed=False)
    assert stock["data_quality"]["status"] == "degraded"
    assert stock["combined_swing_signal"]["effective_signal"] == "WAIT"

    plan = build_stock_trade_plan(
        stock,
        stock["swing_strategy"],
        stock["combined_swing_signal"],
        None,
        stock["pnl"],
    )
    assert plan["direction"] == "none"
    assert plan["suggested_qty"] == 0
    assert plan["trigger_price_low"] is None
    assert plan["trigger_price_high"] is None
    assert plan["watch_triggers"] == []


def test_hold_watch_triggers_for_moderate_loss() -> None:
    stock = {
        "code": "HK.00358",
        "qty": 1000,
        "can_sell_qty": 1000,
        "lot_size": 100,
        "lot_confirmed": True,
        "daily": {"atr": 2.0, "technical_close": 40.0, "swing_signal": "HOLD"},
        "weekly": {"swing_signal": "HOLD"},
        "swing_strategy": {"max_swing_position_pct": 20, "loss_tier": "moderate_loss"},
        "combined_swing_signal": {
            "effective_signal": "HOLD",
            "primary_signal": "HOLD",
            "secondary_signal": "HOLD",
        },
        "pnl": {"market_price": 36.68},
        "data_quality": {"status": "ok", "issues": []},
    }
    plan = build_stock_trade_plan(
        stock,
        stock["swing_strategy"],
        stock["combined_swing_signal"],
        {"lot_size": 100},
        stock["pnl"],
    )
    assert plan["direction"] == "none"
    assert len(plan["watch_triggers"]) == 2
    sides = {item["side"] for item in plan["watch_triggers"]}
    assert sides == {"buy", "sell"}


def test_hold_watch_triggers_profitable_sell_only() -> None:
    stock = {
        "code": "HK.00700",
        "qty": 100,
        "can_sell_qty": 100,
        "lot_size": 100,
        "lot_confirmed": True,
        "daily": {"atr": 10.0, "technical_close": 440.0, "swing_signal": "HOLD"},
        "weekly": {"swing_signal": "HOLD"},
        "swing_strategy": {"max_swing_position_pct": 15, "loss_tier": "profitable"},
        "combined_swing_signal": {"effective_signal": "HOLD"},
        "pnl": {"market_price": 447.2},
        "data_quality": {"status": "ok", "issues": []},
    }
    plan = build_stock_trade_plan(
        stock,
        stock["swing_strategy"],
        stock["combined_swing_signal"],
        {"lot_size": 100},
        stock["pnl"],
    )
    assert len(plan["watch_triggers"]) == 1
    assert plan["watch_triggers"][0]["side"] == "sell"


def test_round_kline_cache_dedupes_without_disk_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kline_cache, "KLINE_CACHE_ENABLED", False)
    monkeypatch.setattr(kline_cache, "KLINE_ROUND_CACHE_TTL_SEC", 300)

    frame = pd.DataFrame([{"close": 123.0, "volume": 1}])
    calls = {"count": 0}

    class FakeQuote:
        def request_history_kline(self, *args, **kwargs):
            calls["count"] += 1
            return RET_OK, frame.copy(), None

    quote = FakeQuote()
    kline_cache.fetch_history_kline_cached(quote, "HK.800000", KLType.K_DAY, 30)  # type: ignore[arg-type]
    kline_cache.fetch_history_kline_cached(quote, "HK.800000", KLType.K_DAY, 30)  # type: ignore[arg-type]
    assert calls["count"] == 1


def test_round_kline_cache_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kline_cache, "KLINE_CACHE_ENABLED", False)
    monkeypatch.setattr(kline_cache, "KLINE_ROUND_CACHE_TTL_SEC", 1)

    frame = pd.DataFrame([{"close": 50.0, "volume": 1}])
    key = kline_cache.cache_key("HK.00700", KLType.K_DAY, 60)
    kline_cache._ROUND_MEMORY[key] = (time.time() - 5, frame.copy())

    assert kline_cache._get_round_cached_frame(key) is None
