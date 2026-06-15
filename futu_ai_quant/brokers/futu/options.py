"""
Futu OpenD 期权接口：卖权候选扫描与持仓 Greeks。

Futu API
--------
- ``get_option_expiration_date`` / ``get_option_chain``：卖权候选扫描
- ``get_option_quote``：批量/单个期权报价（IV、Delta、Theta 等）

主要导出
--------
- ``scan_sell_option_candidates``：按 ATR 与 Delta 过滤卖 Call/Put 候选
- ``fetch_option_metrics``： enrich 已有期权持仓
- ``_build_option_quote_leg``：构造报价请求腿（供 sim 模块复用）
"""

from __future__ import annotations

from typing import Any

from futu import (
    RET_OK,
    IndexOptionType,
    OpenQuoteContext,
    OptionStrategyLeg,
    OptionType,
    StrategyLegAction,
)

from futu_ai_quant.config.settings import (
    MAX_OPTION_CANDIDATES_EACH_SIDE,
    OPTION_DELTA_MAX,
    OPTION_DELTA_MIN,
    OPTION_MAX_DAYS,
    OPTION_MIN_DAYS,
    OPTION_STRIKE_ATR_MULT_HIGH,
    OPTION_STRIKE_ATR_MULT_LOW,
)
from futu_ai_quant.domain.positions import enrich_option_context, resolve_position_side
from futu_ai_quant.indicators.iv import annotate_iv_metrics
from futu_ai_quant.indicators.technical import scale_atr_to_market
from futu_ai_quant.planning.option import build_option_position_trade_plan
from futu_ai_quant.utils.logging import log
from futu_ai_quant.utils.numbers import safe_float


def _build_option_quote_leg(option_code: str) -> OptionStrategyLeg:
    leg = OptionStrategyLeg()
    leg.code = option_code
    leg.action = StrategyLegAction.BUY
    leg.quantity = 1
    return leg


def _quote_option_contracts(
    quote_ctx: OpenQuoteContext,
    option_codes: list[str],
) -> list[dict[str, Any]]:
    quoted: list[dict[str, Any]] = []
    for option_code in option_codes:
        try:
            ret, quote_df = quote_ctx.get_option_quote([_build_option_quote_leg(option_code)])
            if ret != RET_OK or quote_df is None or quote_df.empty:
                continue
            row = quote_df.iloc[0]
            delta = safe_float(row.get("delta"))
            if delta is None or not (OPTION_DELTA_MIN <= abs(delta) <= OPTION_DELTA_MAX):
                continue
            quoted.append(
                {
                    "code": option_code,
                    "last_price": safe_float(row.get("price")),
                    "implied_volatility": safe_float(row.get("implied_volatility")),
                    "delta": delta,
                    "theta": safe_float(row.get("theta")),
                    "strike_price": safe_float(row.get("strike_price")),
                    "days_to_expiry": safe_float(row.get("days_to_expiry")),
                    "option_type": str(row.get("option_type", "")),
                    "expire_time": str(row.get("expire_time", "")),
                    "contract_size": safe_float(row.get("contract_size")),
                }
            )
        except Exception:
            continue
    return quoted


