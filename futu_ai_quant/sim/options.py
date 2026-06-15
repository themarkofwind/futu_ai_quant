from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from futu import RET_OK, IndexOptionType, OpenQuoteContext, OptionType

from futu_ai_quant.brokers.futu.options import _build_option_quote_leg
from futu_ai_quant.config.settings import OPTION_MAX_DAYS, OPTION_MIN_DAYS
from futu_ai_quant.domain.positions import is_option_code
from futu_ai_quant.sim.settings import OPTION_PREFIX_TO_STOCK, SIM_OPTION_CONTRACT_SIZE
from futu_ai_quant.utils.logging import log
from futu_ai_quant.utils.numbers import safe_float

if TYPE_CHECKING:
    from futu_ai_quant.sim.portfolio import PaperPortfolio


def resolve_underlying_code(
    option_code: str,
    held_meta: dict[str, Any],
    decision: dict[str, Any],
) -> str:
    owner = str(held_meta.get("stock_owner") or "").strip()
    if owner:
        return owner
    for rec in decision.get("recommendations", []):
        stock_code = str(rec.get("code", ""))
        if is_option_code(stock_code):
            continue
        plan = rec.get("option_trade_plan") or {}
        if str(plan.get("contract_code", "")) == option_code:
            return stock_code
    symbol = option_code.split(".", 1)[-1]
    match = re.match(r"^([A-Z]+)", symbol)
    if match:
        return OPTION_PREFIX_TO_STOCK.get(match.group(1), "")
    return ""


def resolve_contract_size(
    code: str,
    option_quote_map: dict[str, dict[str, Any]],
    portfolio: PaperPortfolio | None = None,
    fallback: int = SIM_OPTION_CONTRACT_SIZE,
) -> int:
    meta = option_quote_map.get(code) or {}
    size = int(safe_float(meta.get("contract_size")) or 0)
    if size > 0:
        return size
    if portfolio:
        pos = portfolio.get_option(code)
        if pos:
            size = int(pos.get("contract_size") or 0)
            if size > 0:
                return size
    return fallback


def fetch_option_quote_map(
    quote_ctx: OpenQuoteContext,
    option_codes: list[str],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for code in dict.fromkeys(option_codes):
        if not is_option_code(code):
            continue
        try:
            ret, quote_df = quote_ctx.get_option_quote([_build_option_quote_leg(code)])
            if ret != RET_OK or quote_df is None or quote_df.empty:
                log("期权", f"{code} 行情失败: {quote_df}")
                continue
            row = quote_df.iloc[0]
            contract_size = int(safe_float(row.get("contract_size")) or SIM_OPTION_CONTRACT_SIZE)
            result[code] = {
                "code": code,
                "price": safe_float(row.get("price")),
                "contract_size": contract_size,
                "strike_price": safe_float(row.get("strike_price")),
                "expire_time": str(row.get("expire_time", "")),
                "option_type": str(row.get("option_type", "")).upper(),
                "stock_owner": str(row.get("stock_owner", "")),
                "days_to_expiry": safe_float(row.get("days_to_expiry")),
            }
            log("期权", f"{code} 乘数={contract_size} 价={result[code]['price']}")
        except Exception as exc:
            log("期权", f"{code} 行情异常: {exc}")
    return result


def find_roll_open_leg(
    quote_ctx: OpenQuoteContext,
    held_code: str,
    held_meta: dict[str, Any],
    decision: dict[str, Any],
    option_quote_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    underlying = resolve_underlying_code(held_code, held_meta, decision)
    strike = safe_float(held_meta.get("strike_price"))
    option_type = str(held_meta.get("option_type") or "CALL").upper()
    held_expire = str(held_meta.get("expire_time") or "")

    for rec in decision.get("recommendations", []):
        rec_code = str(rec.get("code", ""))
        if is_option_code(rec_code):
            continue
        if underlying and rec_code != underlying:
            continue
        plan = rec.get("option_trade_plan") or {}
        new_code = str(plan.get("contract_code") or "")
        if (
            plan.get("action") in ("sell_call", "sell_put", "roll")
            and new_code
            and new_code != held_code
        ):
            meta = option_quote_map.get(new_code)
            if meta and meta.get("price") is not None:
                return {
                    "code": new_code,
                    "price": meta["price"],
                    "contract_size": meta["contract_size"],
                    "expire_time": meta.get("expire_time"),
                    "strike_price": meta.get("strike_price"),
                    "option_type": str(meta.get("option_type") or option_type).upper(),
                    "source": f"决策 {rec_code} option_trade_plan",
                }
            premium = safe_float(plan.get("premium_per_share"))
            if premium is not None:
                return {
                    "code": new_code,
                    "price": premium,
                    "contract_size": int(plan.get("contract_size") or SIM_OPTION_CONTRACT_SIZE),
                    "expire_time": plan.get("expire_date"),
                    "strike_price": plan.get("strike_price"),
                    "option_type": option_type,
                    "source": f"决策 {rec_code} option_trade_plan",
                }

    if not underlying or strike is None:
        return None

    try:
        ret, exp_df = quote_ctx.get_option_expiration_date(underlying, IndexOptionType.NORMAL)
        if ret != RET_OK or exp_df is None or exp_df.empty:
            return None

        valid_exps = exp_df[
            (exp_df["option_expiry_date_distance"] >= OPTION_MIN_DAYS)
            & (exp_df["option_expiry_date_distance"] <= OPTION_MAX_DAYS)
        ].sort_values("option_expiry_date_distance")

        for _, exp_row in valid_exps.iterrows():
            expiry = str(exp_row["strike_time"])
            if held_expire and expiry <= held_expire:
                continue
            chain_type = OptionType.CALL if option_type == "CALL" else OptionType.PUT
            ret, chain = quote_ctx.get_option_chain(
                underlying,
                start=expiry,
                end=expiry,
                option_type=chain_type,
            )
            if ret != RET_OK or chain is None or chain.empty:
                continue
            matched = chain[chain["strike_price"] == strike]
            if matched.empty:
                continue
            new_code = str(matched.iloc[0]["code"])
            quoted = fetch_option_quote_map(quote_ctx, [new_code])
            meta = quoted.get(new_code)
            if meta and meta.get("price") is not None:
                option_quote_map[new_code] = meta
                return {
                    "code": new_code,
                    "price": meta["price"],
                    "contract_size": meta["contract_size"],
                    "expire_time": meta.get("expire_time"),
                    "strike_price": meta.get("strike_price"),
                    "option_type": option_type,
                    "source": f"期权链远月 {expiry}",
                }
    except Exception as exc:
        log("ROLL", f"{held_code} 扫描远月失败: {exc}")
    return None
