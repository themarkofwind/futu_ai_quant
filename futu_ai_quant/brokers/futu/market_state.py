"""基于 Futu OpenD 的市场状态 / 交易日判断。

优先使用：
- ``get_market_state``：当前是否处于可持续交易时段（含节假日、午休、临时休市）
- ``request_trading_days``：日历层交易日（排除周末与常规假期）

官方策略示例亦以 ``MarketState.MORNING`` / ``AFTERNOON`` 作为股票连续竞价时段。
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from futu import RET_OK, MarketState, OpenQuoteContext, TradeDateMarket

from futu_ai_quant.market.session import is_trading_session, market_of_code
from futu_ai_quant.utils.logging import log
from futu_ai_quant.utils.retry import retry_call

# 股票连续交易（可做 T）状态；不含竞价 AUCTION / 盘前盘后
_CONTINUOUS_STATES = {
    str(MarketState.MORNING),
    str(MarketState.AFTERNOON),
    "MORNING",
    "AFTERNOON",
}


def _normalize_state(raw: Any) -> str:
    if raw is None:
        return ""
    if hasattr(raw, "name"):
        return str(raw.name)
    text = str(raw).strip()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text.upper()


def is_continuous_market_state(state: Any) -> bool:
    return _normalize_state(state) in {s.upper() for s in _CONTINUOUS_STATES}


def trade_date_market_for_code(code: str) -> Any:
    market = market_of_code(code)
    if market == "US":
        return TradeDateMarket.US
    return TradeDateMarket.HK


class FutuMarketSessionGate:
    """
    做 T 执行门禁：以 OpenD ``get_market_state`` 为准。

    - 缓存默认 20 秒，避免行情回调刷爆接口
    - API 失败时可选回退本地时钟（仍无法识别港交所假期）
    """

    def __init__(
        self,
        quote_ctx: OpenQuoteContext,
        *,
        ttl_sec: float = 20.0,
        fallback_local: bool = True,
    ) -> None:
        self.quote_ctx = quote_ctx
        self.ttl_sec = max(float(ttl_sec), 1.0)
        self.fallback_local = fallback_local
        self._cache: dict[str, tuple[float, str]] = {}
        self._last_api_error: str | None = None
        self._trading_day_cache: dict[str, tuple[float, bool]] = {}
        self._trading_day_info_cache: dict[str, tuple[float, bool, dict[str, str] | None]] = {}
        self._trading_day_range_cache: dict[str, tuple[float, dict[str, dict[str, str]]]] = {}

    def invalidate(self, code: str | None = None) -> None:
        if code is None:
            self._cache.clear()
            self._trading_day_range_cache.clear()
            self._trading_day_info_cache.clear()
            self._trading_day_cache.clear()
        else:
            self._cache.pop(code, None)

    def get_market_state(self, code: str, *, force: bool = False) -> str | None:
        """返回标准化状态字符串；失败返回 None。"""
        now = time.time()
        if not force and code in self._cache:
            ts, state = self._cache[code]
            if (now - ts) < self.ttl_sec:
                return state

        states = self.fetch_states([code], force=force)
        return states.get(code)

    def fetch_states(self, codes: list[str], *, force: bool = False) -> dict[str, str]:
        unique = list(dict.fromkeys(codes))
        now = time.time()
        result: dict[str, str] = {}
        missing: list[str] = []
        for code in unique:
            if not force and code in self._cache:
                ts, state = self._cache[code]
                if (now - ts) < self.ttl_sec:
                    result[code] = state
                    continue
            missing.append(code)

        if missing:
            fetched = self._query_market_states(missing)
            for code, state in fetched.items():
                self._cache[code] = (now, state)
                result[code] = state
        return result

    def _query_market_states(self, codes: list[str]) -> dict[str, str]:
        try:
            ret, data = retry_call(
                lambda: self.quote_ctx.get_market_state(codes),
                label="get_market_state",
                expect_ret_ok=True,
            )
        except Exception as exc:
            self._last_api_error = str(exc)
            log("做T", f"get_market_state 异常: {exc}")
            return {}

        if ret != RET_OK:
            self._last_api_error = str(data)
            log("做T", f"get_market_state 失败: {data}")
            return {}

        out: dict[str, str] = {}
        try:
            for _, row in data.iterrows():
                code = str(row.get("code") or "")
                state = _normalize_state(row.get("market_state"))
                if code:
                    out[code] = state
        except Exception as exc:
            self._last_api_error = str(exc)
            log("做T", f"解析 market_state 失败: {exc}")
            return {}
        self._last_api_error = None
        return out

    def is_continuous_trading(self, code: str, *, force: bool = False) -> bool:
        state = self.get_market_state(code, force=force)
        if state:
            return is_continuous_market_state(state)
        if self.fallback_local:
            return is_trading_session(market_of_code(code))
        return False

    def any_continuous_trading(self, codes: list[str], *, force: bool = False) -> bool:
        if not codes:
            return False
        states = self.fetch_states(codes, force=force)
        known = False
        for code in codes:
            state = states.get(code)
            if state:
                known = True
                if is_continuous_market_state(state):
                    return True
        if known:
            return False
        if self.fallback_local:
            return any(is_trading_session(market_of_code(c)) for c in codes)
        return False

    def is_trading_day(self, code: str, day: datetime | None = None) -> bool | None:
        """查询日历是否交易日。True/False；接口失败返回 None。"""
        day = day or datetime.now()
        day_key = day.strftime("%Y-%m-%d")
        rows = self.fetch_trading_days(market_of_code(code), day_key, day_key)
        if rows is None:
            return None
        return day_key in rows

    def get_trade_day_info(
        self,
        market: str = "HK",
        *,
        day: datetime | None = None,
    ) -> dict[str, str] | None:
        """
        当日交易日信息 ``{time, trade_date_type}``。

        - 交易日：返回 dict（``trade_date_type`` 如 WHOLE / MORNING）
        - 非交易日：返回空？——此处返回 None 且 ``fetch_trading_days`` 成功时需用 ``day_key in rows``
        - 接口失败：``fetch_trading_days`` 返回 None，本函数也返回 None

        建议调度侧直接用 ``fetch_trading_days`` 区间结果。
        """
        day = day or datetime.now()
        day_key = day.strftime("%Y-%m-%d")
        rows = self.fetch_trading_days(market, day_key, day_key)
        if rows is None:
            return None
        return rows.get(day_key)

    def fetch_trading_days(
        self,
        market: str,
        start: str,
        end: str,
    ) -> dict[str, dict[str, str]] | None:
        """
        拉取区间交易日映射 ``YYYY-MM-DD -> {time, trade_date_type}``。

        接口失败返回 None；成功但无交易日返回空 dict。
        """
        cache_key = f"range:{market.upper()}:{start}:{end}"
        now = time.time()
        cached = self._trading_day_range_cache.get(cache_key)
        if cached and (now - cached[0]) < 3600:
            return cached[1]

        market_enum = TradeDateMarket.US if market.upper() == "US" else TradeDateMarket.HK
        try:
            ret, data = retry_call(
                lambda: self.quote_ctx.request_trading_days(
                    market=market_enum,
                    start=start,
                    end=end,
                ),
                label="request_trading_days",
                expect_ret_ok=True,
            )
        except Exception as exc:
            self._last_api_error = str(exc)
            log("行情", f"request_trading_days 异常: {exc}")
            return None

        if ret != RET_OK:
            self._last_api_error = str(data)
            log("行情", f"request_trading_days 失败: {data}")
            return None

        out: dict[str, dict[str, str]] = {}
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                t = str(item.get("time") or "")
                if not t:
                    continue
                out[t] = {
                    "time": t,
                    "trade_date_type": str(item.get("trade_date_type") or "WHOLE").upper(),
                }
        self._last_api_error = None
        self._trading_day_range_cache[cache_key] = (now, out)
        # 同步单日缓存
        for t, info in out.items():
            self._trading_day_info_cache[f"info:{market.upper()}:{t}"] = (now, True, info)
        return out

    def describe(self, code: str) -> str:
        state = self.get_market_state(code) or "UNKNOWN"
        active = is_continuous_market_state(state)
        return f"{code} market_state={state} continuous={'Y' if active else 'N'}"
