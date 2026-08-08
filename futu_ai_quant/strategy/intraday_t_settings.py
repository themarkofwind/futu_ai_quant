"""日内 T+0 先卖后买监控策略参数（环境变量可覆盖，支持运行中热刷新）。"""

from __future__ import annotations

import os
from typing import Any

# 默认：主力华虹实时；轮询标的腾讯+阿里
_DEFAULT_CODE = "HK.01347"
_DEFAULT_CODES = "HK.09988,HK.00700"


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default).strip() or default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return float(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no")


# 模块级可变配置：启动时与热加载时由 refresh_from_environ() 写入
INTRADAY_T_CODE: str = _DEFAULT_CODE
INTRADAY_T_CODES: str = _DEFAULT_CODES
INTRADAY_T_LOT_SIZE: int = 1000
INTRADAY_T_LOT_PCT: float = 30.0
INTRADAY_T_TARGET_SPREAD: float = 1.2
INTRADAY_T_TARGET_SPREAD_AUTO: bool = True
# 开仓时按布林带宽抬高止盈：max(费用/配置下限, 带宽×比例)；0=关闭
INTRADAY_T_SPREAD_BOLL_RATIO: float = 0.45
INTRADAY_T_MIN_PROFIT_COST_RATIO: float = 2.0
INTRADAY_T_US_COMMISSION_PER_SHARE: float = 0.0049
INTRADAY_T_US_MIN_COMMISSION: float = 0.99
INTRADAY_T_US_PLATFORM_FEE: float = 0.0
INTRADAY_T_POLL_SEC: int = 60
INTRADAY_T_BOLL_LENGTH: int = 20
INTRADAY_T_BOLL_STD: float = 2.0
INTRADAY_T_RSI_LENGTH: int = 14
INTRADAY_T_RSI_SELL: float = 75.0
INTRADAY_T_RSI_BUY: float = 35.0
INTRADAY_T_VWAP_PREMIUM: float = 1.015
INTRADAY_T_VWAP_DISCOUNT: float = 0.985
INTRADAY_T_KLINE_WINDOW: int = 120
INTRADAY_T_HISTORY_BARS: int = 60
INTRADAY_T_STATUS_INTERVAL_SEC: int = 30
INTRADAY_T_EVAL_TICK_SEC: float = 2.0
INTRADAY_T_VOLUME_SURGE_RATIO: float = 2.5
INTRADAY_T_CONSECUTIVE_ABOVE_BAND: int = 3


_SETTING_SPECS: tuple[tuple[str, str, Any, Any], ...] = (
    # (attr, env_name, caster_kind, default)
    ("INTRADAY_T_CODE", "INTRADAY_T_CODE", "str", _DEFAULT_CODE),
    ("INTRADAY_T_CODES", "INTRADAY_T_CODES", "str", _DEFAULT_CODES),
    ("INTRADAY_T_LOT_SIZE", "INTRADAY_T_LOT_SIZE", "int", 1000),
    ("INTRADAY_T_LOT_PCT", "INTRADAY_T_LOT_PCT", "float", 30.0),
    ("INTRADAY_T_TARGET_SPREAD", "INTRADAY_T_TARGET_SPREAD", "float", 1.2),
    ("INTRADAY_T_TARGET_SPREAD_AUTO", "INTRADAY_T_TARGET_SPREAD_AUTO", "bool", True),
    ("INTRADAY_T_SPREAD_BOLL_RATIO", "INTRADAY_T_SPREAD_BOLL_RATIO", "float", 0.45),
    ("INTRADAY_T_MIN_PROFIT_COST_RATIO", "INTRADAY_T_MIN_PROFIT_COST_RATIO", "float", 2.0),
    ("INTRADAY_T_US_COMMISSION_PER_SHARE", "INTRADAY_T_US_COMMISSION_PER_SHARE", "float", 0.0049),
    ("INTRADAY_T_US_MIN_COMMISSION", "INTRADAY_T_US_MIN_COMMISSION", "float", 0.99),
    ("INTRADAY_T_US_PLATFORM_FEE", "INTRADAY_T_US_PLATFORM_FEE", "float", 0.0),
    ("INTRADAY_T_POLL_SEC", "INTRADAY_T_POLL_SEC", "int", 60),
    ("INTRADAY_T_BOLL_LENGTH", "INTRADAY_T_BOLL_LENGTH", "int", 20),
    ("INTRADAY_T_BOLL_STD", "INTRADAY_T_BOLL_STD", "float", 2.0),
    ("INTRADAY_T_RSI_LENGTH", "INTRADAY_T_RSI_LENGTH", "int", 14),
    ("INTRADAY_T_RSI_SELL", "INTRADAY_T_RSI_SELL", "float", 75.0),
    ("INTRADAY_T_RSI_BUY", "INTRADAY_T_RSI_BUY", "float", 35.0),
    ("INTRADAY_T_VWAP_PREMIUM", "INTRADAY_T_VWAP_PREMIUM", "float", 1.015),
    ("INTRADAY_T_VWAP_DISCOUNT", "INTRADAY_T_VWAP_DISCOUNT", "float", 0.985),
    ("INTRADAY_T_KLINE_WINDOW", "INTRADAY_T_KLINE_WINDOW", "int", 120),
    ("INTRADAY_T_HISTORY_BARS", "INTRADAY_T_HISTORY_BARS", "int", 60),
    ("INTRADAY_T_STATUS_INTERVAL_SEC", "INTRADAY_T_STATUS_INTERVAL_SEC", "int", 30),
    ("INTRADAY_T_EVAL_TICK_SEC", "INTRADAY_T_EVAL_TICK_SEC", "float", 2.0),
    ("INTRADAY_T_VOLUME_SURGE_RATIO", "INTRADAY_T_VOLUME_SURGE_RATIO", "float", 2.5),
    ("INTRADAY_T_CONSECUTIVE_ABOVE_BAND", "INTRADAY_T_CONSECUTIVE_ABOVE_BAND", "int", 3),
)


def refresh_from_environ() -> dict[str, tuple[Any, Any]]:
    """
    从当前 ``os.environ`` 重读全部做 T 参数，写回本模块全局变量。

    返回发生变更的键 -> (旧值, 新值)。调用方应在 ``load_dotenv(override=True)`` 之后调用。
    """
    g = globals()
    changes: dict[str, tuple[Any, Any]] = {}
    for attr, env_name, kind, default in _SETTING_SPECS:
        old = g[attr]
        if kind == "str":
            new = _env_str(env_name, default)
        elif kind == "int":
            new = _env_int(env_name, default)
        elif kind == "float":
            new = _env_float(env_name, default)
        else:
            new = _env_bool(env_name, bool(default))
        if new != old:
            changes[attr] = (old, new)
            g[attr] = new
    return changes


# 首次导入时按环境初始化（兼容已 export 的变量；.env 需在 CLI 里先 load 再 refresh）
refresh_from_environ()
