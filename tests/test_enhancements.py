"""新功能单元测试：风控、技术集成、分析师、绩效指标、信号回测。"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from futu_ai_quant.analysis.analysts import attach_analyst_signals, build_stock_analyst_signals
from futu_ai_quant.backtest.signals import run_signal_backtest_on_frame
from futu_ai_quant.indicators.ensemble import (
    compute_technical_ensemble,
    weighted_signal_combination,
)
from futu_ai_quant.indicators.technical import compute_indicators_from_frame
from futu_ai_quant.risk.position_limits import (
    adjust_swing_max_pct,
    attach_portfolio_risk_limits,
    calculate_volatility_metrics,
)
from futu_ai_quant.sim.metrics import compute_risk_metrics


def _make_ohlcv(rows: int = 120, *, trend: float = 0.001) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    prices = [100.0]
    for _ in range(rows - 1):
        prices.append(prices[-1] * (1 + trend + rng.normal(0, 0.01)))
    close = np.array(prices)
    high = close * 1.01
    low = close * 0.99
    volume = rng.integers(1_000_000, 2_000_000, size=rows)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def test_volatility_metrics_and_adjust_swing_max_pct():
    closes = list(100 + np.sin(np.linspace(0, 8, 80)) * 5)
    metrics = calculate_volatility_metrics(closes)
    assert metrics["annualized_volatility"] > 0

    adjusted, reasoning = adjust_swing_max_pct(
        20.0,
        annualized_vol=0.45,
        avg_correlation=0.75,
        weight_pct=25.0,
    )
    assert adjusted <= 20.0
    assert reasoning["correlation_multiplier"] == 0.85


def test_attach_portfolio_risk_limits_tightens_high_vol():
    frame = _make_ohlcv(90, trend=0.002)
    daily = compute_indicators_from_frame(frame, "daily")
    stocks = [
        {
            "code": "HK.09988",
            "name": "TEST",
            "market_val": 100000,
            "swing_strategy": {"max_swing_position_pct": 20, "loss_tier": "moderate_loss"},
            "daily": daily,
        },
        {
            "code": "HK.00700",
            "name": "TEST2",
            "market_val": 80000,
            "swing_strategy": {"max_swing_position_pct": 20, "loss_tier": "moderate_loss"},
            "daily": daily,
        },
    ]
    overlay = attach_portfolio_risk_limits(stocks)
    assert overlay["correlation_matrix_available"] is True
    for stock in stocks:
        limits = stock["risk_limits"]
        assert limits["adjusted_max_swing_pct"] <= limits["tier_max_swing_pct"]


def test_technical_ensemble_and_weighted_combination():
    frame = _make_ohlcv(130)
    ensemble = compute_technical_ensemble(frame)
    assert ensemble is not None
    assert ensemble["signal"] in ("bullish", "bearish", "neutral")
    assert "strategies" in ensemble

    combined = weighted_signal_combination(
        {
            "trend": {"signal": "bullish", "confidence": 0.8},
            "momentum": {"signal": "bearish", "confidence": 0.6},
        },
        {"trend": 0.5, "momentum": 0.5},
    )
    assert combined["signal"] in ("bullish", "bearish", "neutral")


def test_virtual_analyst_signals():
    stock = {
        "code": "HK.09988",
        "daily": {
            "technical_ensemble": {
                "signal": "bearish",
                "confidence": 70,
                "strategies": {"momentum": {"signal": "bearish", "confidence": 65}},
            },
            "macd_bias": "bearish",
        },
        "combined_swing_signal": {
            "effective_signal": "HOLD",
            "primary_signal": "HOLD",
            "secondary_signal": "WAIT",
        },
        "risk_limits": {
            "tier_max_swing_pct": 20,
            "adjusted_max_swing_pct": 12,
            "volatility_metrics": {"annualized_volatility": 0.35},
            "avg_correlation_with_peers": 0.7,
        },
    }
    signals = build_stock_analyst_signals(stock)
    assert signals["consensus"] in ("bullish", "bearish", "neutral")
    assert len(signals["analysts"]) == 4

    stocks = [stock]
    summary = attach_analyst_signals(stocks)
    assert stocks[0]["analyst_signals"]["code"] == "HK.09988"
    assert summary["stock_count"] == 1


def test_sim_risk_metrics_from_nav_series():
    nav = pd.Series(
        [1_000_000, 1_010_000, 1_005_000, 1_020_000, 1_015_000],
        index=pd.date_range("2026-01-01", periods=5, freq="D"),
    )
    metrics = compute_risk_metrics(nav)
    assert metrics["observation_days"] == 4
    assert metrics["max_drawdown_pct"] is not None


def test_signal_backtest_on_synthetic_frame():
    frame = _make_ohlcv(100, trend=0.0)
    result = run_signal_backtest_on_frame(frame, pl_ratio=-30.0, min_warmup=35)
    assert "signal_count" in result
    assert "stats" in result


def test_compute_indicators_includes_close_history_and_ensemble():
    frame = _make_ohlcv(80)
    daily = compute_indicators_from_frame(frame, "daily")
    assert len(daily.get("close_history") or []) > 0
    assert daily.get("technical_ensemble") is not None


def test_metrics_merge_in_save_snapshot_shape(tmp_path, monkeypatch):
    from futu_ai_quant.sim import io as sim_io

    snapshots = tmp_path / "snapshots.jsonl"
    metrics_file = tmp_path / "metrics.json"
    monkeypatch.setattr(sim_io, "SNAPSHOTS_FILE", snapshots)
    monkeypatch.setattr(sim_io, "METRICS_FILE", metrics_file)

    snapshots.write_text(
        "\n".join(
            json.dumps({"timestamp": f"2026-01-0{i}", "total_nav": 1_000_000 + i * 1000})
            for i in range(1, 6)
        ),
        encoding="utf-8",
    )

    class _Portfolio:
        data = {"stats": {"total_trades": 3}}

    sim_io.save_snapshot(
        _Portfolio(),
        {
            "total_nav": 1_004_000,
            "cash_hkd": 100_000,
            "total_unrealized_pnl": 4000,
            "realized_pnl": 0,
            "total_fees": 50,
            "pending_orders": 0,
        },
        "decision_test",
        {"executed": 1},
    )
    saved = json.loads(metrics_file.read_text(encoding="utf-8"))
    assert saved["latest_nav"] == 1_004_000
    assert "sharpe_ratio" in saved
