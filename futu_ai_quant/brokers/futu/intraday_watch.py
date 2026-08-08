"""Futu OpenD 轮询：多标的日内 T+0 监控。"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd
from futu import RET_OK, OpenQuoteContext, SubType

from futu_ai_quant.brokers.futu.intraday_kline import fetch_intraday_5m_klines
from futu_ai_quant.brokers.futu.intraday_monitor import log_intraday_t
from futu_ai_quant.brokers.futu.market_state import FutuMarketSessionGate
from futu_ai_quant.indicators.intraday import (
    compute_locked_intraday_indicators,
    session_vwap_from_klines,
)
from futu_ai_quant.market.session import (
    currency_of_market,
    market_of_code,
    session_date_prefix,
)
from futu_ai_quant.notify.bark import (
    bark_is_configured,
    bark_notify_warning,
    bark_title_for_signal,
    send_bark_async,
)
from futu_ai_quant.strategy import intraday_t_settings as its
from futu_ai_quant.strategy.intraday_t import (
    IntradayTContext,
    IntradayTState,
    SignalEvent,
    SignalKind,
    build_status_message,
    evaluate_intraday_t,
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
        poll_sec: int | None = None,
        status_interval_sec: int | None = None,
        lot_size: int | None = None,
        target_spread: float | None = None,
        lot_by_code: dict[str, int] | None = None,
        target_by_code: dict[str, float] | None = None,
        session_gate: FutuMarketSessionGate | None = None,
    ) -> None:
        self.quote_ctx = quote_ctx
        self.session_gate = session_gate or FutuMarketSessionGate(quote_ctx)
        self.poll_sec = its.INTRADAY_T_POLL_SEC if poll_sec is None else poll_sec
        self.status_interval_sec = (
            its.INTRADAY_T_STATUS_INTERVAL_SEC
            if status_interval_sec is None
            else status_interval_sec
        )
        self._default_lot = its.INTRADAY_T_LOT_SIZE if lot_size is None else lot_size
        self._default_spread = (
            its.INTRADAY_T_TARGET_SPREAD if target_spread is None else target_spread
        )
        self._lot_by_code = dict(lot_by_code or {})
        self._target_by_code = dict(target_by_code or {})
        self._freeze_poll = poll_sec is not None
        self._freeze_status = status_interval_sec is not None
        self.symbols = [
            self._make_symbol(code) for code in codes
        ]

    def _make_symbol(self, code: str) -> WatchedSymbol:
        market = market_of_code(code)
        return WatchedSymbol(
            code=code,
            market=market,
            currency=currency_of_market(market),
            ctx=IntradayTContext(
                lot_size=self._lot_by_code.get(code, self._default_lot),
                target_spread=self._target_by_code.get(code, self._default_spread),
                currency=currency_of_market(market),
            ),
        )

    def apply_hot_config(
        self,
        *,
        codes: list[str] | None = None,
        poll_sec: int | None = None,
        status_interval_sec: int | None = None,
        lot_by_code: dict[str, int] | None = None,
        target_by_code: dict[str, float] | None = None,
    ) -> list[str]:
        """热加载轮询参数；若 codes 变化则增删标的并重新订阅。"""
        notes: list[str] = []
        if lot_by_code is not None:
            self._lot_by_code.update(lot_by_code)
        if target_by_code is not None:
            self._target_by_code.update(target_by_code)

        if not self._freeze_poll:
            value = its.INTRADAY_T_POLL_SEC if poll_sec is None else poll_sec
            if value != self.poll_sec:
                notes.append(f"poll_sec {self.poll_sec}→{value}")
                self.poll_sec = value
        if not self._freeze_status:
            value = (
                its.INTRADAY_T_STATUS_INTERVAL_SEC
                if status_interval_sec is None
                else status_interval_sec
            )
            if value != self.status_interval_sec:
                notes.append(f"status_interval {self.status_interval_sec}→{value}")
                self.status_interval_sec = value

        for sym in self.symbols:
            lot = self._lot_by_code.get(sym.code)
            if lot is not None and lot != sym.ctx.lot_size:
                notes.append(f"{sym.code} lot {sym.ctx.lot_size}→{lot}")
                sym.ctx.lot_size = lot
            spread = self._target_by_code.get(sym.code)
            if spread is not None and spread != sym.ctx.target_spread:
                if sym.ctx.state != IntradayTState.AT_BASE:
                    notes.append(
                        f"{sym.code} spread 持仓中保持 {sym.ctx.target_spread}"
                        f"（忽略热加载 {spread}）"
                    )
                else:
                    notes.append(f"{sym.code} spread {sym.ctx.target_spread}→{spread}")
                    sym.ctx.configured_spread = spread
                    sym.ctx.target_spread = spread

        if codes is not None:
            desired = list(dict.fromkeys(codes))
            current = [sym.code for sym in self.symbols]
            if desired != current:
                notes.append(f"codes {current}→{desired}")
                kept = {sym.code: sym for sym in self.symbols}
                self.symbols = []
                for code in desired:
                    if code in kept:
                        self.symbols.append(kept[code])
                    else:
                        self.symbols.append(self._make_symbol(code))
                self.subscribe_klines()
        return notes

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
        return self.session_gate.any_continuous_trading([sym.code for sym in self.symbols])

    def open_symbols(self) -> list[WatchedSymbol]:
        states = self.session_gate.fetch_states([sym.code for sym in self.symbols])
        from futu_ai_quant.brokers.futu.market_state import is_continuous_market_state

        open_list: list[WatchedSymbol] = []
        for sym in self.symbols:
            state = states.get(sym.code)
            if state:
                if is_continuous_market_state(state):
                    open_list.append(sym)
            elif self.session_gate.is_continuous_trading(sym.code):
                open_list.append(sym)
        return open_list

    def log_startup_banner(self) -> None:
        codes = ", ".join(sym.code for sym in self.symbols)
        markets = ", ".join(sorted({sym.market for sym in self.symbols}))
        log_intraday_t(
            f"多标的轮询启动 | 标的={codes} | 市场={markets} | "
            f"轮询间隔={self.poll_sec}s | 单次={self.symbols[0].ctx.lot_size} 股 | "
            f"目标净价差>={self.symbols[0].ctx.target_spread} | "
            f"Bark={'开启' if bark_is_configured() else '关闭'} | "
            f"执行门禁=OpenD market_state(MORNING/AFTERNOON)"
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
        for sym in self.open_symbols():
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
        notify_kinds = {
            SignalKind.SELL,
            SignalKind.BUY_T,
            SignalKind.BUY_BACK,
            SignalKind.SELL_OFF,
        }
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
