"""
港股代码 ↔ 中英文名称缓存。

- ``name_en``：优先富途 ``get_stock_basicinfo`` / 持仓 ``stock_name``
- ``name_zh``：富途名称含中文时直接采用；否则可选腾讯行情补全（``STOCK_NAME_ZH_ENRICH``）
"""

from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from futu import RET_OK, Market, OpenQuoteContext, SecurityType

from futu_ai_quant.config.settings import (
    STOCK_NAME_ZH_ENRICH,
    STOCK_NAMES_CACHE_HOURS,
    STOCK_NAMES_DIR,
)
from futu_ai_quant.utils.files import atomic_write_text
from futu_ai_quant.utils.logging import log

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CACHE_PATH = STOCK_NAMES_DIR / "symbols.json"
_MEMORY_CACHE: dict[str, Any] | None = None


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _cache_is_fresh(entry: dict[str, Any]) -> bool:
    updated = entry.get("updated_at")
    if not updated:
        return False
    try:
        ts = datetime.fromisoformat(str(updated))
    except ValueError:
        return False
    return datetime.now() - ts < timedelta(hours=STOCK_NAMES_CACHE_HOURS)


def _load_disk_cache() -> dict[str, Any]:
    if not _CACHE_PATH.exists():
        return {"symbols": {}}
    try:
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log("名称", f"读取名称缓存失败，将重建: {exc}")
        return {"symbols": {}}
    if not isinstance(data.get("symbols"), dict):
        return {"symbols": {}}
    return data


def _save_disk_cache(data: dict[str, Any]) -> None:
    data["updated_at"] = datetime.now().isoformat(timespec="seconds")
    atomic_write_text(_CACHE_PATH, json.dumps(data, ensure_ascii=False, indent=2))


def _hk_code_to_tencent(code: str) -> str | None:
    if not code or "." not in code:
        return None
    market, num = code.split(".", 1)
    if market.upper() != "HK":
        return None
    return f"hk{num.zfill(5)}"


def _fetch_tencent_zh_names(codes: list[str]) -> dict[str, dict[str, str]]:
    tencent_codes = []
    code_map: dict[str, str] = {}
    for code in codes:
        tc = _hk_code_to_tencent(code)
        if tc:
            tencent_codes.append(tc)
            code_map[tc] = code
    if not tencent_codes:
        return {}

    url = f"https://qt.gtimg.cn/q={','.join(tencent_codes)}"
    try:
        with urllib.request.urlopen(url, timeout=8) as resp:
            text = resp.read().decode("gbk", errors="replace")
    except Exception as exc:
        log("名称", f"腾讯行情补全中文名失败: {exc}")
        return {}

    result: dict[str, dict[str, str]] = {}
    for chunk in text.strip().split(";"):
        chunk = chunk.strip()
        if not chunk or "=\"" not in chunk:
            continue
        body = chunk.split("=\"", 1)[1].rstrip('"')
        parts = body.split("~")
        if len(parts) < 3:
            continue
        num = parts[2].zfill(5)
        hk_code = code_map.get(f"hk{num}")
        if not hk_code:
            continue
        name_zh = str(parts[1]).strip()
        name_en = str(parts[46]).strip() if len(parts) > 46 else ""
        if name_zh:
            result[hk_code] = {"name_zh": name_zh, "name_en_hint": name_en}
    return result


def _fetch_futu_basic_names(
    quote_ctx: OpenQuoteContext,
    codes: list[str],
) -> dict[str, str]:
    names: dict[str, str] = {}
    batch_size = 200
    for idx in range(0, len(codes), batch_size):
        batch = codes[idx : idx + batch_size]
        try:
            ret, data = quote_ctx.get_stock_basicinfo(Market.HK, SecurityType.STOCK, batch)
            if ret != RET_OK or data is None or data.empty:
                log("名称", f"富途静态信息失败: {data}")
                continue
            for _, row in data.iterrows():
                code = str(row.get("code", ""))
                name = str(row.get("name", "")).strip()
                if code and name:
                    names[code] = name
        except Exception as exc:
            log("名称", f"富途静态信息异常: {exc}")
    return names


def _merge_entry(
    code: str,
    *,
    futu_name: str = "",
    position_name: str = "",
    tencent: dict[str, str] | None = None,
    cached: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cached = cached or {}
    candidates = [c for c in (futu_name, position_name) if c]
    name_zh = str(cached.get("name_zh") or "")
    name_en = str(cached.get("name_en") or "")

    for candidate in candidates:
        if _has_cjk(candidate):
            name_zh = candidate
            break

    for candidate in candidates:
        if not _has_cjk(candidate):
            name_en = candidate
            break

    if not name_zh and STOCK_NAME_ZH_ENRICH and tencent:
        name_zh = str(tencent.get("name_zh") or "")

    if not name_en:
        name_en = str((tencent or {}).get("name_en_hint") or "")
    if not name_en:
        for candidate in candidates:
            name_en = candidate
            break

    return {
        "name_zh": name_zh,
        "name_en": name_en,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def display_name(entry: dict[str, Any] | None, *, code: str = "") -> str:
    if not entry:
        return code
    return str(entry.get("name_zh") or entry.get("name_en") or code)


def resolve_symbol_names(
    quote_ctx: OpenQuoteContext,
    codes: list[str],
    *,
    position_names: dict[str, str] | None = None,
    force_refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """解析并缓存代码→中英文名称；返回 ``{code: {name_zh, name_en, ...}}``。"""
    global _MEMORY_CACHE

    unique_codes = list(dict.fromkeys(code for code in codes if code))
    if not unique_codes:
        return {}

    if _MEMORY_CACHE is not None and not force_refresh:
        symbols = _MEMORY_CACHE.get("symbols", {})
        if all(code in symbols and _cache_is_fresh(symbols[code]) for code in unique_codes):
            return {code: symbols[code] for code in unique_codes}

    disk = _load_disk_cache()
    symbols: dict[str, dict[str, Any]] = dict(disk.get("symbols", {}))
    missing = [
        code
        for code in unique_codes
        if force_refresh or code not in symbols or not _cache_is_fresh(symbols[code])
    ]

    futu_names: dict[str, str] = {}
    tencent_names: dict[str, dict[str, str]] = {}
    if missing:
        futu_names = _fetch_futu_basic_names(quote_ctx, missing)
        if STOCK_NAME_ZH_ENRICH:
            tencent_names = _fetch_tencent_zh_names(missing)

    hints = position_names or {}
    for code in unique_codes:
        if code in missing or code not in symbols:
            symbols[code] = _merge_entry(
                code,
                futu_name=futu_names.get(code, ""),
                position_name=hints.get(code, ""),
                tencent=tencent_names.get(code),
                cached=symbols.get(code),
            )

    disk["symbols"] = symbols
    _save_disk_cache(disk)
    _MEMORY_CACHE = disk

    if missing:
        log("名称", f"已更新 {len(missing)} 个标的名称缓存")
    return {code: symbols[code] for code in unique_codes if code in symbols}


def clear_symbol_names_memory_cache() -> None:
    global _MEMORY_CACHE
    _MEMORY_CACHE = None
