"""自选配置与港股三槽调度单测。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from futu_ai_quant.config import watchlist as wl
from futu_ai_quant.market.watchlist_schedule import next_watchlist_slot
from futu_ai_quant.notify import pushplus


class TestWatchlistConfig:
    def test_load_from_json_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "codes.json"
        path.write_text(
            json.dumps({"codes": ["00700", "HK.09988", "HK.00700"]}),
            encoding="utf-8",
        )
        monkeypatch.delenv("WATCHLIST_CODES", raising=False)
        codes = wl.load_watchlist_codes(codes_file=path)
        assert codes == ["HK.00700", "HK.09988"]

    def test_cli_codes_override(self, tmp_path: Path) -> None:
        path = tmp_path / "codes.json"
        path.write_text(json.dumps({"codes": ["HK.00001"]}), encoding="utf-8")
        codes = wl.load_watchlist_codes(codes_arg="HK.00700,03690", codes_file=path)
        assert codes == ["HK.00700", "HK.03690"]

    def test_synthetic_stock(self) -> None:
        stock = wl.synthetic_watchlist_stock("HK.00700", assumed_pl_ratio=None)
        assert stock["qty"] == 0.0
        assert stock["cost_price"] is None
        assert stock["pl_ratio"] is None
        assert stock["position_direction"] == "自选观察"


class TestWatchlistSchedule:
    def test_weekday_next_slot_before_auction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WATCHLIST_SLOT_AUCTION", "09:00")
        monkeypatch.setenv("WATCHLIST_SLOT_LUNCH", "13:05")
        monkeypatch.setenv("WATCHLIST_SLOT_PRECLOSE", "15:30")
        # 2026-08-03 是周一
        now = datetime(2026, 8, 3, 8, 30, 0)
        key, fire_at, label = next_watchlist_slot(now)
        assert key == "auction"
        assert label == "盘前竞价"
        assert fire_at == datetime(2026, 8, 3, 9, 0, 0)

    def test_after_auction_goes_lunch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WATCHLIST_SLOT_AUCTION", "09:00")
        monkeypatch.setenv("WATCHLIST_SLOT_LUNCH", "13:05")
        monkeypatch.setenv("WATCHLIST_SLOT_PRECLOSE", "15:30")
        now = datetime(2026, 8, 3, 9, 1, 0)
        key, fire_at, label = next_watchlist_slot(now)
        assert key == "lunch"
        assert label == "午后开盘"
        assert fire_at == datetime(2026, 8, 3, 13, 5, 0)

    def test_after_lunch_goes_preclose(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WATCHLIST_SLOT_AUCTION", "09:00")
        monkeypatch.setenv("WATCHLIST_SLOT_LUNCH", "13:05")
        monkeypatch.setenv("WATCHLIST_SLOT_PRECLOSE", "15:30")
        now = datetime(2026, 8, 3, 13, 6, 0)
        key, fire_at, label = next_watchlist_slot(now)
        assert key == "preclose"
        assert label == "收盘前半小时"
        assert fire_at == datetime(2026, 8, 3, 15, 30, 0)

    def test_weekend_jumps_to_monday(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WATCHLIST_SLOT_AUCTION", "09:00")
        monkeypatch.setenv("WATCHLIST_SLOT_LUNCH", "13:05")
        monkeypatch.setenv("WATCHLIST_SLOT_PRECLOSE", "15:30")
        # 2026-08-01 周六
        now = datetime(2026, 8, 1, 10, 0, 0)
        key, fire_at, _ = next_watchlist_slot(now)
        assert key == "auction"
        assert fire_at.weekday() == 0
        assert fire_at == datetime(2026, 8, 3, 9, 0, 0)

    def test_skips_holiday_via_trading_days(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WATCHLIST_SLOT_AUCTION", "09:00")
        monkeypatch.setenv("WATCHLIST_SLOT_LUNCH", "13:05")
        monkeypatch.setenv("WATCHLIST_SLOT_PRECLOSE", "15:30")
        # 假设周一是假期，周二才有交易日
        trading_days = {
            "2026-08-04": {"time": "2026-08-04", "trade_date_type": "WHOLE"},
        }
        now = datetime(2026, 8, 3, 8, 0, 0)  # 周一假期
        key, fire_at, _ = next_watchlist_slot(now, trading_days=trading_days)
        assert key == "auction"
        assert fire_at == datetime(2026, 8, 4, 9, 0, 0)

    def test_half_day_skips_afternoon_slots(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from futu_ai_quant.market.watchlist_schedule import (
            should_run_watchlist_slot,
            trade_date_type_allows_slot,
        )

        monkeypatch.setenv("WATCHLIST_SLOT_AUCTION", "09:00")
        monkeypatch.setenv("WATCHLIST_SLOT_LUNCH", "13:05")
        monkeypatch.setenv("WATCHLIST_SLOT_PRECLOSE", "15:30")
        assert trade_date_type_allows_slot("auction", "MORNING") is True
        assert trade_date_type_allows_slot("lunch", "MORNING") is False
        assert trade_date_type_allows_slot("preclose", "MORNING") is False

        trading_days = {
            "2025-12-24": {"time": "2025-12-24", "trade_date_type": "MORNING"},
            "2025-12-26": {"time": "2025-12-26", "trade_date_type": "WHOLE"},
        }
        now = datetime(2025, 12, 24, 10, 0, 0)  # 半日市上午已过盘前
        key, fire_at, _ = next_watchlist_slot(now, trading_days=trading_days)
        assert key == "auction"
        assert fire_at == datetime(2025, 12, 26, 9, 0, 0)

        ok, reason = should_run_watchlist_slot(
            "preclose",
            trading_days=trading_days,
            day=datetime(2025, 12, 24),
        )
        assert ok is False
        assert "半日市" in reason


class TestPushPlusWatchlistNotify:
    def test_analyze_forces_empty_topic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PUSHPLUS_ENABLED", "1")
        monkeypatch.setenv("PUSHPLUS_TOKEN", "tok")
        monkeypatch.setenv("PUSHPLUS_TOPIC", "group_should_not_apply")

        called: dict = {}

        def _fake_send(title, content, **kwargs):
            called["kwargs"] = kwargs
            return True, "ok"

        monkeypatch.setattr(pushplus, "send_pushplus", _fake_send)
        ok, _ = pushplus.notify_analyze_decision(
            {
                "decision": {"portfolio_risk_summary": "x", "recommendations": []},
                "decision_source": "rules",
            }
        )
        assert ok is True
        assert called["kwargs"].get("topic") == ""

    def test_watchlist_uses_default_topic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PUSHPLUS_ENABLED", "1")
        monkeypatch.setenv("PUSHPLUS_TOKEN", "tok")
        monkeypatch.setenv("PUSHPLUS_TOPIC", "lucky_seven_bros")

        called: dict = {}

        def _fake_send(title, content, **kwargs):
            called["title"] = title
            called["kwargs"] = kwargs
            return True, "ok"

        monkeypatch.setattr(pushplus, "send_pushplus", _fake_send)
        ok, _ = pushplus.notify_watchlist_decision(
            {
                "decision": {"portfolio_risk_summary": "x", "recommendations": []},
                "decision_source": "rules",
                "slot_label": "盘前竞价",
            }
        )
        assert ok is True
        assert "自选分析" in called["title"]
        assert "盘前竞价" in called["title"]
        assert "topic" not in called["kwargs"]  # 走 send_pushplus 默认 env topic
