"""日内做 T 规则参数解析（全局默认 + 分标的覆盖）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, replace
from typing import Any

from futu_ai_quant.strategy import intraday_t_settings as its


@dataclass(frozen=True)
class IntradayTRuleParams:
    """单次评估使用的规则阈值（可由分标的 JSON 覆盖）。"""

    rsi_sell: float = 75.0
    rsi_buy: float = 35.0
    vwap_premium: float = 1.015
    vwap_discount: float = 0.985
    stop_loss_mult: float = 1.5
    skip_open_min: float = 15.0
    skip_close_min: float = 20.0
    entry_confirm: bool = True
    spread_boll_ratio: float | None = None  # None=跟随全局 INTRADAY_T_SPREAD_BOLL_RATIO


_JSON_KEY_MAP = {
    "rsi_sell": "rsi_sell",
    "rsi_buy": "rsi_buy",
    "vwap_premium": "vwap_premium",
    "vwap_discount": "vwap_discount",
    "stop_loss_mult": "stop_loss_mult",
    "skip_open_min": "skip_open_min",
    "skip_close_min": "skip_close_min",
    "entry_confirm": "entry_confirm",
    "spread_boll_ratio": "spread_boll_ratio",
}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() not in ("0", "false", "no", "")


def parse_code_params_json(raw: str | None = None) -> dict[str, dict[str, Any]]:
    """解析 ``INTRADAY_T_CODE_PARAMS`` JSON：``{"HK.01347": {"rsi_sell": 78, ...}}``。"""
    text = (raw if raw is not None else its.INTRADAY_T_CODE_PARAMS).strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for code, overrides in data.items():
        if not isinstance(overrides, dict):
            continue
        key = str(code).strip().upper()
        cleaned: dict[str, Any] = {}
        for k, v in overrides.items():
            mapped = _JSON_KEY_MAP.get(str(k).strip().lower())
            if mapped is None:
                continue
            cleaned[mapped] = v
        if cleaned:
            out[key] = cleaned
    return out


def base_rule_params() -> IntradayTRuleParams:
    return IntradayTRuleParams(
        rsi_sell=float(its.INTRADAY_T_RSI_SELL),
        rsi_buy=float(its.INTRADAY_T_RSI_BUY),
        vwap_premium=float(its.INTRADAY_T_VWAP_PREMIUM),
        vwap_discount=float(its.INTRADAY_T_VWAP_DISCOUNT),
        stop_loss_mult=float(its.INTRADAY_T_STOP_LOSS_MULT),
        skip_open_min=float(its.INTRADAY_T_SKIP_OPEN_MIN),
        skip_close_min=float(its.INTRADAY_T_SKIP_CLOSE_MIN),
        entry_confirm=bool(its.INTRADAY_T_ENTRY_CONFIRM),
        spread_boll_ratio=None,
    )


def resolve_rule_params(
    code: str | None = None,
    *,
    overrides: dict[str, Any] | None = None,
) -> IntradayTRuleParams:
    """合并全局设置、分标的 JSON 与临时 overrides。"""
    params = base_rule_params()
    merged: dict[str, Any] = {}
    if code:
        merged.update(parse_code_params_json().get(code.strip().upper(), {}))
    if overrides:
        for k, v in overrides.items():
            mapped = _JSON_KEY_MAP.get(str(k).strip().lower(), str(k).strip().lower())
            if mapped in _JSON_KEY_MAP.values():
                merged[mapped] = v

    if not merged:
        return params

    kwargs: dict[str, Any] = {}
    for f in fields(IntradayTRuleParams):
        if f.name not in merged:
            continue
        raw = merged[f.name]
        if f.name == "entry_confirm":
            kwargs[f.name] = _as_bool(raw)
        elif f.name == "spread_boll_ratio":
            kwargs[f.name] = None if raw is None else float(raw)
        else:
            kwargs[f.name] = float(raw)
    return replace(params, **kwargs)
