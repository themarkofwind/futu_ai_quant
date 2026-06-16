"""宏观事件日历（FOMC 等），本地 JSON 维护。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from futu_ai_quant.config.settings import MACRO_CALENDAR_PATH

_CALENDAR_CACHE: list[dict[str, Any]] | None = None
_CALENDAR_MTIME: float | None = None


def _parse_date(value: str) -> str | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        return None


def _normalize_event(raw: dict[str, Any]) -> dict[str, Any] | None:
    event_date = _parse_date(str(raw.get("date", "")))
    if event_date is None:
        return None
    event_type = str(raw.get("event_type") or raw.get("type") or "fed_meeting").lower()
    return {
        "date": event_date,
        "event_type": event_type,
        "label": str(raw.get("label") or raw.get("title") or event_type),
    }


def load_macro_calendar(path: Path | None = None) -> list[dict[str, Any]]:
    """读取宏观事件列表；文件不存在时返回空列表。"""
    global _CALENDAR_CACHE, _CALENDAR_MTIME

    calendar_path = path or MACRO_CALENDAR_PATH
    if not calendar_path.exists():
        return []

    mtime = calendar_path.stat().st_mtime
    if _CALENDAR_CACHE is not None and _CALENDAR_MTIME == mtime:
        return _CALENDAR_CACHE

    try:
        raw = json.loads(calendar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if isinstance(raw, list):
        items = raw
    else:
        items = raw.get("macro_events") or []

    events: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        event = _normalize_event(item)
        if event:
            events.append(event)

    _CALENDAR_CACHE = events
    _CALENDAR_MTIME = mtime
    return events


def clear_macro_calendar_cache() -> None:
    global _CALENDAR_CACHE, _CALENDAR_MTIME
    _CALENDAR_CACHE = None
    _CALENDAR_MTIME = None
