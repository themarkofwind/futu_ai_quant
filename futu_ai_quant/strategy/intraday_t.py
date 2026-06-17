"""日内 T+0 双向做 T 信号状态机（纯逻辑，便于单测）。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import pandas as pd

from futu_ai_quant.indicators.intraday import (
    compute_intraday_indicators,
    count_consecutive_closes_above_upper,
    count_consecutive_closes_below_lower,
    normalize_kline_frame,
)
from futu_ai_quant.strategy.intraday_t_settings import (
    INTRADAY_T_CONSECUTIVE_ABOVE_BAND,
    INTRADAY_T_LOT_SIZE,
    INTRADAY_T_RSI_BUY,
    INTRADAY_T_RSI_SELL,
    INTRADAY_T_TARGET_SPREAD,
    INTRADAY_T_VOLUME_SURGE_RATIO,
    INTRADAY_T_VWAP_DISCOUNT,
    INTRADAY_T_VWAP_PREMIUM,
)
from futu_ai_quant.utils.numbers import safe_float


class IntradayTState(StrEnum):
    AT_BASE = "AT_BASE"
    SHORT_T = "SHORT_T"
    LONG_T = "LONG_T"


class SignalKind(StrEnum):
    STATUS = "STATUS"
    SELL = "SELL"
    BUY_T = "BUY_T"
    BUY_BACK = "BUY_BACK"
    SELL_OFF = "SELL_OFF"
    WARNING = "WARNING"


@dataclass
class SignalEvent:
    kind: SignalKind
    message: str
    price: float | None = None
    vwap: float | None = None
    rsi: float | None = None
    boll_upper: float | None = None
    state: IntradayTState = IntradayTState.AT_BASE


@dataclass
class IntradayTContext:
    state: IntradayTState = IntradayTState.AT_BASE
    entry_price: float | None = None
    warning_uptrend: bool = False
    warning_downtrend: bool = False
    lot_size: int = INTRADAY_T_LOT_SIZE
    target_spread: float = INTRADAY_T_TARGET_SPREAD
    currency: str = "HKD"

    @property
    def sell_price(self) -> float | None:
        """兼容旧字段：SHORT_T 时的卖出锚定价。"""
        if self.state == IntradayTState.SHORT_T:
            return self.entry_price
        return None

    @sell_price.setter
    def sell_price(self, value: float | None) -> None:
        self.entry_price = value


def _resolve_lower_col(frame: pd.DataFrame) -> str | None:
    col = next((c for c in frame.columns if c.startswith("BBL_")), None)
    return col


def _resolve_upper_col(frame: pd.DataFrame) -> str | None:
    col = next((c for c in frame.columns if c.startswith("BBU_")), None)
    return col


def detect_strong_uptrend_warning(
    indicators: dict[str, Any],
    *,
    volume_surge_ratio: float = INTRADAY_T_VOLUME_SURGE_RATIO,
    consecutive_above: int = INTRADAY_T_CONSECUTIVE_ABOVE_BAND,
) -> tuple[bool, str | None]:
    """单边放量暴涨：暂停高抛开仓，避免卖飞。"""
    if not indicators.get("locked") and not indicators.get("ready"):
        return False, None

    frame: pd.DataFrame | None = indicators.get("frame")
    if frame is None or frame.empty:
        return False, None

    upper_col = _resolve_upper_col(frame)
    if upper_col is None:
        return False, None

    streak = count_consecutive_closes_above_upper(frame, upper_col)
    volume = safe_float(indicators.get("volume"))
    volume_ma = safe_float(indicators.get("volume_ma"))
    volume_surge = (
        volume is not None
        and volume_ma not in (None, 0)
        and volume >= volume_ma * volume_surge_ratio
    )

    if streak >= consecutive_above and volume_surge:
        return True, (
            f"连续 {streak} 根 5 分钟 K 线收盘站上布林上轨，"
            f"最新成交量 {volume:.0f} 为均量 {volume_ma:.0f} 的 "
            f"{volume / volume_ma:.1f} 倍"
        )
    return False, None


def detect_strong_downtrend_warning(
    indicators: dict[str, Any],
    *,
    volume_surge_ratio: float = INTRADAY_T_VOLUME_SURGE_RATIO,
    consecutive_below: int = INTRADAY_T_CONSECUTIVE_ABOVE_BAND,
) -> tuple[bool, str | None]:
    """单边放量暴跌：暂停低吸开仓，避免接飞刀。"""
    if not indicators.get("locked") and not indicators.get("ready"):
        return False, None

    frame: pd.DataFrame | None = indicators.get("frame")
    if frame is None or frame.empty:
        return False, None

    lower_col = _resolve_lower_col(frame)
    if lower_col is None:
        return False, None

    streak = count_consecutive_closes_below_lower(frame, lower_col)
    volume = safe_float(indicators.get("volume"))
    volume_ma = safe_float(indicators.get("volume_ma"))
    volume_surge = (
        volume is not None
        and volume_ma not in (None, 0)
        and volume >= volume_ma * volume_surge_ratio
    )

    if streak >= consecutive_below and volume_surge:
        return True, (
            f"连续 {streak} 根 5 分钟 K 线收盘跌破布林下轨，"
            f"最新成交量 {volume:.0f} 为均量 {volume_ma:.0f} 的 "
            f"{volume / volume_ma:.1f} 倍"
        )
    return False, None


# 兼容旧测试/调用
detect_strong_trend_warning = detect_strong_uptrend_warning


def _resolve_price(current_price: float | None, indicators: dict[str, Any]) -> float | None:
    if current_price is not None:
        return current_price
    return safe_float(indicators.get("close"))


def evaluate_intraday_t(
    ctx: IntradayTContext,
    *,
    current_price: float | None,
    vwap: float | None,
    indicators: dict[str, Any],
) -> list[SignalEvent]:
    """根据秒级现价与已锁定指标评估双向做 T 信号，并原地更新状态机。"""
    events: list[SignalEvent] = []
    price = _resolve_price(current_price, indicators)
    rsi = safe_float(indicators.get("rsi"))
    boll_upper = safe_float(indicators.get("boll_upper"))
    boll_lower = safe_float(indicators.get("boll_lower"))
    indicators_ready = indicators.get("locked") or indicators.get("ready")

    if ctx.state == IntradayTState.AT_BASE:
        up_warn, up_msg = detect_strong_uptrend_warning(indicators)
        down_warn, down_msg = detect_strong_downtrend_warning(indicators)
        ctx.warning_uptrend = up_warn
        ctx.warning_downtrend = down_warn
        if up_warn and up_msg:
            events.append(
                SignalEvent(
                    kind=SignalKind.WARNING,
                    message=f"🚨 [WARNING] 强上涨，暂停高抛开仓以免卖飞！{up_msg}",
                    price=price,
                    vwap=vwap,
                    rsi=rsi,
                    boll_upper=boll_upper,
                    state=ctx.state,
                )
            )
        if down_warn and down_msg:
            events.append(
                SignalEvent(
                    kind=SignalKind.WARNING,
                    message=f"🚨 [WARNING] 强下跌，暂停低吸开仓以免接飞刀！{down_msg}",
                    price=price,
                    vwap=vwap,
                    rsi=rsi,
                    boll_upper=boll_upper,
                    state=ctx.state,
                )
            )
    else:
        ctx.warning_uptrend = False
        ctx.warning_downtrend = False

    if not indicators_ready or price is None:
        return events

    sell_open_ready = (
        boll_upper is not None
        and rsi is not None
        and vwap is not None
        and price >= boll_upper
        and rsi >= INTRADAY_T_RSI_SELL
        and price > vwap * INTRADAY_T_VWAP_PREMIUM
    )

    buy_open_ready = (
        boll_lower is not None
        and rsi is not None
        and vwap is not None
        and price <= boll_lower
        and rsi <= INTRADAY_T_RSI_BUY
        and price < vwap * INTRADAY_T_VWAP_DISCOUNT
    )

    if ctx.state == IntradayTState.AT_BASE and sell_open_ready and not ctx.warning_uptrend:
        ctx.state = IntradayTState.SHORT_T
        ctx.entry_price = price
        events.append(
            SignalEvent(
                kind=SignalKind.SELL,
                message=(
                    f"🚨 [SELL T] 建议卖出 {ctx.lot_size} 股 @ {price:.3f} {ctx.currency} | "
                    f"锚定价={price:.3f} | 目标买回 <= {price - ctx.target_spread:.3f} {ctx.currency}"
                ),
                price=price,
                vwap=vwap,
                rsi=rsi,
                boll_upper=boll_upper,
                state=ctx.state,
            )
        )
        return events

    if ctx.state == IntradayTState.AT_BASE and buy_open_ready and not ctx.warning_downtrend:
        ctx.state = IntradayTState.LONG_T
        ctx.entry_price = price
        events.append(
            SignalEvent(
                kind=SignalKind.BUY_T,
                message=(
                    f"🚨 [BUY T] 建议买入 {ctx.lot_size} 股 @ {price:.3f} {ctx.currency} | "
                    f"锚定价={price:.3f} | 目标卖出 >= {price + ctx.target_spread:.3f} {ctx.currency}"
                ),
                price=price,
                vwap=vwap,
                rsi=rsi,
                boll_upper=boll_upper,
                state=ctx.state,
            )
        )
        return events

    if ctx.state == IntradayTState.SHORT_T and ctx.entry_price is not None:
        take_profit = price <= (ctx.entry_price - ctx.target_spread)
        technical_buy = (
            boll_lower is not None
            and rsi is not None
            and price <= boll_lower
            and rsi <= INTRADAY_T_RSI_BUY
        )
        if take_profit or technical_buy:
            reason = (
                f"硬性止盈（<= {ctx.entry_price - ctx.target_spread:.3f}）"
                if take_profit
                else f"技术面共振（BOLL 下轨 {boll_lower:.3f} & RSI {rsi:.1f}）"
            )
            profit = ctx.entry_price - price
            events.append(
                SignalEvent(
                    kind=SignalKind.BUY_BACK,
                    message=(
                        f"✅ [BUY BACK] 建议买回 {ctx.lot_size} 股 @ {price:.3f} {ctx.currency} | "
                        f"触发：{reason} | 预估净价差 {profit:.3f} {ctx.currency}"
                    ),
                    price=price,
                    vwap=vwap,
                    rsi=rsi,
                    boll_upper=boll_upper,
                    state=IntradayTState.AT_BASE,
                )
            )
            ctx.state = IntradayTState.AT_BASE
            ctx.entry_price = None

    elif ctx.state == IntradayTState.LONG_T and ctx.entry_price is not None:
        take_profit = price >= (ctx.entry_price + ctx.target_spread)
        technical_sell = (
            boll_upper is not None
            and rsi is not None
            and vwap is not None
            and price >= boll_upper
            and rsi >= INTRADAY_T_RSI_SELL
            and price > vwap * INTRADAY_T_VWAP_PREMIUM
        )
        if take_profit or technical_sell:
            reason = (
                f"硬性止盈（>= {ctx.entry_price + ctx.target_spread:.3f}）"
                if take_profit
                else f"技术面共振（BOLL 上轨 {boll_upper:.3f} & RSI {rsi:.1f}）"
            )
            profit = price - ctx.entry_price
            events.append(
                SignalEvent(
                    kind=SignalKind.SELL_OFF,
                    message=(
                        f"✅ [SELL OFF] 建议卖出 {ctx.lot_size} 股 @ {price:.3f} {ctx.currency} | "
                        f"触发：{reason} | 预估净价差 {profit:.3f} {ctx.currency}"
                    ),
                    price=price,
                    vwap=vwap,
                    rsi=rsi,
                    boll_upper=boll_upper,
                    state=IntradayTState.AT_BASE,
                )
            )
            ctx.state = IntradayTState.AT_BASE
            ctx.entry_price = None

    return events


def build_status_message(
    *,
    code: str,
    price: float | None,
    vwap: float | None,
    indicators: dict[str, Any],
    ctx: IntradayTContext,
) -> str:
    """格式化心跳日志。"""
    rsi = safe_float(indicators.get("rsi"))
    boll_upper = safe_float(indicators.get("boll_upper"))
    boll_lower = safe_float(indicators.get("boll_lower"))
    locked_at = indicators.get("locked_at")
    forming_key = indicators.get("forming_time_key")
    parts = [
        f"标的={code}",
        f"价格={price:.3f}" if price is not None else "价格=N/A",
        f"VWAP={vwap:.3f}" if vwap is not None else "VWAP=N/A",
        f"RSI(锁定)={rsi:.1f}" if rsi is not None else "RSI(锁定)=N/A",
        f"BOLL上(锁定)={boll_upper:.3f}" if boll_upper is not None else "BOLL上(锁定)=N/A",
        f"BOLL下(锁定)={boll_lower:.3f}" if boll_lower is not None else "BOLL下(锁定)=N/A",
        f"状态={ctx.state.value}",
    ]
    if locked_at:
        parts.append(f"指标锁定于={locked_at}")
    if forming_key:
        parts.append(f"形成中K={forming_key}")
    if ctx.state == IntradayTState.SHORT_T:
        parts.append("模式=监控买回")
    elif ctx.state == IntradayTState.LONG_T:
        parts.append("模式=监控卖出")
    else:
        parts.append("模式=双向监控")
    if ctx.entry_price is not None:
        parts.append(f"锚定价={ctx.entry_price:.3f}")
    if ctx.warning_uptrend:
        parts.append("强上涨防御=ON")
    if ctx.warning_downtrend:
        parts.append("强下跌防御=ON")
    return " | ".join(parts)


def indicators_from_kline(frame: pd.DataFrame) -> dict[str, Any]:
    return compute_intraday_indicators(normalize_kline_frame(frame))
