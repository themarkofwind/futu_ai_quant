"""LLM JSON 解析与自选精简 payload 单测。"""

from __future__ import annotations

import pytest

from futu_ai_quant.analysis.slim import slim_watchlist_for_ai
from futu_ai_quant.decision.ai import parse_llm_json_content


class TestParseLlmJson:
    def test_plain_object(self) -> None:
        data = parse_llm_json_content('{"portfolio_risk_summary":"ok","recommendations":[]}')
        assert data["portfolio_risk_summary"] == "ok"

    def test_code_fence(self) -> None:
        raw = '```json\n{"portfolio_risk_summary":"x","recommendations":[]}\n```'
        data = parse_llm_json_content(raw)
        assert data["portfolio_risk_summary"] == "x"

    def test_unterminated_raises(self) -> None:
        with pytest.raises(ValueError, match="JSON 解析失败"):
            parse_llm_json_content('{"reasoning": "未闭合')


class TestSlimWatchlist:
    def test_strips_heavy_fields(self) -> None:
        payload = {
            "as_of": "2026-08-01",
            "market": "HK",
            "required_positions": [{"code": "HK.00700", "asset_type": "stock"}],
            "summary": {"stock_count": 1},
            "portfolio_risk": {},
            "macro_risk": {"risk_level": "normal", "summary": "正常"},
            "stocks": [
                {
                    "code": "HK.00700",
                    "pnl": {"market_price": 100, "today_change_pct": 1.2},
                    "daily": {"swing_signal": "HOLD", "rsi": 50},
                    "weekly": {"swing_signal": "HOLD"},
                    "combined_swing_signal": {"effective_signal": "HOLD"},
                    "stock_trade_plan": {
                        "direction": "none",
                        "watch_triggers": [{"side": "buy", "price_low": 90, "price_high": 95}],
                        "lot_size": 100,
                    },
                    "trade_history": {"ytd_summary": {"trade_count": 9}},
                    "option_overlay": {"sell_call_candidates": [{"x": 1}]},
                    "option_trade_plan": {"action": "sell_call"},
                }
            ],
            "options": [],
        }
        slim = slim_watchlist_for_ai(payload)
        assert slim["analysis_mode"] == "watchlist"
        assert slim["required_codes"] == ["HK.00700"]
        stock = slim["stocks"][0]
        assert "trade_history" not in stock
        assert "option_overlay" not in stock
        assert stock["stock_trade_plan"]["watch_triggers"]
