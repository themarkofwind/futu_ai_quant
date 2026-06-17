"""Futu OpenD 轮询：多标的日内 T+0 监控。"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd
from futu import RET_OK, OpenQuoteContext, SubType

from futu_ai_quant.brokers.futu.intraday_kline import fetch_intraday_5m_klines
from futu_ai_quant.brokers.futu.intraday_monitor import log_intraday_t
from futu_ai_quant.indicators.intraday import (
    compute_locked_intraday_indicators,
    session_vwap_from_klines,
)
from futu_ai_quant.market.session import (
    currency_of_market,
    is_trading_session,
    market_of_code,
    session_date_prefix,
)
from futu_ai_quant.notify.bark import (
    bark_is_configured,
    bark_notify_warning,
    bark_title_for_signal,
    send_bark_async,
)
from futu_ai_quant.strategy.intraday_t import (
    IntradayTContext,
    SignalEvent,
    SignalKind,
    build_status_message,
    evaluate_intraday_t,
)
from futu_ai_quant.strategy.intraday_t_settings import (
    INTRADAY_T_LOT_SIZE,
    INTRADAY_T_POLL_SEC,
    INTRADAY_T_STATUS_INTERVAL_SEC,
    INTRADAY_T_TARGET_SPREAD,
)
from futu_ai_quant.utils.numbers import safe_float
from futu_ai_quant.utils.retry import retry_call


@dataclass
class WatchedSymbol:
    code: str
    market: str
    currency: str
    ctx: IntradayTContext
    last_bark_sig: str | None = None
    last_bark_at: float = 0.0
    last_status_at: float = 0.0
    last_warning_at: float = 0.0
    last_warning_msg: str | None = None


class IntradayTWatch:
    """
    多标的轮询监控器。

    与 ``IntradayTMonitor``（单标的实时推送）不同，本类在交易时段内按固定间隔
    依次拉取各标的 5 分钟 K 线，用最新价与锁定指标评估信号。
  """

    def __init__(
        self,
        quote_ctx: OpenQuoteContext,
        codes: list[str],
        *,
        poll_sec: int = INTRADAY_T_POLL_SEC,
        status_interval_sec: int = INTRADAY_T_STATUS_INTERVAL_SEC,
        lot_size: int = INTRADAY_T_LOT_SIZE,
        target_spread: float = INTRADAY_T_TARGET_SPREAD,
        lot_by_code: dict[str, int] | None = None,
        target_by_code: dict[str, float] | None = None,
    ) -> None:
        self.quote_ctx = quote_ctx
        self.poll_sec = poll_sec
        self.status_interval_sec = status_interval_sec
        lot_map = lot_by_code or {}
        spread_map = target_by_code or {}
        self.symbols = [
            WatchedSymbol(
                code=code,
                market=market_of_code(code),
                currency=currency_of_market(market_of_code(code)),
                ctx=IntradayTContext(
                    lot_size=lot_map.get(code, lot_size),
                    target_spread=spread_map.get(code, target_spread),
                    currency=currency_of_market(market_of_code(code)),
                ),
            )
            for code in codes
        ]

    def check_connection(self) -> None:
        ret, state = retry_call(
            lambda: self.quote_ctx.get_global_state(),
            label="get_global_state",
            expect_ret_ok=True,
        )
        if ret != RET_OK:
            raise RuntimeError(f"OpenD 连接检查失败: {state}")
        log_intraday_t(f"OpenD 已连接 | 市场状态: {state}")

    def any_market_open(self) -> bool:
        return any(is_trading_session(sym.market) for sym in self.symbols)

    def open_symbols(self) -> list[WatchedSymbol]:
        return [sym for sym in self.symbols if is_trading_session(sym.market)]

    def log_startup_banner(self) -> None:
        codes = ", ".join(sym.code for sym in self.symbols)
        markets = ", ".join(sorted({sym.market for sym in self.symbols}))
        log_intraday_t(
            f"多标的轮询启动 | 标的={codes} | 市场={markets} | "
            f"轮询间隔={self.poll_sec}s | 单次={self.symbols[0].ctx.lot_size} 股 | "
            f"目标净价差>={self.symbols[0].ctx.target_spread} | "
            f"Bark={'开启' if bark_is_configured() else '关闭'}"
        )

    def subscribe_klines(self) -> None:
        codes = [sym.code for sym in self.symbols]

        def _sub() -> tuple[int, str]:
            return self.quote_ctx.subscribe(
                codes,
                [SubType.K_5M],
                is_first_push=True,
                subscribe_push=False,
            )

        ret, err = retry_call(_sub, label="watch_subscribe_k5m", expect_ret_ok=True)
        if ret != RET_OK:
            raise RuntimeError(f"K 线订阅失败: {err}")
        log_intraday_t(f"已订阅 K_5M | {', '.join(codes)}")

    def poll_once(self) -> int:
        """轮询一轮，返回本轮实际处理的标的数。"""
        processed = 0
        for sym in self.symbols:
            if not is_trading_session(sym.market):
                continue
            self._poll_symbol(sym)
            processed += 1
        return processed

    def _fetch_kline(self, code: str) -> pd.DataFrame:
        kline, _source = fetch_intraday_5m_klines(self.quote_ctx, code)
        return kline

    def _poll_symbol(self, sym: WatchedSymbol) -> None:
        kline = self._fetch_kline(sym.code)
        session_date = session_date_prefix(sym.market)
        indicators = compute_locked_intraday_indicators(kline)
        price = safe_float(kline.iloc[-1].get("close")) if not kline.empty else None
        vwap = session_vwap_from_klines(kline, session_date)

        events = evaluate_intraday_t(
            sym.ctx,
            current_price=price,
            vwap=vwap,
            indicators=indicators,
        )
        self._emit_events(sym, events, indicators, price, vwap)
        self._maybe_print_status(sym, indicators, price, vwap)

    def _emit_events(
        self,
        sym: WatchedSymbol,
        events: list[SignalEvent],
        indicators: dict,
        price: float | None,
        vwap: float | None,
    ) -> None:
        for event in events:
            if event.kind == SignalKind.STATUS:
                continue
            if event.kind == SignalKind.WARNING:
                now = time.time()
                if (
                    sym.last_warning_msg == event.message
                    and (now - sym.last_warning_at) < 60
                ):
                    continue
                sym.last_warning_at = now
                sym.last_warning_msg = event.message

            header = build_status_message(
                code=sym.code,
                price=price,
                vwap=vwap,
                indicators=indicators,
                ctx=sym.ctx,
            )
            log_intraday_t(f"[{sym.code}] {header}\n{event.message}")
            self._maybe_notify_bark(sym, event, header)

    def _maybe_notify_bark(
        self,
        sym: WatchedSymbol,
        event: SignalEvent,
        header: str,
    ) -> None:
        if not bark_is_configured():
            return
        notify_kinds = {SignalKind.SELL, SignalKind.BUY_BACK}
        if bark_notify_warning():
            notify_kinds.add(SignalKind.WARNING)
        if event.kind not in notify_kinds:
            return

        sig = f"{event.kind}:{event.price}"
        now = time.time()
        if sym.last_bark_sig == sig and (now - sym.last_bark_at) < 30:
            return
        sym.last_bark_sig = sig
        sym.last_bark_at = now

        title = bark_title_for_signal(event.kind.value, sym.code)
        send_bark_async(title, f"{header}\n{event.message}")

    def _maybe_print_status(
        self,
        sym: WatchedSymbol,
        indicators: dict,
        price: float | None,
        vwap: float | None,
        *,
        force: bool = False,
    ) -> None:
        now = time.time()
        if not force and (now - sym.last_status_at) < self.status_interval_sec:
            return
        if not indicators.get("locked"):
            return

        sym.last_status_at = now
        msg = build_status_message(
            code=sym.code,
            price=price,
            vwap=vwap,
            indicators=indicators,
            ctx=sym.ctx,
        )
        log_intraday_t(f"[{sym.code}] {msg}")
