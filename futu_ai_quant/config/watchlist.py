"""自选股（watchlist）配置：标的列表与港股推送时段。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from futu_ai_quant.market.codes import normalize_stock_code, parse_stock_codes

DEFAULT_CODES_FILE = Path(os.getenv("WATCHLIST_CODES_FILE", "data/watchlist/codes.json"))
DEFAULT_DECISIONS_DIR = Path(os.getenv("WATCHLIST_DECISIONS_DIR", "data/watchlist/decisions"))
DEFAULT_PAYLOADS_DIR = Path(os.getenv("WATCHLIST_PAYLOADS_DIR", "data/watchlist/payloads"))

# 港股三槽默认（北京时间）
DEFAULT_SLOT_AUCTION = "09:00"
DEFAULT_SLOT_LUNCH = "13:05"
DEFAULT_SLOT_PRECLOSE = "15:45"

# 默认对午后/收盘前启用 30 分钟盘中周期
DEFAULT_INTRADAY_SLOTS = ("lunch", "preclose")
DEFAULT_INTRADAY_BAR = "30m"
DEFAULT_INTRADAY_COUNT = 80

SLOT_LABELS: dict[str, str] = {
    "auction": "盘前竞价",
    "lunch": "午后开盘",
    "preclose": "尾盘提醒",
    "manual": "手动",
}
_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() not in {"", "0", "false", "no", "off"}


def watchlist_use_ai() -> bool:
    """自选是否调用 LLM；由 ``WATCHLIST_USE_AI`` 控制（默认关闭，仅规则引擎）。"""
    return _env_bool("WATCHLIST_USE_AI", "0")


def watchlist_assumed_pl_ratio() -> float | None:
    """已废弃：自选固定走 watchlist 分层，不再用盈亏分层。保留仅为兼容旧 .env。"""
    raw = os.getenv("WATCHLIST_ASSUMED_PL_RATIO", "").strip()
    if not raw:
        return None
    return float(raw)


def parse_hhmm(text: str, *, fallback: str) -> tuple[int, int]:
    raw = (text or "").strip() or fallback
    hour_s, minute_s = raw.split(":", 1)
    hour, minute = int(hour_s), int(minute_s)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"非法时间: {raw}")
    return hour, minute


def load_watchlist_slots() -> list[tuple[str, int, int]]:
    """返回 [(slot_key, hour, minute), ...]，按日内时间排序。"""
    auction = parse_hhmm(os.getenv("WATCHLIST_SLOT_AUCTION", ""), fallback=DEFAULT_SLOT_AUCTION)
    lunch = parse_hhmm(os.getenv("WATCHLIST_SLOT_LUNCH", ""), fallback=DEFAULT_SLOT_LUNCH)
    preclose = parse_hhmm(
        os.getenv("WATCHLIST_SLOT_PRECLOSE", ""),
        fallback=DEFAULT_SLOT_PRECLOSE,
    )
    slots = [
        ("auction", auction[0], auction[1]),
        ("lunch", lunch[0], lunch[1]),
        ("preclose", preclose[0], preclose[1]),
    ]
    return sorted(slots, key=lambda x: x[1] * 60 + x[2])


def slot_label(slot_key: str) -> str:
    return SLOT_LABELS.get(slot_key, slot_key)


def slot_clock(slot_key: str | None) -> str | None:
    """返回配置中的槽位时钟 ``HH:MM``；未知/手动则 None。"""
    if not slot_key or slot_key == "manual":
        return None
    for key, hour, minute in load_watchlist_slots():
        if key == slot_key:
            return f"{hour:02d}:{minute:02d}"
    return None


def format_watchlist_notify_when(
    slot_key: str | None = None,
    slot_label_text: str | None = None,
    analyzed_at: datetime | str | None = None,
) -> str:
    """生成推送用的「哪天 · 哪个时段」文案，如 ``2026-08-02（周日）15:45 · 尾盘提醒``。"""
    if isinstance(analyzed_at, str) and analyzed_at.strip():
        try:
            analyzed_at = datetime.fromisoformat(analyzed_at.strip())
        except ValueError:
            analyzed_at = None
    now = analyzed_at if isinstance(analyzed_at, datetime) else datetime.now()
    weekday = _WEEKDAY_CN[now.weekday()]
    day = now.strftime("%Y-%m-%d")
    clock = slot_clock(slot_key) or now.strftime("%H:%M")
    label = (slot_label_text or "").strip() or (slot_label(slot_key) if slot_key else "手动")
    return f"{day}（{weekday}）{clock} · {label}"


def watchlist_intraday_slots() -> set[str]:
    raw = os.getenv("WATCHLIST_INTRADAY_SLOTS", ",".join(DEFAULT_INTRADAY_SLOTS)).strip()
    if not raw:
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def watchlist_slot_uses_intraday(slot_key: str | None) -> bool:
    if not slot_key:
        return False
    return slot_key in watchlist_intraday_slots()


def watchlist_intraday_bar() -> str:
    return (os.getenv("WATCHLIST_INTRADAY_BAR", DEFAULT_INTRADAY_BAR) or DEFAULT_INTRADAY_BAR).strip().lower()


def watchlist_intraday_count() -> int:
    return int(os.getenv("WATCHLIST_INTRADAY_COUNT", str(DEFAULT_INTRADAY_COUNT)))


def watchlist_intraday_kltype():
    """按配置返回 Futu KLType（默认 30 分钟）。"""
    from futu import KLType

    bar = watchlist_intraday_bar()
    mapping = {
        "15m": KLType.K_15M,
        "15": KLType.K_15M,
        "30m": KLType.K_30M,
        "30": KLType.K_30M,
        "60m": KLType.K_60M,
        "60": KLType.K_60M,
        "5m": KLType.K_5M,
        "5": KLType.K_5M,
    }
    return mapping.get(bar, KLType.K_30M)


def _codes_from_json_file(path: Path) -> list[str] | None:
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        raw_list = data
    elif isinstance(data, dict):
        raw_list = data.get("codes") or []
    else:
        raise ValueError(f"自选股配置格式无效: {path}")
    if not isinstance(raw_list, list):
        raise ValueError(f"codes 必须为数组: {path}")

    seen: set[str] = set()
    codes: list[str] = []
    for item in raw_list:
        if isinstance(item, dict):
            piece = str(item.get("code") or "").strip()
        else:
            piece = str(item).strip()
        if not piece:
            continue
        code = normalize_stock_code(piece)
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def load_watchlist_codes(
    *,
    codes_arg: str | None = None,
    codes_file: str | Path | None = None,
) -> list[str]:
    """
    解析自选股列表，优先级：
    1. CLI ``--codes``
    2. ``WATCHLIST_CODES_FILE`` / 默认 ``data/watchlist/codes.json``（文件存在时）
    3. 环境变量 ``WATCHLIST_CODES``
    """
    if codes_arg and codes_arg.strip():
        return parse_stock_codes(codes_arg)

    path = Path(codes_file) if codes_file else DEFAULT_CODES_FILE
    from_file = _codes_from_json_file(path)
    if from_file is not None:
        if not from_file:
            raise ValueError(f"自选股配置为空: {path}")
        return from_file

    env_codes = os.getenv("WATCHLIST_CODES", "").strip()
    if env_codes:
        return parse_stock_codes(env_codes)

    raise ValueError(
        "未配置自选股：请创建 data/watchlist/codes.json，"
        "或设置 WATCHLIST_CODES，或使用 --codes"
    )


def synthetic_watchlist_stock(
    code: str,
    *,
    assumed_pl_ratio: float | None = None,
    name: str = "",
) -> dict[str, Any]:
    """构造无个人持仓字段的观察用 stock dict。"""
    return {
        "code": code,
        "name": name,
        "qty": 0.0,
        "can_sell_qty": 0.0,
        "cost_price": None,
        "nominal_price": None,
        "market_val": 0.0,
        "pl_ratio": assumed_pl_ratio,
        "pl_val": None,
        "today_pl_val": None,
        "position_side": "LONG",
        "position_type": "WATCHLIST",
        "strategy_type": "N/A",
        "position_direction": "自选观察",
    }
