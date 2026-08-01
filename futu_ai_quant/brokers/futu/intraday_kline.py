"""日内 5 分钟 K 线拉取（预热与轮询共用）。"""

from __future__ import annotations

import pandas as pd
from futu import RET_OK, AuType, KLType, OpenQuoteContext

from futu_ai_quant.indicators.intraday import normalize_kline_frame
from futu_ai_quant.strategy import intraday_t_settings as its
from futu_ai_quant.utils.retry import retry_call


def min_indicator_bars() -> int:
    return max(its.INTRADAY_T_BOLL_LENGTH, its.INTRADAY_T_RSI_LENGTH) + 2


def fetch_intraday_5m_klines(
    quote_ctx: OpenQuoteContext,
    code: str,
    *,
    window: int | None = None,
    history_bars: int | None = None,
    prefer_cur_kline: bool = True,
) -> tuple[pd.DataFrame, str]:
    """
    拉取日内 5 分钟 K 线窗口。

    优先 ``get_cur_kline``（需已订阅 K_5M，返回最近交易日数据）；
    不足时回退 ``request_history_kline``。
    """
    if window is None:
        window = its.INTRADAY_T_KLINE_WINDOW
    if history_bars is None:
        history_bars = its.INTRADAY_T_HISTORY_BARS
    min_bars = min_indicator_bars()

    if prefer_cur_kline:
        def _cur() -> tuple[int, pd.DataFrame | str]:
            return quote_ctx.get_cur_kline(
                code,
                num=window,
                ktype=KLType.K_5M,
                autype=AuType.NONE,
            )

        ret, kline = retry_call(_cur, label=f"get_cur_kline_5m:{code}", expect_ret_ok=True)
        if ret == RET_OK and kline is not None and not kline.empty:
            work = normalize_kline_frame(kline).tail(window)
            if len(work) >= min_bars:
                return work, _format_source("get_cur_kline", work)

    def _history() -> tuple[int, pd.DataFrame | str, object]:
        return quote_ctx.request_history_kline(
            code,
            ktype=KLType.K_5M,
            autype=AuType.NONE,
            max_count=history_bars,
        )

    ret, kline, _ = retry_call(_history, label=f"history_kline_5m:{code}", expect_ret_ok=True)
    if ret != RET_OK or kline is None or kline.empty:
        raise RuntimeError(f"5 分钟 K 线拉取失败: {kline}")

    work = normalize_kline_frame(kline).tail(window)
    if len(work) < min_bars:
        raise RuntimeError(f"5 分钟 K 线不足（{len(work)}/{min_bars}）")
    return work, _format_source("request_history_kline", work)


def _format_source(label: str, frame: pd.DataFrame) -> str:
    if frame.empty:
        return label
    first = frame.iloc[0]["time_key"]
    last = frame.iloc[-1]["time_key"]
    return f"{label} {len(frame)} 根（{first} ~ {last}）"
