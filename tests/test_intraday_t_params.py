"""分标的规则参数与时段辅助测试。"""

from __future__ import annotations

from datetime import datetime

from futu_ai_quant.market.session import (
    minutes_since_continuous_open,
    minutes_until_day_close,
)
from futu_ai_quant.strategy.intraday_t_params import parse_code_params_json, resolve_rule_params


def test_minutes_since_hk_open() -> None:
    now = datetime(2026, 6, 16, 9, 45, 0)
    assert minutes_since_continuous_open("HK", now) == 15.0
    now_pm = datetime(2026, 6, 16, 13, 10, 0)
    assert minutes_since_continuous_open("HK", now_pm) == 10.0


def test_minutes_until_hk_close() -> None:
    now = datetime(2026, 6, 16, 15, 40, 0)
    assert minutes_until_day_close("HK", now) == 20.0


def test_parse_code_params_json() -> None:
    parsed = parse_code_params_json(
        '{"HK.01347": {"rsi_sell": 78, "rsi_buy": 32, "entry_confirm": 0}}'
    )
    assert parsed["HK.01347"]["rsi_sell"] == 78
    assert parsed["HK.01347"]["entry_confirm"] == 0


def test_resolve_rule_params_overrides(monkeypatch) -> None:
    import futu_ai_quant.strategy.intraday_t_settings as its

    monkeypatch.setattr(
        its,
        "INTRADAY_T_CODE_PARAMS",
        '{"HK.01347": {"rsi_sell": 78, "rsi_buy": 32, "entry_confirm": false}}',
    )
    params = resolve_rule_params("HK.01347")
    assert params.rsi_sell == 78.0
    assert params.rsi_buy == 32.0
    assert params.entry_confirm is False

    other = resolve_rule_params(
        "HK.00700",
        overrides={"rsi_sell": 72, "stop_loss_mult": 2.0},
    )
    assert other.rsi_sell == 72.0
    assert other.stop_loss_mult == 2.0
