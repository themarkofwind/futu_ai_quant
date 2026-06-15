"""
K 线短期缓存（默认关闭）：减少 OpenD ``request_history_kline`` 重复调用。

在 ``.env`` 中设置 ``KLINE_CACHE_ENABLED=1`` 后生效；
日K ``KLINE_CACHE_TTL_SEC``、周K ``KLINE_WEEKLY_CACHE_TTL_SEC`` 建议小于分析间隔。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
from futu import RET_OK, AuType, KLType, OpenQuoteContext

from futu_ai_quant.config.settings import (
    KLINE_CACHE_DIR,
    KLINE_CACHE_ENABLED,
    KLINE_CACHE_TTL_SEC,
    KLINE_WEEKLY_CACHE_TTL_SEC,
)
from futu_ai_quant.utils.logging import log
from futu_ai_quant.utils.retry import retry_call

_MEMORY: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _timeframe_name(ktype: KLType) -> str:
    return "daily" if ktype == KLType.K_DAY else "weekly"


def _caching_enabled_for(ktype: KLType) -> bool:
    if not KLINE_CACHE_ENABLED:
        return False
    if ktype == KLType.K_DAY:
        return KLINE_CACHE_TTL_SEC > 0
    return KLINE_WEEKLY_CACHE_TTL_SEC > 0


def _cache_ttl_sec(ktype: KLType) -> int:
    if ktype == KLType.K_WEEK:
        return KLINE_WEEKLY_CACHE_TTL_SEC
    return KLINE_CACHE_TTL_SEC


def cache_key(code: str, ktype: KLType, max_count: int) -> str:
    return f"{code}:{_timeframe_name(ktype)}:{max_count}"


def _disk_path(key: str) -> Path:
    safe = key.replace(".", "_").replace(":", "_")
    return KLINE_CACHE_DIR / f"{safe}.json"


def _is_fresh(fetched_at: float, ktype: KLType) -> bool:
    return (time.time() - fetched_at) < _cache_ttl_sec(ktype)


def _load_disk_entry(key: str, ktype: KLType) -> list[dict[str, Any]] | None:
    path = _disk_path(key)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        fetched_at = float(payload.get("fetched_at", 0))
        if not _is_fresh(fetched_at, ktype):
            return None
        rows = payload.get("rows")
        if isinstance(rows, list) and rows:
            _MEMORY[key] = (fetched_at, rows)
            return rows
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return None


def _save_disk_entry(key: str, fetched_at: float, rows: list[dict[str, Any]]) -> None:
    KLINE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _disk_path(key)
    path.write_text(
        json.dumps({"fetched_at": fetched_at, "rows": rows}, ensure_ascii=False),
        encoding="utf-8",
    )


def get_cached_kline(code: str, ktype: KLType, max_count: int) -> pd.DataFrame | None:
    if not _caching_enabled_for(ktype):
        return None
    key = cache_key(code, ktype, max_count)
    mem = _MEMORY.get(key)
    if mem and _is_fresh(mem[0], ktype):
        return pd.DataFrame(mem[1])

    rows = _load_disk_entry(key, ktype)
    if rows:
        log("K线缓存", f"命中磁盘 {code} {_timeframe_name(ktype)}")
        return pd.DataFrame(rows)
    return None


def put_cached_kline(
    code: str,
    ktype: KLType,
    max_count: int,
    frame: pd.DataFrame,
) -> None:
    if not _caching_enabled_for(ktype) or frame is None or frame.empty:
        return
    key = cache_key(code, ktype, max_count)
    fetched_at = time.time()
    rows = frame.to_dict(orient="records")
    _MEMORY[key] = (fetched_at, rows)
    _save_disk_entry(key, fetched_at, rows)


def clear_kline_cache() -> None:
    """测试或强制刷新时清空内存缓存。"""
    _MEMORY.clear()


def fetch_history_kline_cached(
    quote_ctx: OpenQuoteContext,
    code: str,
    ktype: KLType,
    max_count: int,
) -> tuple[int, pd.DataFrame | None, Any]:
    cached = get_cached_kline(code, ktype, max_count)
    if cached is not None and not cached.empty:
        log("K线缓存", f"命中内存 {code} {_timeframe_name(ktype)}")
        return RET_OK, cached, None

    ret, kline, page_req_key = retry_call(
        lambda: quote_ctx.request_history_kline(
            code,
            ktype=ktype,
            autype=AuType.QFQ,
            max_count=max_count,
        ),
        label=f"K线 {code} {_timeframe_name(ktype)}",
        expect_ret_ok=True,
    )
    if ret == RET_OK and kline is not None and not kline.empty:
        put_cached_kline(code, ktype, max_count, kline)
    return ret, kline, page_req_key