def scan_sell_option_candidates(
    quote_ctx: OpenQuoteContext,
    stock: dict[str, Any],
    swing_profile: dict[str, Any],
) -> dict[str, Any]:
    overlay: dict[str, Any] = {
        "sell_call_candidates": [],
        "sell_put_candidates": [],
        "scan_note": None,
    }

    market_price = safe_float((stock.get("pnl") or {}).get("market_price"))
    if market_price is None:
        overlay["scan_note"] = "缺少现价，跳过期权链扫描"
        return overlay

    try:
        ret, exp_df = quote_ctx.get_option_expiration_date(stock["code"], IndexOptionType.NORMAL)
        if ret != RET_OK or exp_df is None or exp_df.empty:
            overlay["scan_note"] = f"到期日查询失败: {exp_df}"
            return overlay

        valid_exps = exp_df[
            (exp_df["option_expiry_date_distance"] >= OPTION_MIN_DAYS)
            & (exp_df["option_expiry_date_distance"] <= OPTION_MAX_DAYS)
        ].sort_values("option_expiry_date_distance")

        if valid_exps.empty:
            overlay["scan_note"] = f"无 {OPTION_MIN_DAYS}-{OPTION_MAX_DAYS} 天到期合约"
            return overlay

        call_codes: list[str] = []
        put_codes: list[str] = []
        seen_codes: set[str] = set()

        for _, exp_row in valid_exps.head(2).iterrows():
            expiry = str(exp_row["strike_time"])
            ret, chain = quote_ctx.get_option_chain(
                stock["code"],
                start=expiry,
                end=expiry,
                option_type=OptionType.ALL,
            )
            if ret != RET_OK or chain is None or chain.empty:
                continue

            call_rows = chain[chain["option_type"].astype(str).str.upper() == "CALL"].copy()
            put_rows = chain[chain["option_type"].astype(str).str.upper() == "PUT"].copy()

            daily = stock.get("daily") or {}
            atr_market = scale_atr_to_market(
                safe_float(daily.get("atr")),
                safe_float(daily.get("technical_close")),
                market_price,
            )
            if atr_market is not None:
                call_strike_low = market_price + OPTION_STRIKE_ATR_MULT_LOW * atr_market
                call_strike_high = market_price + OPTION_STRIKE_ATR_MULT_HIGH * atr_market
                put_strike_high = market_price - OPTION_STRIKE_ATR_MULT_LOW * atr_market
                put_strike_low = market_price - OPTION_STRIKE_ATR_MULT_HIGH * atr_market
            else:
                call_strike_low = market_price * 1.03
                call_strike_high = market_price * 1.15
                put_strike_high = market_price * 0.97
                put_strike_low = market_price * 0.85

            call_rows = call_rows[
                (call_rows["strike_price"] >= call_strike_low)
                & (call_rows["strike_price"] <= call_strike_high)
            ].sort_values("strike_price")
            put_rows = put_rows[
                (put_rows["strike_price"] <= put_strike_high)
                & (put_rows["strike_price"] >= put_strike_low)
            ].sort_values("strike_price", ascending=False)

            for code in call_rows["code"].tolist():
                if code not in seen_codes:
                    call_codes.append(code)
                    seen_codes.add(code)
            if swing_profile.get("allow_sell_put"):
                for code in put_rows["code"].tolist():
                    if code not in seen_codes:
                        put_codes.append(code)
                        seen_codes.add(code)

            if len(call_codes) >= MAX_OPTION_CANDIDATES_EACH_SIDE + 1:
                break

        if swing_profile.get("prefer_sell_call") and call_codes:
            overlay["sell_call_candidates"] = annotate_iv_metrics(
                _quote_option_contracts(
                    quote_ctx, call_codes[: MAX_OPTION_CANDIDATES_EACH_SIDE + 1]
                )[:MAX_OPTION_CANDIDATES_EACH_SIDE],
                stock["code"],
            )

        if put_codes:
            overlay["sell_put_candidates"] = annotate_iv_metrics(
                _quote_option_contracts(
                    quote_ctx, put_codes[: MAX_OPTION_CANDIDATES_EACH_SIDE + 1]
                )[:MAX_OPTION_CANDIDATES_EACH_SIDE],
                stock["code"],
            )

        if not overlay["sell_call_candidates"] and not overlay["sell_put_candidates"]:
            overlay["scan_note"] = "未找到满足 Delta 条件的卖权候选合约"
    except Exception as exc:
        overlay["scan_note"] = str(exc)

    return overlay


def build_option_leg(option: dict[str, Any]) -> OptionStrategyLeg:
    leg = OptionStrategyLeg()
    leg.code = option["code"]
    side = resolve_position_side(
        str(option.get("position_side", "")),
        safe_float(option.get("qty")) or 0.0,
    )
    leg.action = StrategyLegAction.SELL if side == "SHORT" else StrategyLegAction.BUY
    leg.quantity = abs(option.get("qty") or 1)
    return leg


