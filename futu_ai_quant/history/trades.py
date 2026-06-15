from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from futu import RET_OK, OpenSecTradeContext, TrdEnv, TrdMarket

from futu_ai_quant.config.settings import (
    FUTU_HISTORY_QUERY_DAYS,
    TRADE_HISTORY_CACHE_HOURS,
    TRADE_HISTORY_DIR,
    TRADE_RECENT_OPTION_COUNT,
    TRADE_RECENT_STOCK_COUNT,
)
from futu_ai_quant.domain.positions import is_option_code, resolve_option_underlying_code
from futu_ai_quant.utils.files import atomic_write_text
from futu_ai_quant.utils.logging import log
from futu_ai_quant.utils.numbers import safe_float

# 进程内缓存：同一轮分析/循环内避免重复读盘与建索引
_MEMORY_DEALS: list[dict[str, Any]] | None = None
_MEMORY_FINGERPRINT: str | None = None
_MEMORY_INDEX: dict[str, dict[str, list[dict[str, Any]]]] | None = None


def clear_trade_history_memory_cache() -> None:
    """测试或强制刷新时清空进程内成交缓存。"""
    global _MEMORY_DEALS, _MEMORY_FINGERPRINT, _MEMORY_INDEX
    _MEMORY_DEALS = None
    _MEMORY_FINGERPRINT = None
    _MEMORY_INDEX = None


def _parse_deal_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _ytd_trade_cache_path(year: int) -> Path:
    return TRADE_HISTORY_DIR / f"deals_ytd_{year}.json"


def _underlying_index_cache_path(year: int) -> Path:
    return TRADE_HISTORY_DIR / f"deals_index_{year}.json"


def _normalize_trd_side(value: Any) -> str:
    return str(value or "").upper().replace(" ", "_")


def _is_stock_deal_code(code: str) -> bool:
    return bool(code) and not is_option_code(code)


def deal_underlying_code(code: str) -> str:
    if _is_stock_deal_code(code):
        return code
    return resolve_option_underlying_code({"code": code, "stock_owner": ""})


def _deal_record_from_row(row: pd.Series) -> dict[str, Any]:
    code = str(row.get("code", ""))
    return {
        "deal_id": str(row.get("deal_id", "")),
        "code": code,
        "stock_name": str(row.get("stock_name", "")),
        "trd_side": _normalize_trd_side(row.get("trd_side")),
        "qty": safe_float(row.get("qty")) or 0.0,
        "price": safe_float(row.get("price")) or 0.0,
        "create_time": str(row.get("create_time", "")),
        "underlying_code": deal_underlying_code(code),
        "asset_type": "stock" if _is_stock_deal_code(code) else "option",
    }


def _load_ytd_trade_cache(year: int) -> dict[str, Any] | None:
    path = _ytd_trade_cache_path(year)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _cache_fingerprint(cache: dict[str, Any]) -> str:
    return f"{cache.get('year')}|{cache.get('updated_at')}|{cache.get('deal_count')}"


def _deals_fingerprint(deals: list[dict[str, Any]], year: int) -> str:
    latest = max((str(d.get("create_time") or "") for d in deals), default="")
    return f"{year}|{len(deals)}|{latest}"


def _cache_is_fresh(cache: dict[str, Any]) -> bool:
    updated_at = _parse_deal_time(cache.get("updated_at"))
    if updated_at is None:
        return False
    return datetime.now() - updated_at < timedelta(hours=TRADE_HISTORY_CACHE_HOURS)


