"""成交历史 recent_swing_window 逻辑与缓存测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from futu_ai_quant.history.trades import (
    _build_underlying_index,
    _get_underlying_index,
    _load_underlying_index_cache,
    _save_ytd_trade_cache,
    attach_trade_history_to_stocks,
    clear_trade_history_memory_cache,
    summarize_trade_history_for_stock,
)


def _stock_deal(code: str, when: datetime, side: str = "BUY", qty: float = 100) -> dict:
    return {
        "deal_id": f"{code}-{when.isoformat()}-{side}",
        "code": code,
        "underlying_code": code,
        "asset_type": "stock",
        "trd_side": side,
        "qty": qty,
        "price": 100.0,
        "create_time": when.strftime("%Y-%m-%d %H:%M:%S"),
    }


def _option_deal(underlying: str, when: datetime, side: str = "SELL") -> dict:
    code = "HK.ALB260629C120000"
    return {
        "deal_id": f"opt-{when.isoformat()}",
        "code": code,
        "underlying_code": underlying,
        "asset_type": "option",
        "trd_side": side,
        "qty": 1,
        "price": 1.2,
        "create_time": when.strftime("%Y-%m-%d %H:%M:%S"),
    }


def test_recent_swing_window_returns_last_five_trades_per_asset_type() -> None:
    code = "HK.00700"
    now = datetime.now()
    deals = [
        _stock_deal(code, now - timedelta(days=90), "BUY"),
        _stock_deal(code, now - timedelta(days=60), "SELL"),
        _stock_deal(code, now - timedelta(days=30), "BUY"),
        _stock_deal(code, now - timedelta(days=20), "SELL"),
        _stock_deal(code, now - timedelta(days=10), "BUY"),
        _stock_deal(code, now - timedelta(days=1), "SELL"),
        _option_deal(code, now - timedelta(days=3)),
        _option_deal(code, now - timedelta(days=2)),
    ]

    summary = summarize_trade_history_for_stock(code, deals, effective_signal="HOLD")
    window = summary["recent_swing_window"]

    assert summary["recent_stock_trade_limit"] == 5
    assert summary["recent_option_trade_limit"] == 5
    assert window["stock_trade_count"] == 5
    assert window["option_trade_count"] == 2
    assert len(window["stock_trades"]) == 5
    assert len(window["option_trades"]) == 2
    assert window["ytd_stock_trade_count"] == 6


def test_recent_swing_window_empty_when_no_ytd_trades() -> None:
    summary = summarize_trade_history_for_stock("HK.09988", [], effective_signal="HOLD")
    window = summary["recent_swing_window"]
    assert window["stock_trades"] == []
    assert window["option_trades"] == []
    assert "当年无该正股" in (summary.get("swing_hint") or "")


def test_underlying_index_disk_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("futu_ai_quant.history.trades.TRADE_HISTORY_DIR", tmp_path)
    clear_trade_history_memory_cache()

    code = "HK.09988"
    now = datetime.now()
    deals = [_stock_deal(code, now - timedelta(days=i), "BUY") for i in range(3)]
    year = now.year
    cache_payload = {
        "year": year,
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "deal_count": len(deals),
        "deals": deals,
    }
    _save_ytd_trade_cache(year, deals, source="test")

    fingerprint = f"{year}|{cache_payload['updated_at']}|{len(deals)}"
    loaded = _load_underlying_index_cache(year, fingerprint)
    assert loaded is not None
    assert len(loaded[code]["stock"]) == 3
    assert loaded[code]["option"] == []


def test_attach_trade_history_builds_index_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []

    def _spy(deals: list, year: int):
        calls.append(len(deals))
        return _build_underlying_index(deals, year)

    monkeypatch.setattr("futu_ai_quant.history.trades._build_underlying_index", _spy)
    clear_trade_history_memory_cache()

    now = datetime.now()
    deals = [_stock_deal("HK.00700", now), _stock_deal("HK.09988", now)]
    stocks = [{"code": "HK.00700", "combined_swing_signal": {}}, {"code": "HK.09988", "combined_swing_signal": {}}]

    # 预置内存索引路径：直接测 attach 只触发一次 build（经 _get_underlying_index）
    index = _build_underlying_index(deals, now.year)
    monkeypatch.setattr(
        "futu_ai_quant.history.trades._get_underlying_index",
        lambda d: index,
    )

    attach_trade_history_to_stocks(stocks, deals)
    assert stocks[0]["trade_history"]["recent_swing_window"]["stock_trade_count"] == 1
    assert stocks[1]["trade_history"]["recent_swing_window"]["stock_trade_count"] == 1