def fetch_option_metrics(
    quote_ctx: OpenQuoteContext,
    options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not options:
        return []

    enriched: list[dict[str, Any]] = []
    legs = [build_option_leg(opt) for opt in options]

    try:
        ret, quote_df = quote_ctx.get_option_quote(legs)
        if ret != RET_OK or quote_df is None or quote_df.empty:
            log("期权", f"批量期权行情失败: {quote_df}")
            return fetch_option_metrics_one_by_one(quote_ctx, options)

        row_count = min(len(options), len(quote_df))
        for idx in range(row_count):
            opt = options[idx]
            row = quote_df.iloc[idx]
            enriched.append(
                enrich_option_context(
                    {
                        **opt,
                        "last_price": safe_float(row.get("price")),
                        "implied_volatility": safe_float(row.get("implied_volatility")),
                        "delta": safe_float(row.get("delta")),
                        "gamma": safe_float(row.get("gamma")),
                        "theta": safe_float(row.get("theta")),
                        "vega": safe_float(row.get("vega")),
                        "strike_price": safe_float(row.get("strike_price")),
                        "days_to_expiry": safe_float(row.get("days_to_expiry")),
                        "option_type": str(row.get("option_type", "")),
                        "expire_time": str(row.get("expire_time", "")),
                        "stock_owner": str(row.get("stock_owner", "")),
                        "contract_size": safe_float(row.get("contract_size")),
                    }
                )
            )
            enriched[-1]["option_trade_plan"] = build_option_position_trade_plan(enriched[-1])
            enriched[-1]["iv_relative"] = None
            enriched[-1]["iv_rank"] = None
            enriched[-1]["iv_rank_note"] = "持仓合约，请结合标的正股 option_overlay 的 iv_rank / iv_relative 判断"
            enriched[-1]["stock_trade_plan"] = {
                "direction": "none",
                "suggested_qty": 0,
                "suggested_lots": 0,
                "lot_size": None,
                "pct_of_holding": 0.0,
            }

        if len(options) > row_count:
            log("期权", f"批量返回行数不足，剩余 {len(options) - row_count} 个合约将逐个重试")
            for opt in options[row_count:]:
                enriched.extend(fetch_option_metrics_one_by_one(quote_ctx, [opt]))
    except Exception as exc:
        log("期权", f"批量期权行情异常，切换逐个拉取: {exc}")
        enriched = fetch_option_metrics_one_by_one(quote_ctx, options)

    return enriched


def fetch_option_metrics_one_by_one(
    quote_ctx: OpenQuoteContext,
    options: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for opt in options:
        item = {
            **opt,
            "last_price": None,
            "implied_volatility": None,
            "delta": None,
            "gamma": None,
            "theta": None,
            "vega": None,
            "quote_error": None,
        }
        try:
            ret, quote_df = quote_ctx.get_option_quote([build_option_leg(opt)])
            if ret != RET_OK or quote_df is None or quote_df.empty:
                item["quote_error"] = str(quote_df)
            else:
                row = quote_df.iloc[0]
                item.update(
                    {
                        "last_price": safe_float(row.get("price")),
                        "implied_volatility": safe_float(row.get("implied_volatility")),
                        "delta": safe_float(row.get("delta")),
                        "gamma": safe_float(row.get("gamma")),
                        "theta": safe_float(row.get("theta")),
                        "vega": safe_float(row.get("vega")),
                        "strike_price": safe_float(row.get("strike_price")),
                        "days_to_expiry": safe_float(row.get("days_to_expiry")),
                        "option_type": str(row.get("option_type", "")),
                        "expire_time": str(row.get("expire_time", "")),
                        "stock_owner": str(row.get("stock_owner", "")),
                        "contract_size": safe_float(row.get("contract_size")),
                    }
                )
                item = enrich_option_context(item)
                item["option_trade_plan"] = build_option_position_trade_plan(item)
                item["stock_trade_plan"] = {
                    "direction": "none",
                    "suggested_qty": 0,
                    "suggested_lots": 0,
                    "lot_size": None,
                    "pct_of_holding": 0.0,
                }
        except Exception as exc:
            item["quote_error"] = str(exc)
        enriched.append(item)
    return enriched
