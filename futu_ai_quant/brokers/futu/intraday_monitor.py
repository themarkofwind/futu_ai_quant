"""Futu OpenD 实时订阅：日内 T+0 监控。"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any

import pandas as pd
from futu import (
    RET_OK,
    CurKlineHandlerBase,
    OpenQuoteContext,
    RTDataHandlerBase,
    SubType,
)

from futu_ai_quant.brokers.futu.intraday_kline import fetch_intraday_5m_klines
from futu_ai_quant.brokers.futu.quotes import fetch_snapshot_map
from futu_ai_quant.indicators.intraday import (
    append_kline_bars,
    compute_locked_intraday_indicators,
    compute_vwap,
    is_rt_data_session_fresh,
    normalize_kline_frame,
    session_vwap_from_klines,
)
from futu_ai_quant.market.session import market_of_code, session_date_prefix
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
    INTRADAY_T_EVAL_TICK_SEC,
    INTRADAY_T_KLINE_WINDOW,
    INTRADAY_T_STATUS_INTERVAL_SEC,
)
from futu_ai_quant.utils.numbers import safe_float
from futu_ai_quant.utils.retry import retry_call


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log_intraday_t(message: str) -> None:
    print(f"[{_now_str()}] [做T] {message}")


class IntradayRTHandler(RTDataHandlerBase):
    """实时分时推送：秒级更新现价与日内 VWAP，并触发信号评估。"""

    def __init__(self, monitor: IntradayTMonitor) -> None:
        super().__init__()
        self._monitor = monitor

    def on_recv_rsp(self, rsp_pb) -> tuple[int, Any]:
        ret, content = super().on_recv_rsp(rsp_pb)
        if ret == RET_OK and content is not None and not content.empty:
            try:
                self._monitor.on_rt_data(content)
            except Exception as exc:
                log_intraday_t(f"on_rt_data 回调异常: {exc}")
        return ret, content


class IntradayKlineHandler(CurKlineHandlerBase):
    """5 分钟 K 线推送：仅用于维护 K 线窗口，并在 K 线收盘时锁定指标。"""

    def __init__(self, monitor: IntradayTMonitor) -> None:
        super().__init__()
        self._monitor = monitor

    def on_recv_rsp(self, rsp_pb) -> tuple[int, Any]:
        ret, content = super().on_recv_rsp(rsp_pb)
        if ret == RET_OK and content is not None and not content.empty:
            try:
                self._monitor.on_kline_push(content)
            except Exception as exc:
                log_intraday_t(f"on_kline_push 回调异常: {exc}")
        return ret, content


class IntradayTMonitor:
    """
    日内 T+0 监控器。

    数据频率设计
    ------------
    - **秒级（RT_DATA 推送）**：现价、VWAP、信号判断（对比已锁定指标）
    - **5 分钟（K_5M 收盘）**：锁定 RSI / BOLL，避免未收盘 K 线导致指标闪烁
    - 预热优先 ``get_cur_kline``（订阅后一次性拉取最近交易日 K 线），回退历史接口
    """

    def __init__(
        self,
        quote_ctx: OpenQuoteContext,
        code: str,
        *,
        ctx: IntradayTContext | None = None,
        status_interval_sec: int = INTRADAY_T_STATUS_INTERVAL_SEC,
        eval_tick_sec: float = INTRADAY_T_EVAL_TICK_SEC,
    ) -> None:
        self.quote_ctx = quote_ctx
        self.code = code
        self.ctx = ctx or IntradayTContext()
        self.status_interval_sec = status_interval_sec
        self.eval_tick_sec = eval_tick_sec

        self._lock = threading.Lock()
        self._kline_df = pd.DataFrame()
        self._locked_indicators: dict[str, Any] = {"ready": False, "locked": False}
        self._forming_time_key: str | None = None
        self._current_price: float | None = None
        self._vwap: float | None = None
        self._last_rt_at = 0.0
        self._last_fallback_at = 0.0
        self._last_status_at = 0.0
        self._last_eval_at = 0.0
        self._last_warning_at = 0.0
        self._last_warning_msg: str | None = None
        self._last_bark_sig: str | None = None
        self._last_bark_at = 0.0
        self._rt_handler = IntradayRTHandler(self)
        self._kline_handler = IntradayKlineHandler(self)

    def check_connection(self) -> None:
        ret, state = retry_call(
            lambda: self.quote_ctx.get_global_state(),
            label="get_global_state",
            expect_ret_ok=True,
        )
        if ret != RET_OK:
            raise RuntimeError(f"OpenD 连接检查失败: {state}")
        log_intraday_t(f"OpenD 已连接 | 市场状态: {state}")

    def _refresh_locked_indicators(self, *, reason: str) -> None:
        """在已收盘 K 线上重算并锁定 RSI / BOLL（调用方需已持有 _lock）。"""
        self._locked_indicators = compute_locked_intraday_indicators(self._kline_df)
        if self._locked_indicators.get("locked"):
            self._forming_time_key = self._locked_indicators.get("forming_time_key")
            log_intraday_t(
                f"5分钟K线收盘锁定指标（{reason}）| "
                f"收盘K={self._locked_indicators.get('locked_at')} | "
                f"形成中K={self._forming_time_key} | "
                f"RSI={self._locked_indicators.get('rsi')} "
                f"BOLL上={self._locked_indicators.get('boll_upper')} "
                f"BOLL下={self._locked_indicators.get('boll_lower')}"
            )

    def bootstrap_history(self) -> None:
        log_intraday_t(f"预热 {self.code} 5 分钟 K 线窗口（{INTRADAY_T_KLINE_WINDOW} 根）...")
        kline, source = fetch_intraday_5m_klines(self.quote_ctx, self.code)

        with self._lock:
            self._kline_df = kline
            self._refresh_locked_indicators(reason="历史预热")
            self._sync_price_vwap_from_klines_unlocked()

        log_intraday_t(
            f"K 线预热就绪：{source} | 已收盘 {max(len(self._kline_df) - 1, 0)} 根用于锁定指标"
        )

    def subscribe(self) -> None:
        self.quote_ctx.set_handler(self._rt_handler)
        self.quote_ctx.set_handler(self._kline_handler)

        def _sub() -> tuple[int, str]:
            return self.quote_ctx.subscribe(
                [self.code],
                [SubType.RT_DATA, SubType.K_5M],
                is_first_push=True,
                subscribe_push=True,
            )

        ret, err = retry_call(_sub, label="subscribe_rt_k5m", expect_ret_ok=True)
        if ret != RET_OK:
            raise RuntimeError(f"订阅失败: {err}")
        log_intraday_t(
            f"已订阅 {self.code} | RT_DATA=秒级现价/VWAP | K_5M=5分钟收盘锁定指标"
        )

    def _session_date(self) -> str:
        return session_date_prefix(market_of_code(self.code))

    def _rt_data_is_live(self) -> bool:
        return (time.time() - self._last_rt_at) < 15.0

    def _sync_price_vwap_from_klines_unlocked(self) -> None:
        """调用方已持有 _lock。"""
        if self._kline_df.empty:
            return
        close = safe_float(self._kline_df.iloc[-1].get("close"))
        if close is not None:
            self._current_price = close
        vwap = session_vwap_from_klines(self._kline_df, self._session_date())
        if vwap is not None:
            self._vwap = vwap

    def refresh_quote_fallback(self) -> None:
        """
        RT_DATA 缺失或推送昨收价时，用快照 + K 线兜底现价/VWAP。

        OpenD 对部分港股标的的 RT_DATA 可能只推一次陈旧昨收，不能作为唯一现价来源。
        """
        now = time.time()
        if self._rt_data_is_live() and self._current_price is not None:
            return
        if (now - self._last_fallback_at) < 2.0:
            return

        snapshot = fetch_snapshot_map(self.quote_ctx, [self.code]).get(self.code, {})
        price = safe_float(snapshot.get("last_price")) or safe_float(snapshot.get("cur_price"))

        with self._lock:
            self._last_fallback_at = now
            if not self._rt_data_is_live():
                self._sync_price_vwap_from_klines_unlocked()
            if price is not None and not self._rt_data_is_live():
                self._current_price = price

    def on_rt_data(self, frame: pd.DataFrame) -> None:
        """秒级路径：用推送现价对比已锁定的 RSI / BOLL。"""
        try:
            row = frame.iloc[-1]
            rt_time = str(row.get("time", ""))
            price = safe_float(row.get("cur_price"))
            turnover = safe_float(row.get("turnover"))
            volume = safe_float(row.get("volume"))
            vwap = compute_vwap(turnover, volume)

            events: list[SignalEvent] = []
            with self._lock:
                if is_rt_data_session_fresh(rt_time, self._session_date()):
                    if price is not None:
                        self._current_price = price
                    if vwap is not None:
                        self._vwap = vwap
                    self._last_rt_at = time.time()
                events = self._evaluate_locked()

            self._emit_events(events)
        except Exception as exc:
            log_intraday_t(f"on_rt_data 异常: {exc}")

    def on_kline_push(self, frame: pd.DataFrame) -> None:
        """
        5 分钟路径：维护 K 线窗口；仅在检测到新 K 线（上一根收盘）时锁定指标。

        形成中的 K 线推送只更新 OHLCV，不刷新 RSI/BOLL，也不触发信号评估。
        """
        try:
            push = normalize_kline_frame(frame)
            if push.empty:
                return

            pushed_time_key = str(push.iloc[-1]["time_key"])
            bar_closed = False
            events: list[SignalEvent] = []

            with self._lock:
                prev_forming = self._forming_time_key
                self._kline_df = append_kline_bars(
                    self._kline_df,
                    frame,
                    max_rows=INTRADAY_T_KLINE_WINDOW,
                )

                if prev_forming is not None and pushed_time_key != prev_forming:
                    bar_closed = True
                    self._refresh_locked_indicators(reason="推送收盘")
                    events = self._evaluate_locked()
                else:
                    self._forming_time_key = pushed_time_key

                if not self._rt_data_is_live():
                    self._sync_price_vwap_from_klines_unlocked()

            if bar_closed:
                self._emit_events(events)
        except Exception as exc:
            log_intraday_t(f"on_kline_push 异常: {exc}")

    def _evaluate_locked(self) -> list[SignalEvent]:
        return evaluate_intraday_t(
            self.ctx,
            current_price=self._current_price,
            vwap=self._vwap,
            indicators=self._locked_indicators,
        )

    def _emit_events(self, events: list[SignalEvent]) -> None:
        for event in events:
            if event.kind == SignalKind.STATUS:
                continue
            if event.kind == SignalKind.WARNING:
                # 避免本地补帧评估导致 WARNING 刷屏：同内容 60 秒内只输出一次
                now = time.time()
                if (
                    self._last_warning_msg == event.message
                    and (now - self._last_warning_at) < 60
                ):
                    continue
                self._last_warning_at = now
                self._last_warning_msg = event.message
            header = build_status_message(
                code=self.code,
                price=event.price,
                vwap=event.vwap,
                indicators=self._locked_indicators,
                ctx=self.ctx,
            )
            log_intraday_t(f"{header}\n{event.message}")
            self._maybe_notify_bark(event, header)

    def _maybe_notify_bark(self, event: SignalEvent, header: str) -> None:
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
        if self._last_bark_sig == sig and (now - self._last_bark_at) < 30:
            return
        self._last_bark_sig = sig
        self._last_bark_at = now

        title = bark_title_for_signal(event.kind.value, self.code)
        body = f"{header}\n{event.message}"
        send_bark_async(title, body)

    def maybe_print_status(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and (now - self._last_status_at) < self.status_interval_sec:
            return

        with self._lock:
            if not self._locked_indicators.get("locked"):
                return
            msg = build_status_message(
                code=self.code,
                price=self._current_price,
                vwap=self._vwap,
                indicators=self._locked_indicators,
                ctx=self.ctx,
            )

        self._last_status_at = now
        log_intraday_t(msg)

    def maybe_eval_tick(self) -> None:
        """
        本地补帧评估（不请求 OpenD）：
        - 即使 RT_DATA 推送变慢，也能稳定按 1~3 秒频率评估一次信号
        - 仅使用最近一次收到的 price/vwap + 已锁定 RSI/BOLL
        """
        if self.eval_tick_sec <= 0:
            return
        now = time.time()
        if (now - self._last_eval_at) < self.eval_tick_sec:
            return
        self._last_eval_at = now

        events: list[SignalEvent] = []
        with self._lock:
            if self._current_price is None or self._vwap is None:
                return
            if not self._locked_indicators.get("locked"):
                return
            events = self._evaluate_locked()

        self._emit_events(events)

    def log_startup_banner(self) -> None:
        mode = {
            "SHORT_T": "监控买回",
            "LONG_T": "监控卖出",
        }.get(self.ctx.state.value, "双向做T")
        tick_note = (
            "关闭"
            if self.eval_tick_sec <= 0
            else f"{self.eval_tick_sec:g}s"
        )
        log_intraday_t(
            f"监控启动 | 标的={self.code} | 单次={self.ctx.lot_size} 股 | "
            f"目标净价差>={self.ctx.target_spread} {self.ctx.currency} | 状态={self.ctx.state.value} | "
            f"当前模式={mode} | 现价=秒级推送 | 指标=5分钟收盘锁定 | 补帧评估={tick_note} | "
            f"Bark={'开启' if bark_is_configured() else '关闭'}"
        )
