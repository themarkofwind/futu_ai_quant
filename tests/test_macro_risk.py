"""宏观风险 overlay 单元测试。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from futu_ai_quant.risk.macro_calendar import clear_macro_calendar_cache, load_macro_calendar
from futu_ai_quant.risk.macro_overlay import (
    apply_macro_risk_to_stocks,
    evaluate_macro_risk,
)


@pytest.fixture
def calendar_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "macro_calendar.json"
    path.write_text(
        json.dumps(
            {
                "macro_events": [
                    {
                        "date": "2026-06-18",
                        "event_type": "fed_meeting",
                        "label": "FOMC",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("futu_ai_quant.risk.macro_calendar.MACRO_CALENDAR_PATH", path)
    clear_macro_calendar_cache()
    yield path
    clear_macro_calendar_cache()


def test_load_macro_calendar(calendar_file: Path) -> None:
    events = load_macro_calendar(calendar_file)
    assert len(events) == 1
    assert events[0]["event_type"] == "fed_meeting"


def test_macro_risk_elevated_tightens_limits() -> None:
    stocks = [
        {
            "code": "HK.09988",
            "risk_limits": {"tier_max_swing_pct": 20, "adjusted_max_swing_pct": 18},
        }
    ]
    macro = evaluate_macro_risk(
        hsi={
            "available": True,
            "code": "HK.800000",
            "return_5d_pct": -6.0,
            "today_change_pct": -1.0,
        },
        gold=None,
        macro_events=[],
        as_of=date(2026, 6, 16),
    )
    assert macro["risk_level"] == "elevated"
    assert macro["swing_pct_multiplier"] == 0.7
    apply_macro_risk_to_stocks(stocks, macro)
    assert stocks[0]["risk_limits"]["adjusted_max_swing_pct"] == pytest.approx(12.6)


def test_macro_fed_meeting_adds_trigger(calendar_file: Path) -> None:
    macro = evaluate_macro_risk(
        hsi={"available": True, "return_5d_pct": 1.0, "today_change_pct": 0.5},
        gold=None,
        macro_events=load_macro_calendar(calendar_file),
        as_of=date(2026, 6, 17),
    )
    assert macro["risk_level"] == "elevated"
    assert any("FOMC" in item for item in macro["triggers"])
