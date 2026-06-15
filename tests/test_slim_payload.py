from __future__ import annotations

import json
from pathlib import Path

from futu_ai_quant.analysis.portfolio import collect_required_codes
from futu_ai_quant.analysis.slim import slim_portfolio_for_ai, slim_stock_for_ai


def _sample_stock() -> dict:
    return {
        "code": "HK.09988",
        "name": "BABA-W",
        "qty": 5000.0,
        "can_sell_qty": 5000.0,
        "cost_price": 163.187,
        "nominal_price": 109.3,
        "market_val": 546500.0,
        "pl_ratio": -33.02,
        "position_direction": "买入持仓",
        "lot_size": 100,
        "shares_per_lot": 100,
        "pnl": {
            "nominal_price": 109.3,
            "market_price": 109.3,
            "cost_price": 163.187,
            "pl_ratio": -33.02,
            "pl_val": -269435.0,
            "today_pl_val": -4500.0,
            "cost_gap_pct": 33.02,
            "today_change_pct": -0.82,
            "prev_close_price": 110.2,
        },
        "swing_strategy": {
            "loss_tier": "moderate_loss",
            "primary_timeframe": "daily",
            "prefer_sell_call": True,
        },
        "daily": {
            "timeframe": "daily",
            "technical_close": 136.27,
            "rsi": 65.58606517527362,
            "boll_upper": 137.85,
            "boll_position": "near_upper",
            "macd_line": 4.59,
            "macd_signal": 3.21,
            "macd_hist": 1.38,
            "macd_bias": "bullish",
            "atr": 5.141353758510822,
            "volume": 147299900.0,
            "volume_ma": 124249168.4,
            "volume_ratio_raw": 1.19,
            "volume_ratio": 1.19,
            "volume_confirmed": False,
            "swing_signal": "HOLD",
        },
        "weekly": {
            "timeframe": "weekly",
            "swing_signal": "HOLD",
            "macd_bias": "bearish",
            "boll_position": "near_lower",
            "volume_confirmed": True,
            "volume_ratio": 1.27,
            "atr": 11.95,
            "rsi": 36.79,
        },
        "combined_swing_signal": {
            "primary_timeframe": "daily",
            "primary_signal": "HOLD",
            "secondary_signal": "HOLD",
            "effective_signal": "HOLD",
        },
        "stock_trade_plan": {
            "current_qty": 5000,
            "can_sell_qty": 5000,
            "lot_size": 100,
            "current_lots": 50,
            "direction": "none",
            "suggested_qty": 0,
            "suggested_lots": 0,
            "trigger_price_low": None,
            "trigger_price_high": None,
            "atr_used": 4.1237,
        },
        "option_trade_plan": {
            "action": "sell_call",
            "contract_code": "HK.ALB260629C117500",
            "expire_date": "2026-06-29",
            "strike_price": 117.5,
            "contracts": 10,
            "contract_size": 500,
            "premium_per_share": 0.88,
            "iv_rank": 50.0,
            "iv_rank_note": "历史IV Rank=50.0（IV中等）",
            "label": "卖出 10 张 Call",
        },
        "option_overlay": {
            "sell_call_candidates": [{"code": "HK.ALB260629C117500", "theta": -0.08}],
            "sell_put_candidates": [{"code": "HK.ALB260629P102500"}],
            "scan_note": None,
        },
        "trade_history": {
            "lookback_year": 2026,
            "recent_swing_days": 14,
            "ytd_summary": {
                "trade_count": 14,
                "buy_count": 10,
                "sell_count": 4,
                "last_trade": {"time": "2026-06-08", "side": "BUY"},
            },
            "recent_swing_window": {
                "stock_trades": [{"time": "2026-06-08", "side": "BUY", "qty": 300}],
                "option_trades": [],
                "stock_trade_count": 1,
                "option_trade_count": 0,
            },
            "swing_hint": "近14日正股成交1笔",
        },
        "existing_option_positions": [],
        "indicator_error": None,
    }


class TestSlimPortfolio:
    def test_removes_redundant_stock_fields(self) -> None:
        slim = slim_stock_for_ai(_sample_stock())
        assert "qty" not in slim
        assert "market_val" not in slim
        assert "cost_price" not in slim
        assert slim["pnl"]["market_price"] == 109.3
        assert "nominal_price" not in slim["pnl"]
        assert "macd_line" not in (slim.get("daily") or {})
        assert slim["daily"]["macd_bias"] == "bullish"
        assert slim["stock_trade_plan"]["lot_size"] == 100
        assert "current_qty" not in slim["stock_trade_plan"]
        assert slim["option_trade_plan"]["contract_code"] == "HK.ALB260629C117500"
        assert "label" not in slim["option_trade_plan"]
        assert slim.get("option_overlay") is None

    def test_preserves_decision_critical_fields(self) -> None:
        slim = slim_stock_for_ai(_sample_stock())
        assert slim["combined_swing_signal"]["effective_signal"] == "HOLD"
        assert slim["swing_strategy"]["loss_tier"] == "moderate_loss"
        assert slim["trade_history"]["swing_hint"] == "近14日正股成交1笔"
        assert slim["trade_history"]["recent_swing_window"]["stock_trade_count"] == 1

    def test_slim_portfolio_smaller_than_full(self) -> None:
        full = {
            "as_of": "2026-06-15",
            "market": "HK",
            "stocks": [_sample_stock()],
            "options": [],
            "required_positions": [{"code": "HK.09988", "asset_type": "stock"}],
            "summary": {"stock_count": 1},
            "portfolio_risk": {
                "total_stock_market_val": 100.0,
                "max_single_weight_pct": 40,
                "position_weights": [{"code": "HK.09988", "weight_pct": 100}],
                "concentration_alerts": [],
            },
        }
        slim = slim_portfolio_for_ai(full)
        full_size = len(json.dumps(full, ensure_ascii=False))
        slim_size = len(json.dumps(slim, ensure_ascii=False, separators=(",", ":")))
        assert slim_size < full_size * 0.7
        assert "position_weights" not in slim["portfolio_risk"]
        assert collect_required_codes(slim) == ["HK.09988"]

    def test_real_payload_slim_ratio(self) -> None:
        path = Path(__file__).resolve().parents[1] / "data/payloads/latest_payload.json"
        if not path.exists():
            return
        record = json.loads(path.read_text(encoding="utf-8"))
        full = record["portfolio_payload"]
        slim = slim_portfolio_for_ai(full)
        full_size = len(json.dumps(full, ensure_ascii=False))
        slim_size = len(json.dumps(slim, ensure_ascii=False, separators=(",", ":")))
        assert slim_size < full_size * 0.65
        assert len(slim["stocks"]) == len(full["stocks"])

    def test_overlay_scan_note_when_no_plan(self) -> None:
        stock = _sample_stock()
        stock["option_trade_plan"] = None
        stock["option_overlay"] = {
            "sell_call_candidates": [],
            "sell_put_candidates": [],
            "scan_note": "到期日查询失败: Empty DataFrame",
        }
        slim = slim_stock_for_ai(stock)
        assert slim["option_overlay"] == {"scan_note": "到期日查询失败: Empty DataFrame"}
