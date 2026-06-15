"""
波动率 + 相关性动态波段仓位上限。

借鉴 ai-hedge-fund risk_manager，适配 futu_ai_quant 的「持仓比例波段」模型：
在分层 ``max_swing_position_pct`` 基础上，按单票波动与组合相关性进一步收紧。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from futu_ai_quant.config.settings import PORTFOLIO_MAX_SINGLE_WEIGHT_PCT
from futu_ai_quant.utils.numbers import safe_float

_VOL_LOOKBACK_DAYS = 60


def calculate_volatility_metrics(closes: list[float], lookback_days: int = _VOL_LOOKBACK_DAYS) -> dict[str, Any]:
    """从收盘价序列计算波动率指标。"""
    if len(closes) < 2:
        return {
            "daily_volatility": 0.05,
            "annualized_volatility": 0.05 * np.sqrt(252),
            "volatility_percentile": 100.0,
            "data_points": len(closes),
        }

    series = pd.Series(closes, dtype=float)
    daily_returns = series.pct_change().dropna()
    if len(daily_returns) < 2:
        return {
            "daily_volatility": 0.05,
            "annualized_volatility": 0.05 * np.sqrt(252),
            "volatility_percentile": 100.0,
            "data_points": len(daily_returns),
        }

    recent = daily_returns.tail(min(lookback_days, len(daily_returns)))
    daily_vol = float(recent.std())
    annualized_vol = daily_vol * np.sqrt(252)

    if len(daily_returns) >= 30:
        rolling_vol = daily_returns.rolling(window=30).std().dropna()
        vol_percentile = float((rolling_vol <= daily_vol).mean() * 100) if len(rolling_vol) else 50.0
    else:
        vol_percentile = 50.0

    return {
        "daily_volatility": daily_vol if not np.isnan(daily_vol) else 0.025,
        "annualized_volatility": annualized_vol if not np.isnan(annualized_vol) else 0.25,
        "volatility_percentile": vol_percentile,
        "data_points": len(recent),
    }


def volatility_limit_factor(annualized_volatility: float) -> float:
    """
    相对基准 20% 仓位的波动率乘数（0.25–1.25）。
    低波放大、高波缩小。
    """
    base_limit = 0.20
    if annualized_volatility < 0.15:
        vol_multiplier = 1.25
    elif annualized_volatility < 0.30:
        vol_multiplier = 1.0 - (annualized_volatility - 0.15) * 0.5
    elif annualized_volatility < 0.50:
        vol_multiplier = 0.75 - (annualized_volatility - 0.30) * 0.5
    else:
        vol_multiplier = 0.50
    vol_multiplier = max(0.25, min(1.25, vol_multiplier))
    return vol_multiplier / base_limit


def correlation_multiplier(avg_correlation: float | None) -> float:
    """持仓间平均相关性 → 仓位乘数。"""
    if avg_correlation is None:
        return 1.0
    if avg_correlation >= 0.80:
        return 0.70
    if avg_correlation >= 0.60:
        return 0.85
    if avg_correlation >= 0.40:
        return 1.00
    if avg_correlation >= 0.20:
        return 1.05
    return 1.10


def _build_returns_matrix(stocks: list[dict[str, Any]]) -> pd.DataFrame | None:
    returns_by_code: dict[str, pd.Series] = {}
    for stock in stocks:
        closes = (stock.get("daily") or {}).get("close_history")
        if not closes or len(closes) < 5:
            continue
        rets = pd.Series(closes, dtype=float).pct_change().dropna()
        if len(rets) >= 3:
            returns_by_code[stock["code"]] = rets

    if len(returns_by_code) < 2:
        return None

    try:
        frame = pd.DataFrame(returns_by_code).dropna(how="any")
        if frame.shape[1] >= 2 and frame.shape[0] >= 5:
            return frame.corr()
    except Exception:
        return None
    return None


def _avg_correlation_with_peers(
    code: str,
    correlation_matrix: pd.DataFrame | None,
    peer_codes: list[str],
) -> float | None:
    if correlation_matrix is None or code not in correlation_matrix.columns:
        return None
    peers = [c for c in peer_codes if c in correlation_matrix.columns and c != code]
    if not peers:
        return None
    series = correlation_matrix.loc[code, peers].dropna()
    if series.empty:
        return None
    return float(series.mean())


def adjust_swing_max_pct(
    tier_max_pct: float,
    *,
    annualized_vol: float,
    avg_correlation: float | None,
    weight_pct: float,
    min_floor_pct: float = 5.0,
) -> tuple[float, dict[str, Any]]:
    """在分层上限内，按波动率/相关性/集中度收紧波段仓位比例。"""
    vol_factor = volatility_limit_factor(annualized_vol)
    corr_mult = correlation_multiplier(avg_correlation)
    conc_mult = 0.70 if weight_pct > PORTFOLIO_MAX_SINGLE_WEIGHT_PCT * 0.8 else 1.0

    raw = tier_max_pct * vol_factor * corr_mult * conc_mult
    adjusted = max(min_floor_pct, min(tier_max_pct, round(raw, 2)))

    reasoning = {
        "tier_max_pct": tier_max_pct,
        "vol_factor": round(vol_factor, 4),
        "correlation_multiplier": corr_mult,
        "concentration_multiplier": conc_mult,
        "adjusted_max_swing_pct": adjusted,
    }
    return adjusted, reasoning


def attach_portfolio_risk_limits(stocks: list[dict[str, Any]]) -> dict[str, Any]:
    """
    为每只正股写入 ``risk_limits``，并返回组合级 ``dynamic_risk`` 摘要。
    须在 ``compute_stock_indicators`` 之后、重建 ``stock_trade_plan`` 之前调用。
    """
    total_mv = sum(safe_float(s.get("market_val")) or 0.0 for s in stocks)
    correlation_matrix = _build_returns_matrix(stocks)
    peer_codes = [s["code"] for s in stocks]

    per_stock: list[dict[str, Any]] = []
    for stock in stocks:
        code = stock["code"]
        tier_max = float((stock.get("swing_strategy") or {}).get("max_swing_position_pct") or 10)
        market_val = safe_float(stock.get("market_val")) or 0.0
        weight_pct = round(market_val / total_mv * 100, 2) if total_mv > 0 else 0.0

        closes = (stock.get("daily") or {}).get("close_history") or []
        vol_metrics = calculate_volatility_metrics(closes)
        avg_corr = _avg_correlation_with_peers(code, correlation_matrix, peer_codes)

        adjusted_pct, reasoning = adjust_swing_max_pct(
            tier_max,
            annualized_vol=vol_metrics["annualized_volatility"],
            avg_correlation=avg_corr,
            weight_pct=weight_pct,
        )

        risk_limits = {
            "tier_max_swing_pct": tier_max,
            "adjusted_max_swing_pct": adjusted_pct,
            "weight_pct": weight_pct,
            "volatility_metrics": vol_metrics,
            "avg_correlation_with_peers": round(avg_corr, 4) if avg_corr is not None else None,
            "reasoning": reasoning,
        }
        stock["risk_limits"] = risk_limits
        per_stock.append({"code": code, **risk_limits})

    return {
        "correlation_matrix_available": correlation_matrix is not None,
        "stock_count": len(stocks),
        "per_stock": per_stock,
    }