def _merge_deal_records(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in existing + incoming:
        key = item.get("deal_id") or f"{item.get('code')}|{item.get('create_time')}|{item.get('trd_side')}"
        merged[str(key)] = item
    records = list(merged.values())
    records.sort(key=lambda row: row.get("create_time", ""), reverse=True)
    return records


def _fetch_history_deals_between(
    trade_ctx: OpenSecTradeContext,
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    ret, frame = trade_ctx.history_deal_list_query(
        start=start.strftime("%Y-%m-%d %H:%M:%S"),
        end=end.strftime("%Y-%m-%d %H:%M:%S"),
        trd_env=TrdEnv.REAL,
        deal_market=TrdMarket.HK,
    )
    if ret != RET_OK or frame is None or isinstance(frame, str) or frame.empty:
        if ret != RET_OK:
            log("成交", f"历史成交查询失败 {start.date()}~{end.date()}: {frame}")
        return []
    return [_deal_record_from_row(row) for _, row in frame.iterrows()]


def _fetch_ytd_deals_from_api(trade_ctx: OpenSecTradeContext, year: int) -> list[dict[str, Any]]:
    now = datetime.now()
    year_start = datetime(year, 1, 1, 0, 0, 0)
    end = now
    collected: list[dict[str, Any]] = []
    window_end = end
    while window_end > year_start:
        window_start = max(year_start, window_end - timedelta(days=FUTU_HISTORY_QUERY_DAYS - 1))
        chunk = _fetch_history_deals_between(trade_ctx, window_start, window_end)
        collected = _merge_deal_records(collected, chunk)
        if window_start <= year_start:
            break
        window_end = window_start - timedelta(seconds=1)
    return [item for item in collected if item.get("create_time", "").startswith(str(year))]


def _save_ytd_trade_cache(year: int, deals: list[dict[str, Any]], *, source: str) -> Path:
    TRADE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = _ytd_trade_cache_path(year)
    payload = {
        "year": year,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": source,
        "deal_count": len(deals),
        "deals": deals,
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    _save_underlying_index_cache(year, deals, cache_payload=payload)
    return path


def _sort_deals_desc(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda item: item.get("create_time", ""), reverse=True)


def _build_underlying_index(deals: list[dict[str, Any]], year: int) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """按标的聚合当年成交，正股/期权分开排序。"""
    year_prefix = str(year)
    index: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for deal in deals:
        if not str(deal.get("create_time", "")).startswith(year_prefix):
            continue
        underlying = deal.get("underlying_code")
        if not underlying:
            continue
        bucket = index.setdefault(
            str(underlying),
            {"stock": [], "option": []},
        )
        key = "stock" if deal.get("asset_type") == "stock" else "option"
        bucket[key].append(deal)

    for bucket in index.values():
        bucket["stock"] = _sort_deals_desc(bucket["stock"])
        bucket["option"] = _sort_deals_desc(bucket["option"])
    return index


def _load_underlying_index_cache(year: int, fingerprint: str) -> dict[str, dict[str, list[dict[str, Any]]]] | None:
    path = _underlying_index_cache_path(year)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("fingerprint") == fingerprint:
            return payload.get("index")
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return None


def _save_underlying_index_cache(
    year: int,
    deals: list[dict[str, Any]],
    *,
    cache_payload: dict[str, Any],
) -> None:
    fingerprint = _cache_fingerprint(cache_payload)
    index = _build_underlying_index(deals, year)
    TRADE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        _underlying_index_cache_path(year),
        json.dumps(
            {
                "year": year,
                "fingerprint": fingerprint,
                "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "underlying_count": len(index),
                "index": index,
            },
            ensure_ascii=False,
        ),
    )


def _remember_deals(deals: list[dict[str, Any]], fingerprint: str) -> list[dict[str, Any]]:
    global _MEMORY_DEALS, _MEMORY_FINGERPRINT, _MEMORY_INDEX
    _MEMORY_DEALS = deals
    _MEMORY_FINGERPRINT = fingerprint
    _MEMORY_INDEX = None
    return deals


def load_ytd_trade_history(
    trade_ctx: OpenSecTradeContext,
    *,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """
    加载当年成交：进程内缓存 → 本地 YTD 缓存 → 增量/全量 API。

    缓存未过期时跳过 Futu API；API 失败时回退到过期的本地缓存。
    """
    global _MEMORY_DEALS, _MEMORY_FINGERPRINT

    year = datetime.now().year
    cache = _load_ytd_trade_cache(year)

    if not force_refresh and cache and _cache_is_fresh(cache):
        fingerprint = _cache_fingerprint(cache)
        if _MEMORY_DEALS is not None and _MEMORY_FINGERPRINT == fingerprint:
            log("成交", f"使用内存缓存当年成交 {len(_MEMORY_DEALS)} 条")
            return _MEMORY_DEALS
        deals = cache.get("deals", [])
        log("成交", f"使用磁盘缓存当年成交 {cache.get('deal_count', 0)} 条（{cache.get('updated_at')}）")
        return _remember_deals(deals, fingerprint)

    existing = (cache or {}).get("deals", [])
    now = datetime.now()
    try:
        if existing and not force_refresh:
            latest = max(
                (_parse_deal_time(item.get("create_time")) for item in existing),
                key=lambda dt: dt or datetime.min,
            )
            fetch_start = (latest - timedelta(days=1)) if latest else datetime(year, 1, 1)
            if fetch_start < datetime(year, 1, 1):
                fetch_start = datetime(year, 1, 1)
            incremental = _fetch_history_deals_between(trade_ctx, fetch_start, now)
            deals = _merge_deal_records(existing, incremental)
            source = "incremental"
            log("成交", f"增量刷新成交：新增 {max(0, len(deals) - len(existing))} 条，合计 {len(deals)} 条")
        else:
            deals = _fetch_ytd_deals_from_api(trade_ctx, year)
            source = "full"
            log("成交", f"全量拉取当年成交 {len(deals)} 条")

        saved = _save_ytd_trade_cache(year, deals, source=source)
        refreshed = _load_ytd_trade_cache(year) or {}
        fingerprint = _cache_fingerprint(refreshed) if refreshed else _deals_fingerprint(deals, year)
        log("成交", f"成交缓存已更新: {saved.name}")
        return _remember_deals(deals, fingerprint)
    except Exception as exc:
        if existing:
            fingerprint = _cache_fingerprint(cache) if cache else _deals_fingerprint(existing, year)
            log("成交", f"API 刷新失败，回退本地缓存 {len(existing)} 条: {exc}")
            return _remember_deals(existing, fingerprint)
        raise


def _get_underlying_index(deals: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    global _MEMORY_INDEX, _MEMORY_FINGERPRINT

    year = datetime.now().year
    cache = _load_ytd_trade_cache(year)
    fingerprint = (
        _cache_fingerprint(cache)
        if cache and cache.get("deal_count") is not None
        else _deals_fingerprint(deals, year)
    )

    if _MEMORY_INDEX is not None and _MEMORY_FINGERPRINT == fingerprint:
        return _MEMORY_INDEX

    indexed = _load_underlying_index_cache(year, fingerprint)
    if indexed is None:
        indexed = _build_underlying_index(deals, year)
        if cache:
            _save_underlying_index_cache(year, deals, cache_payload=cache)
        else:
            TRADE_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            atomic_write_text(
                _underlying_index_cache_path(year),
                json.dumps(
                    {
                        "year": year,
                        "fingerprint": fingerprint,
                        "built_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "underlying_count": len(indexed),
                        "index": indexed,
                    },
                    ensure_ascii=False,
                ),
            )

    _MEMORY_INDEX = indexed
    _MEMORY_FINGERPRINT = fingerprint
    return indexed


def _compact_trade_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": record.get("create_time"),
        "code": record.get("code"),
        "side": record.get("trd_side"),
        "qty": record.get("qty"),
        "price": record.get("price"),
        "asset_type": record.get("asset_type"),
    }


def _summarize_stock_trades(records: list[dict[str, Any]]) -> dict[str, Any]:
    buy_qty = sell_qty = 0.0
    buy_notional = sell_notional = 0.0
    buy_count = sell_count = 0
    last_trade = None

    for item in records:
        side = item.get("trd_side", "")
        qty = float(item.get("qty") or 0)
        price = float(item.get("price") or 0)
        if side == "BUY":
            buy_qty += qty
            buy_notional += qty * price
            buy_count += 1
        elif side == "SELL":
            sell_qty += qty
            sell_notional += qty * price
            sell_count += 1
        if last_trade is None:
            last_trade = _compact_trade_row(item)

    return {
        "trade_count": buy_count + sell_count,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "buy_qty": int(buy_qty) if buy_qty.is_integer() else round(buy_qty, 2),
        "sell_qty": int(sell_qty) if sell_qty.is_integer() else round(sell_qty, 2),
        "avg_buy_price": round(buy_notional / buy_qty, 3) if buy_qty > 0 else None,
        "avg_sell_price": round(sell_notional / sell_qty, 3) if sell_qty > 0 else None,
        "net_qty_change": int(buy_qty - sell_qty) if (buy_qty - sell_qty).is_integer() else round(buy_qty - sell_qty, 2),
        "last_trade": last_trade,
    }


def _build_swing_hint(
    recent_stock: list[dict[str, Any]],
    recent_option: list[dict[str, Any]],
    effective_signal: str,
    *,
    stock_limit: int,
    option_limit: int,
) -> str | None:
    if not recent_stock and not recent_option:
        return "当年无该正股或关联期权成交记录，波段节奏未受近期操作干扰"

    hints: list[str] = []
    if recent_stock:
        sides = [item.get("trd_side") for item in recent_stock]
        if "SELL" in sides and effective_signal == "SELL_SWING":
            hints.append("最近成交含卖出，若信号仍为减仓须避免重复卖、关注印花税")
        if "BUY" in sides and effective_signal == "BUY_SWING":
            hints.append("最近成交含买入，若信号仍为低吸须避免连续加仓")
        if len(recent_stock) >= 3:
            hints.append(f"最近{len(recent_stock)}笔正股成交（上限{stock_limit}笔），交易偏频，宜降频")
    if recent_option:
        hints.append(
            f"最近{len(recent_option)}笔有关联期权成交（上限{option_limit}笔），须与备兑/卖权方案一并考虑"
        )
    return "；".join(hints) if hints else None


def summarize_trade_history_for_stock(
    stock_code: str,
    deals: list[dict[str, Any]],
    *,
    effective_signal: str = "HOLD",
    underlying_index: dict[str, dict[str, list[dict[str, Any]]]] | None = None,
) -> dict[str, Any]:
    now = datetime.now()
    stock_limit = TRADE_RECENT_STOCK_COUNT
    option_limit = TRADE_RECENT_OPTION_COUNT

    index = underlying_index if underlying_index is not None else _get_underlying_index(deals)
    bucket = index.get(stock_code, {"stock": [], "option": []})
    stock_records = bucket.get("stock", [])
    option_records = bucket.get("option", [])

    recent_stock = stock_records[:stock_limit]
    recent_option = option_records[:option_limit]

    return {
        "lookback_year": now.year,
        "recent_stock_trade_limit": stock_limit,
        "recent_option_trade_limit": option_limit,
        "ytd_summary": _summarize_stock_trades(stock_records),
        "ytd_option_trade_count": len(option_records),
        "recent_swing_window": {
            "stock_trade_limit": stock_limit,
            "option_trade_limit": option_limit,
            "stock_trades": [_compact_trade_row(item) for item in recent_stock],
            "option_trades": [_compact_trade_row(item) for item in recent_option],
            "stock_trade_count": len(recent_stock),
            "option_trade_count": len(recent_option),
            "ytd_stock_trade_count": len(stock_records),
        },
        "swing_hint": _build_swing_hint(
            recent_stock,
            recent_option,
            effective_signal,
            stock_limit=stock_limit,
            option_limit=option_limit,
        ),
    }


def attach_trade_history_to_stocks(
    stocks: list[dict[str, Any]],
    deals: list[dict[str, Any]],
) -> None:
    underlying_index = _get_underlying_index(deals)
    for stock in stocks:
        combined = stock.get("combined_swing_signal") or {}
        stock["trade_history"] = summarize_trade_history_for_stock(
            stock["code"],
            deals,
            effective_signal=str(combined.get("effective_signal", "HOLD")),
            underlying_index=underlying_index,
        )
