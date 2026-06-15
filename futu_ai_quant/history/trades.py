from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from futu import OpenSecTradeContext, RET_OK, TrdEnv, TrdMarket

from futu_ai_quant.config.settings import (
    FUTU_HISTORY_QUERY_DAYS,
    TRADE_HISTORY_CACHE_HOURS,
    TRADE_HISTORY_DIR,
    TRADE_RECENT_SWING_DAYS,
)
from futu_ai_quant.domain.positions import is_option_code, resolve_option_underlying_code
from futu_ai_quant.utils.logging import log
from futu_ai_quant.utils.numbers import safe_float


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
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_ytd_trade_history(
    trade_ctx: OpenSecTradeContext,
    *,
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """加载当年成交：优先读本地缓存，过期则增量/全量刷新。"""
    year = datetime.now().year
    cache = _load_ytd_trade_cache(year)
    if cache and not force_refresh and _cache_is_fresh(cache):
        log("成交", f"使用缓存当年成交 {cache.get('deal_count', 0)} 条（{cache.get('updated_at')}）")
        return cache.get("deals", [])

    existing = (cache or {}).get("deals", [])
    now = datetime.now()
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

    _save_ytd_trade_cache(year, deals, source=source)
    return deals


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
    stock_code: str,
    recent_stock: list[dict[str, Any]],
    recent_option: list[dict[str, Any]],
    effective_signal: str,
) -> str | None:
    if not recent_stock and not recent_option:
        return f"近{TRADE_RECENT_SWING_DAYS}日无该正股成交，波段节奏未受近期操作干扰"

    hints: list[str] = []
    if recent_stock:
        sides = [item.get("trd_side") for item in recent_stock]
        if "SELL" in sides and effective_signal == "SELL_SWING":
            hints.append("近两周已卖出，若信号仍为减仓须避免重复卖、关注印花税")
        if "BUY" in sides and effective_signal == "BUY_SWING":
            hints.append("近两周已买入，若信号仍为低吸须避免连续加仓")
        if len(recent_stock) >= 2:
            hints.append(f"近{TRADE_RECENT_SWING_DAYS}日正股成交{len(recent_stock)}笔，交易偏频，宜降频")
    if recent_option:
        hints.append(f"近{TRADE_RECENT_SWING_DAYS}日有关联期权成交{len(recent_option)}笔，须与备兑/卖权方案一并考虑")
    return "；".join(hints) if hints else None


def summarize_trade_history_for_stock(
    stock_code: str,
    deals: list[dict[str, Any]],
    *,
    effective_signal: str = "HOLD",
) -> dict[str, Any]:
    now = datetime.now()
    recent_cutoff = now - timedelta(days=TRADE_RECENT_SWING_DAYS)
    year_prefix = str(now.year)

    stock_records: list[dict[str, Any]] = []
    option_records: list[dict[str, Any]] = []
    recent_stock: list[dict[str, Any]] = []
    recent_option: list[dict[str, Any]] = []

    for deal in deals:
        if deal.get("underlying_code") != stock_code:
            continue
        deal_time = _parse_deal_time(deal.get("create_time"))
        is_stock = deal.get("asset_type") == "stock"
        if is_stock and str(deal.get("create_time", "")).startswith(year_prefix):
            stock_records.append(deal)
        if not is_stock and str(deal.get("create_time", "")).startswith(year_prefix):
            option_records.append(deal)
        if deal_time and deal_time >= recent_cutoff:
            if is_stock:
                recent_stock.append(deal)
            else:
                recent_option.append(deal)

    recent_stock.sort(key=lambda item: item.get("create_time", ""), reverse=True)
    recent_option.sort(key=lambda item: item.get("create_time", ""), reverse=True)

    return {
        "lookback_year": now.year,
        "recent_swing_days": TRADE_RECENT_SWING_DAYS,
        "ytd_summary": _summarize_stock_trades(stock_records),
        "ytd_option_trade_count": len(option_records),
        "recent_swing_window": {
            "stock_trades": [_compact_trade_row(item) for item in recent_stock[:8]],
            "option_trades": [_compact_trade_row(item) for item in recent_option[:5]],
            "stock_trade_count": len(recent_stock),
            "option_trade_count": len(recent_option),
        },
        "swing_hint": _build_swing_hint(stock_code, recent_stock, recent_option, effective_signal),
    }


def attach_trade_history_to_stocks(
    stocks: list[dict[str, Any]],
    deals: list[dict[str, Any]],
) -> None:
    for stock in stocks:
        combined = stock.get("combined_swing_signal") or {}
        stock["trade_history"] = summarize_trade_history_for_stock(
            stock["code"],
            deals,
            effective_signal=str(combined.get("effective_signal", "HOLD")),
        )
