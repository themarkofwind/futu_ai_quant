from __future__ import annotations

from typing import Any

from futu_ai_quant.config.settings import (
    DEEP_LOSS_THRESHOLD,
    MODERATE_LOSS_THRESHOLD,
)


def classify_loss_tier(pl_ratio: float | None) -> str:
    if pl_ratio is None:
        return "unknown"
    if pl_ratio < DEEP_LOSS_THRESHOLD:
        return "deep_loss"
    if pl_ratio < MODERATE_LOSS_THRESHOLD:
        return "moderate_loss"
    return "profitable"


def build_swing_strategy_profile(pl_ratio: float | None) -> dict[str, Any]:
    tier = classify_loss_tier(pl_ratio)
    profiles = {
        "deep_loss": {
            "loss_tier": tier,
            "primary_timeframe": "weekly",
            "secondary_timeframe": "daily",
            "guidance": "周K定方向为主，日K仅小仓位波段降本，避免频繁交易",
            "max_swing_position_pct": 10,
            "prefer_sell_call": True,
            "allow_sell_put": False,
        },
        "moderate_loss": {
            "loss_tier": tier,
            "primary_timeframe": "daily",
            "secondary_timeframe": "weekly",
            "guidance": "日K波段降本为主，周K确认趋势，可配合卖Call收权利金",
            "max_swing_position_pct": 20,
            "prefer_sell_call": True,
            "allow_sell_put": True,
        },
        "profitable": {
            "loss_tier": tier,
            "primary_timeframe": "weekly",
            "secondary_timeframe": "daily",
            "guidance": "周K止盈为主，卖Call备兑增强收益，不必刻意降本",
            "max_swing_position_pct": 15,
            "prefer_sell_call": True,
            "allow_sell_put": False,
        },
        "unknown": {
            "loss_tier": tier,
            "primary_timeframe": "weekly",
            "secondary_timeframe": "daily",
            "guidance": "盈亏未知，保守操作，周K定方向",
            "max_swing_position_pct": 10,
            "prefer_sell_call": False,
            "allow_sell_put": False,
        },
    }
    return profiles.get(tier, profiles["unknown"])
