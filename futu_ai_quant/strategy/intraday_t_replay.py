"""日内 T+0 历史 K 线回放（离线演练信号与 Bark 链路）。"""

from __future__ import annotations

import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from futu_ai_quant.indicators.intraday import (
    append_kline_bars,
    compute_locked_intraday_indicators,
    compute_vwap,
    normalize_kline_frame,
)
from futu_ai_quant.strategy import intraday_t_settings as its
from futu_ai_quant.strategy.intraday_t import (
    IntradayTContext,
    SignalEvent,
    SignalKind,
    build_status_message,
    evaluate_intraday_t,
)
from futu_ai_quant.strategy.intraday_t_params import IntradayTRuleParams, resolve_rule_params
from futu_ai_quant.utils.numbers import safe_float


@dataclass
class ReplayResult:
    code: str
    day: str | None
    bars_total: int
    bars_replayed: int
    ticks_processed: int
    events: list[SignalEvent] = field(default_factory=list)
    approx_pnl: float = 0.0

    @property
    def sell_count(self) -> int:
        return sum(1 for e in self.events if e.kind == SignalKind.SELL)

    @property
    def buy_back_count(self) -> int:
        return sum(1 for e in self.events if e.kind == SignalKind.BUY_BACK)

    @property
    def buy_t_count(self) -> int:
        return sum(1 for e in self.events if e.kind == SignalKind.BUY_T)

    @property
    def sell_off_count(self) -> int:
        return sum(1 for e in self.events if e.kind == SignalKind.SELL_OFF)

    @property
    def warning_count(self) -> int:
        return sum(1 for e in self.events if e.kind == SignalKind.WARNING)

    @property
    def round_trips(self) -> int:
        return min(self.sell_count, self.buy_back_count) + min(
            self.buy_t_count, self.sell_off_count
        )


def filter_kline_by_day(frame: pd.DataFrame, day: str) -> pd.DataFrame:
    """保留 ``YYYY-MM-DD`` 当天的 5 分钟 K 线。"""
    work = normalize_kline_frame(frame)
    if work.empty:
        return work
    prefix = day.strip()
    mask = work["time_key"].str.startswith(prefix)
    return work.loc[mask].reset_index(drop=True)


def latest_trading_day(frame: pd.DataFrame) -> str | None:
    work = normalize_kline_frame(frame)
    if work.empty:
        return None
    return str(work.iloc[-1]["time_key"])[:10]


