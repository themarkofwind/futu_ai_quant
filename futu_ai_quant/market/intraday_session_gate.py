"""日内做 T：非交易时段休眠，直到 OpenD 市场状态进入连续交易。"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from datetime import datetime

from futu_ai_quant.brokers.futu.intraday_monitor import log_intraday_t
from futu_ai_quant.brokers.futu.market_state import FutuMarketSessionGate
from futu_ai_quant.market.session import (
    is_trading_session,
    next_hk_session_open,
    next_us_session_open,
    seconds_until_trading_session,
)


def _next_open_label(market: str) -> str:
    if market.upper() == "US":
        nxt = next_us_session_open()
        return nxt.strftime("%Y-%m-%d %H:%M %Z")
    return next_hk_session_open().strftime("%Y-%m-%d %H:%M")


def wait_for_trading_session(
    market: str,
    *,
    should_stop: Callable[[], bool],
    on_idle_tick: Callable[[], None] | None = None,
    chunk_sec: float = 30.0,
    is_open: Callable[[], bool] | None = None,
    session_gate: FutuMarketSessionGate | None = None,
    code: str | None = None,
) -> bool:
    """
    阻塞直到进入可做 T 的连续交易时段。

    优先用 ``session_gate`` + ``code`` 的 OpenD ``get_market_state``；
    否则回退本地时钟。
    """
    def _open() -> bool:
        if is_open is not None:
            return is_open()
        if session_gate is not None and code:
            return session_gate.is_continuous_trading(code, force=True)
        return is_trading_session(market)

    if _open():
        return True

    wait = seconds_until_trading_session(market)
    state_note = ""
    if session_gate is not None and code:
        state = session_gate.get_market_state(code, force=True) or "UNKNOWN"
        state_note = f" | OpenD state={state}"
        # 假期工作日：本地时钟可能显示“应开盘”，改为短轮询等 API
        if is_trading_session(market) and not is_continuous_market_state_safe(state):
            wait = chunk_sec

    if market.upper() == "US":
        hours_note = "以 OpenD market_state 为准（美股连续交易多为 AFTERNOON）"
    else:
        hours_note = "以 OpenD market_state 为准（港股 MORNING/AFTERNOON，含假期）"

    log_intraday_t(
        f"非连续交易时段，暂停做T评估与推送 | {hours_note}{state_note} | "
        f"参考下一本地开盘 {_next_open_label(market)}（约 {max(wait, 0) / 60:.0f} 分钟）"
    )

    while not should_stop():
        if _open():
            detail = ""
            if session_gate is not None and code:
                detail = f" | {session_gate.describe(code)}"
            log_intraday_t(f"{market} 连续交易开始，恢复做T监控{detail}")
            return True
        if on_idle_tick is not None:
            on_idle_tick()
        if session_gate is not None and code:
            # API 门禁：固定短睡轮询，避免假期按本地时钟误判
            remain = chunk_sec
            if not is_trading_session(market):
                remain = min(chunk_sec, max(seconds_until_trading_session(market), 1.0))
        else:
            remain = seconds_until_trading_session(market)
            if remain <= 0:
                time.sleep(1)
                continue
        time.sleep(min(chunk_sec, remain, 60.0))
    return False


def is_continuous_market_state_safe(state: str) -> bool:
    from futu_ai_quant.brokers.futu.market_state import is_continuous_market_state

    return is_continuous_market_state(state)


def wait_for_any_market_open(
    markets: Iterable[str],
    *,
    should_stop: Callable[[], bool],
    any_open: Callable[[], bool],
    on_idle_tick: Callable[[], None] | None = None,
    chunk_sec: float = 30.0,
    session_gate: FutuMarketSessionGate | None = None,
    codes: list[str] | None = None,
) -> bool:
    """多标的：任一标的进入连续交易即恢复。"""
    market_list = sorted({(m or "HK").upper() for m in markets}) or ["HK"]
    if any_open():
        return True

    soonest = min(market_list, key=lambda m: seconds_until_trading_session(m))
    wait = seconds_until_trading_session(soonest)
    state_note = ""
    if session_gate is not None and codes:
        states = session_gate.fetch_states(codes, force=True)
        state_note = " | OpenD " + ", ".join(
            f"{c}={states.get(c, 'UNKNOWN')}" for c in codes[:4]
        )
        # 本地认为开盘但 API 全关（假期）→ 短轮询
        if any(is_trading_session(m) for m in market_list) and not any_open():
            wait = chunk_sec

    log_intraday_t(
        f"当前无连续交易标的，暂停做T | 门禁=OpenD market_state{state_note} | "
        f"参考下一本地开盘 {_next_open_label(soonest)}（约 {max(wait, 0) / 60:.0f} 分钟）"
    )

    while not should_stop():
        if any_open():
            log_intraday_t("连续交易开始，恢复做T轮询")
            return True
        if on_idle_tick is not None:
            on_idle_tick()
        if session_gate is not None and codes:
            remain = chunk_sec
            if not any(is_trading_session(m) for m in market_list):
                remain = min(
                    chunk_sec,
                    max(min(seconds_until_trading_session(m) for m in market_list), 1.0),
                )
        else:
            remain = min(seconds_until_trading_session(m) for m in market_list)
            if remain <= 0:
                time.sleep(1)
                continue
        time.sleep(min(chunk_sec, remain, 60.0))
    return False


def format_hk_session_banner(now: datetime | None = None) -> str:
    now = now or datetime.now()
    active = is_trading_session("HK", now)
    return (
        f"本地时钟港股窗口={'开' if active else '关'} | "
        "实际执行以 OpenD get_market_state=MORNING/AFTERNOON 为准"
    )
