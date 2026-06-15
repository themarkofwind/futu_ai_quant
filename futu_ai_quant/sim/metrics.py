"""
模拟交易绩效指标：Sharpe、Sortino、最大回撤、胜率等。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from futu_ai_quant.sim.settings import SNAPSHOTS_FILE

_RISK_FREE_ANNUAL = 0.03


def _load_nav_series(snapshots_path: Path | None = None) -> pd.Series:
    path = snapshots_path or SNAPSHOTS_FILE
    if not path.exists():
        return pd.Series(dtype=float)

    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if not rows:
        return pd.Series(dtype=float)

    frame = pd.DataFrame(rows)
    if "timestamp" not in frame.columns or "total_nav" not in frame.columns:
        return pd.Series(dtype=float)

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce")
    frame = frame.dropna(subset=["timestamp", "total_nav"]).sort_values("timestamp")
    if frame.empty:
        return pd.Series(dtype=float)

    nav = frame.set_index("timestamp")["total_nav"].astype(float)
    nav = nav[~nav.index.duplicated(keep="last")]
    return nav


def compute_risk_metrics(
    nav_series: pd.Series,
    *,
    risk_free_annual: float = _RISK_FREE_ANNUAL,
) -> dict[str, Any]:
    """从净值序列计算风险调整后绩效指标。"""
    empty: dict[str, Any] = {
        "sharpe_ratio": None,
        "sortino_ratio": None,
        "max_drawdown_pct": None,
        "max_drawdown_date": None,
        "win_rate_pct": None,
        "profit_factor": None,
        "avg_daily_return_pct": None,
        "volatility_annual_pct": None,
        "observation_days": 0,
    }
    if nav_series is None or len(nav_series) < 2:
        return empty

    daily_nav = nav_series.resample("D").last().dropna()
    if len(daily_nav) < 2:
        daily_returns = nav_series.pct_change().dropna()
    else:
        daily_returns = daily_nav.pct_change().dropna()

    if daily_returns.empty:
        return empty

    daily_rf = (1 + risk_free_annual) ** (1 / 252) - 1
    excess = daily_returns - daily_rf
    mean_excess = float(excess.mean())
    std_excess = float(excess.std())

    sharpe = None
    if std_excess > 1e-12:
        sharpe = round(float(np.sqrt(252) * mean_excess / std_excess), 4)

    sortino = None
    downside = excess[excess < 0]
    if len(downside) > 0:
        downside_std = float(downside.std())
        if downside_std > 1e-12:
            sortino = round(float(np.sqrt(252) * mean_excess / downside_std), 4)

    rolling_max = nav_series.cummax()
    drawdown = (nav_series - rolling_max) / rolling_max
    max_dd = float(drawdown.min()) if not drawdown.empty else 0.0
    max_dd_date = None
    if not drawdown.empty and pd.notnull(drawdown.idxmin()):
        idx = drawdown.idxmin()
        max_dd_date = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)

    wins = daily_returns[daily_returns > 0]
    losses = daily_returns[daily_returns < 0]
    win_rate = round(len(wins) / len(daily_returns) * 100, 2) if len(daily_returns) else None

    profit_factor = None
    gross_profit = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(abs(losses.sum())) if len(losses) else 0.0
    if gross_loss > 1e-12:
        profit_factor = round(gross_profit / gross_loss, 4)
    elif gross_profit > 0:
        profit_factor = None

    vol_annual = round(float(daily_returns.std() * np.sqrt(252) * 100), 4)

    return {
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "max_drawdown_pct": round(abs(max_dd) * 100, 4) if max_dd is not None else None,
        "max_drawdown_date": max_dd_date,
        "win_rate_pct": win_rate,
        "profit_factor": profit_factor,
        "avg_daily_return_pct": round(float(daily_returns.mean() * 100), 4),
        "volatility_annual_pct": vol_annual,
        "observation_days": int(len(daily_returns)),
    }


def compute_metrics_from_snapshots(snapshots_path: Path | None = None) -> dict[str, Any]:
    nav = _load_nav_series(snapshots_path)
    return compute_risk_metrics(nav)