def parse_replay_clock(label: str) -> datetime | None:
    """从 ``YYYY-MM-DD HH:MM:SS`` 或带 ``@open`` 后缀的标签解析时钟。"""
    raw = str(label).split("@", 1)[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def approx_pnl_from_events(events: list[SignalEvent]) -> float:
    """用开平仓价差粗算每股 PnL（未扣费）。"""
    pnl = 0.0
    short_entry: float | None = None
    long_entry: float | None = None
    for event in events:
        px = event.price
        if px is None:
            continue
        if event.kind == SignalKind.SELL:
            short_entry = px
        elif event.kind == SignalKind.BUY_BACK and short_entry is not None:
            pnl += short_entry - px
            short_entry = None
        elif event.kind == SignalKind.BUY_T:
            long_entry = px
        elif event.kind == SignalKind.SELL_OFF and long_entry is not None:
            pnl += px - long_entry
            long_entry = None
    return round(pnl, 4)


def _bar_ticks(row: pd.Series) -> list[tuple[str, float, float, float]]:
    """
    将单根 5 分钟 K 线拆成 open/high/low/close 四个评估点。

    成交量与成交额按 25% 递增，用于近似日内累计 VWAP。
    """
    time_key = str(row["time_key"])
    open_p = safe_float(row.get("open"))
    high_p = safe_float(row.get("high"))
    low_p = safe_float(row.get("low"))
    close_p = safe_float(row.get("close"))
    turnover = safe_float(row.get("turnover")) or 0.0
    volume = safe_float(row.get("volume")) or 0.0

    prices: list[float] = []
    for p in (open_p, high_p, low_p, close_p):
        if p is not None:
            prices.append(p)

    if not prices:
        return []

    step_turnover = turnover / len(prices)
    step_volume = volume / len(prices)
    labels = ("open", "high", "low", "close")
    ticks: list[tuple[str, float, float, float]] = []
    for i, price in enumerate(prices):
        label = labels[i] if i < len(labels) else f"t{i}"
        ticks.append((f"{time_key}@{label}", price, step_turnover, step_volume))
    return ticks


def replay_intraday_t(
    kline_df: pd.DataFrame,
    *,
    code: str,
    ctx: IntradayTContext | None = None,
    day: str | None = None,
    speed_sec: float = 0.0,
    params: IntradayTRuleParams | None = None,
    on_event: Callable[[SignalEvent, str], None] | None = None,
    on_tick: Callable[[str, float, float | None, dict[str, Any]], None] | None = None,
) -> ReplayResult:
    """
    按时间顺序回放 5 分钟 K 线，模拟收盘锁定指标与秒内价格评估。

    较早的 K 线仅用于指标预热；仅对 ``day`` 当天的 K 线生成评估点。
    """
    work = normalize_kline_frame(kline_df)
    if work.empty:
        raise ValueError("回放 K 线为空，请检查标的或 --replay-day")

    replay_day = day or latest_trading_day(work)
    if replay_day is None:
        raise ValueError("无法确定回放交易日")

    # 保留回放日及之前的数据，用于 BOLL/RSI 预热
    work = work.loc[work["time_key"].str[:10] <= replay_day].reset_index(drop=True)
    day_bars = filter_kline_by_day(work, replay_day)
    if day_bars.empty:
        raise ValueError(f"回放日 {replay_day} 无 K 线数据")

    ctx = ctx or IntradayTContext()
    rule = params or resolve_rule_params(code)
    window = pd.DataFrame()
    forming_time_key: str | None = None
    locked_indicators: dict[str, Any] = {"ready": False, "locked": False}
    cum_turnover = 0.0
    cum_volume = 0.0
    events: list[SignalEvent] = []
    ticks_processed = 0
    bars_replayed = 0
    replay_started = False

    def _emit(new_events: list[SignalEvent], price: float, vwap: float | None) -> None:
        for event in new_events:
            if event.kind == SignalKind.STATUS:
                continue
            header = build_status_message(
                code=code,
                price=price,
                vwap=vwap,
                indicators=locked_indicators,
                ctx=ctx,
            )
            events.append(event)
            if on_event is not None:
                on_event(event, header)

    def _evaluate_at(price: float, vwap: float | None, label: str) -> None:
        nonlocal ticks_processed
        ticks_processed += 1
        if on_tick is not None:
            on_tick(label, price, vwap, locked_indicators)
        if not locked_indicators.get("locked"):
            return
        tick_events = evaluate_intraday_t(
            ctx,
            current_price=price,
            vwap=vwap,
            indicators=locked_indicators,
            now=parse_replay_clock(label),
            code=code,
            params=rule,
        )
        _emit(tick_events, price, vwap)
        if speed_sec > 0:
            time.sleep(speed_sec)

    for idx in range(len(work)):
        bar = work.iloc[idx : idx + 1]
        bar_day = str(bar.iloc[-1]["time_key"])[:10]
        pushed_time_key = str(bar.iloc[-1]["time_key"])

        prev_forming = forming_time_key
        window = append_kline_bars(window, bar, max_rows=its.INTRADAY_T_KLINE_WINDOW)

        if prev_forming is not None and pushed_time_key != prev_forming:
            locked_indicators = compute_locked_intraday_indicators(window)
            forming_time_key = locked_indicators.get("forming_time_key")
        else:
            forming_time_key = pushed_time_key

        if bar_day == replay_day:
            if not replay_started:
                replay_started = True
                cum_turnover = 0.0
                cum_volume = 0.0
            bars_replayed += 1
            for tick_label, price, step_turnover, step_volume in _bar_ticks(bar.iloc[-1]):
                cum_turnover += step_turnover
                cum_volume += step_volume
                vwap = compute_vwap(cum_turnover, cum_volume)
                _evaluate_at(price, vwap, tick_label)

    result = ReplayResult(
        code=code,
        day=replay_day,
        bars_total=len(day_bars),
        bars_replayed=bars_replayed,
        ticks_processed=ticks_processed,
        events=events,
    )
    result.approx_pnl = approx_pnl_from_events(events)
    return result


def _parse_float_list(raw: str) -> list[float]:
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    if not parts:
        raise ValueError("调参列表为空")
    return [float(p) for p in parts]


@dataclass
class TuneRow:
    rsi_sell: float
    rsi_buy: float
    sell: int
    buy_back: int
    buy_t: int
    sell_off: int
    round_trips: int
    approx_pnl: float


def tune_intraday_t_replay(
    kline_df: pd.DataFrame,
    *,
    code: str,
    day: str | None = None,
    rsi_sells: list[float] | None = None,
    rsi_buys: list[float] | None = None,
    base_ctx: IntradayTContext | None = None,
) -> list[TuneRow]:
    """对 RSI 买卖阈值做网格回放，按近似每股盈亏排序。"""
    sells = rsi_sells or [70.0, 75.0, 80.0]
    buys = rsi_buys or [30.0, 35.0, 40.0]
    rows: list[TuneRow] = []
    for rsi_sell in sells:
        for rsi_buy in buys:
            if rsi_buy >= rsi_sell:
                continue
            ctx = deepcopy(base_ctx) if base_ctx is not None else IntradayTContext()
            params = resolve_rule_params(
                code,
                overrides={"rsi_sell": rsi_sell, "rsi_buy": rsi_buy},
            )
            result = replay_intraday_t(
                kline_df,
                code=code,
                ctx=ctx,
                day=day,
                params=params,
            )
            rows.append(
                TuneRow(
                    rsi_sell=rsi_sell,
                    rsi_buy=rsi_buy,
                    sell=result.sell_count,
                    buy_back=result.buy_back_count,
                    buy_t=result.buy_t_count,
                    sell_off=result.sell_off_count,
                    round_trips=result.round_trips,
                    approx_pnl=result.approx_pnl,
                )
            )
    rows.sort(key=lambda r: (r.approx_pnl, r.round_trips), reverse=True)
    return rows
