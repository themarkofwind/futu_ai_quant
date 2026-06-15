"""
模拟交易单轮编排。

``run_sim_cycle`` 流程：加载决策 → 拉行情 → 处理挂单 → 执行 recommendations
→ 市值计价 → 写入 snapshots/metrics。

``init_mirror_portfolio``：从实盘持仓初始化 PaperPortfolio（调用 position_list_query）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from futu import RET_OK, Currency, OpenQuoteContext, TrdEnv

from futu_ai_quant.brokers.futu.positions import get_position_list
from futu_ai_quant.domain.positions import classify_positions, is_option_code
from futu_ai_quant.market.triggers import price_in_trigger
from futu_ai_quant.pipeline.cycle import run_analysis_cycle
from futu_ai_quant.sim.engine import LocalSimEngine
from futu_ai_quant.sim.io import load_decision_record, save_snapshot
from futu_ai_quant.sim.market_data import fetch_market_data, mark_to_market
from futu_ai_quant.sim.options import (
    fetch_option_quote_map,
    find_roll_open_leg,
    resolve_contract_size,
)
from futu_ai_quant.sim.portfolio import PaperPortfolio
from futu_ai_quant.sim.settings import SIM_INITIAL_CASH
from futu_ai_quant.utils.logging import log
from futu_ai_quant.utils.numbers import safe_float


def process_pending_orders(
    engine: LocalSimEngine,
    portfolio: PaperPortfolio,
    prices: dict[str, float],
    decision_id: str,
) -> int:
    executed = 0
    for order in list(portfolio.data["pending_orders"]):
        code = order["code"]
        price = prices.get(code)
        low = safe_float(order.get("trigger_low"))
        high = safe_float(order.get("trigger_high"))
        if not price_in_trigger(price, low, high):
            continue
        side = order["side"]
        qty = int(order["qty"])
        if order.get("asset_type") == "stock":
            trade = engine.execute_stock(
                code=code,
                name=order.get("name", ""),
                side=side,
                qty=qty,
                price=price or 0.0,
                lot_size=int(order.get("lot_size") or 100),
                decision_id=decision_id,
                reason=f"触发挂单 {order.get('id')}",
            )
            if trade:
                portfolio.remove_pending(order["id"])
                executed += 1
    return executed


def apply_recommendations(
    engine: LocalSimEngine,
    portfolio: PaperPortfolio,
    decision: dict[str, Any],
    prices: dict[str, float],
    option_quote_map: dict[str, dict[str, Any]],
    decision_id: str,
    quote_ctx: OpenQuoteContext,
) -> dict[str, int]:
    counters = {"executed": 0, "pending": 0, "skipped": 0}
    for rec in decision.get("recommendations", []):
        code = str(rec.get("code", ""))
        if not code:
            continue

        stock_plan = rec.get("stock_trade_plan") or {}
        option_plan = rec.get("option_trade_plan") or {}
        action = str(rec.get("action", "HOLD")).upper()
        name = str(rec.get("name", ""))

        if not is_option_code(code):
            direction = str(stock_plan.get("direction", "none")).lower()
            qty = int(stock_plan.get("suggested_qty") or 0)
            lot_size = int(stock_plan.get("lot_size") or 100)
            trigger_low = safe_float(stock_plan.get("trigger_price_low"))
            trigger_high = safe_float(stock_plan.get("trigger_price_high"))
            price = prices.get(code)

            should_exec, reason = engine.should_execute_now(
                action, direction, qty, trigger_low, trigger_high, price
            )
            if qty > 0 and direction in ("buy", "sell"):
                if should_exec and price is not None:
                    trade = engine.execute_stock(
                        code=code,
                        name=name,
                        side=direction,
                        qty=qty,
                        price=price,
                        lot_size=lot_size,
                        decision_id=decision_id,
                        reason=reason,
                    )
                    if trade:
                        counters["executed"] += 1
                    else:
                        counters["skipped"] += 1
                else:
                    pending = {
                        "id": str(uuid.uuid4()),
                        "code": code,
                        "name": name,
                        "asset_type": "stock",
                        "side": direction,
                        "qty": qty,
                        "lot_size": lot_size,
                        "trigger_low": trigger_low,
                        "trigger_high": trigger_high,
                        "created_at": datetime.now().isoformat(),
                        "decision_id": decision_id,
                        "note": reason,
                    }
                    portfolio.replace_pending_for_code(code, [pending])
                    counters["pending"] += 1
                    log("挂单", f"{code} {direction} {qty}股 触发区间 {trigger_low}-{trigger_high}")
            else:
                counters["skipped"] += 1

            opt_action = str(option_plan.get("action", "none")).lower()
            contracts = int(option_plan.get("contracts") or 0)
            contract_code = str(option_plan.get("contract_code") or "")
            plan_source = str(option_plan.get("plan_source", "")).lower()
            if opt_action in ("sell_call", "sell_put") and contracts > 0 and contract_code:
                if plan_source in ("suggested_skipped", "none"):
                    counters["skipped"] += 1
                else:
                    existing = portfolio.get_option(contract_code)
                    if existing and int(existing.get("qty") or 0) < 0:
                        counters["skipped"] += 1
                    else:
                        if opt_action == "sell_call":
                            stock_pos = portfolio.get_stock(code)
                            held = int(stock_pos["qty"]) if stock_pos else 0
                            contract_size = resolve_contract_size(
                                contract_code, option_quote_map, portfolio
                            )
                            max_cover = held // contract_size if contract_size else 0
                            if max_cover <= 0:
                                counters["skipped"] += 1
                            else:
                                contracts = min(contracts, max_cover)
                                premium = prices.get(contract_code)
                                if premium is None:
                                    premium = safe_float(option_plan.get("premium_per_share"))
                                if premium is None:
                                    counters["skipped"] += 1
                                else:
                                    contract_size = resolve_contract_size(
                                        contract_code, option_quote_map, portfolio
                                    )
                                    trade = engine.execute_option_short(
                                        code=contract_code,
                                        name=name,
                                        contracts=contracts,
                                        price=premium,
                                        contract_size=contract_size,
                                        decision_id=decision_id,
                                        reason=f"{code} 配套 {opt_action}（正股 action={action}）",
                                        action=opt_action,
                                    )
                                    if trade:
                                        counters["executed"] += 1
                        else:
                            premium = prices.get(contract_code)
                            if premium is None:
                                premium = safe_float(option_plan.get("premium_per_share"))
                            if premium is None:
                                counters["skipped"] += 1
                            else:
                                contract_size = resolve_contract_size(
                                    contract_code, option_quote_map, portfolio
                                )
                                trade = engine.execute_option_short(
                                    code=contract_code,
                                    name=name,
                                    contracts=contracts,
                                    price=premium,
                                    contract_size=contract_size,
                                    decision_id=decision_id,
                                    reason=f"{code} 配套 {opt_action}（正股 action={action}）",
                                    action=opt_action,
                                )
                                if trade:
                                    counters["executed"] += 1
        else:
            opt_action = str(option_plan.get("action", "none")).lower()
            contracts = int(option_plan.get("contracts") or 0)
            held_pos = portfolio.get_option(code) or {}
            if contracts <= 0:
                contracts = abs(int(held_pos.get("qty") or 0)) or 1
            price = prices.get(code) or safe_float(option_plan.get("premium_per_share"))
            close_contract_size = resolve_contract_size(code, option_quote_map, portfolio)
            held_meta = {
                **(option_quote_map.get(code) or {}),
                "strike_price": held_pos.get("strike_price") or option_quote_map.get(code, {}).get("strike_price"),
                "expire_time": held_pos.get("expire_time") or option_quote_map.get(code, {}).get("expire_time"),
                "option_type": held_pos.get("option_type") or option_quote_map.get(code, {}).get("option_type"),
                "stock_owner": held_pos.get("stock_owner") or option_quote_map.get(code, {}).get("stock_owner"),
            }
            if action == "ROLL" or opt_action == "roll":
                if price is None:
                    counters["skipped"] += 1
                    continue
                open_leg = find_roll_open_leg(
                    quote_ctx, code, held_meta, decision, option_quote_map
                )
                if open_leg:
                    roll_result = engine.execute_option_roll(
                        held_code=code,
                        name=name,
                        contracts=contracts,
                        close_price=price,
                        close_contract_size=close_contract_size,
                        open_leg=open_leg,
                        decision_id=decision_id,
                    )
                    if roll_result:
                        counters["executed"] += 1
                    else:
                        counters["skipped"] += 1
                else:
                    trade = engine.execute_option_close(
                        code=code,
                        name=name,
                        contracts=contracts,
                        price=price,
                        contract_size=close_contract_size,
                        decision_id=decision_id,
                        reason="ROLL 仅平仓（未找到远月合约）",
                    )
                    if trade:
                        counters["executed"] += 1
                    else:
                        counters["skipped"] += 1
            elif opt_action == "close" and price is not None:
                trade = engine.execute_option_close(
                    code=code,
                    name=name,
                    contracts=contracts,
                    price=price,
                    contract_size=close_contract_size,
                    decision_id=decision_id,
                    reason="建议平仓",
                )
                if trade:
                    counters["executed"] += 1
                else:
                    counters["skipped"] += 1
            else:
                counters["skipped"] += 1

    return counters


def init_mirror_portfolio(
    quote_ctx: OpenQuoteContext,
    trade_ctx: Any,
    portfolio: PaperPortfolio,
) -> None:
    ret, positions = get_position_list(trade_ctx)
    if ret != RET_OK:
        raise RuntimeError(f"拉取真实持仓失败: {positions}")
    stocks, options = classify_positions(positions, quote_ctx)
    option_codes = [opt["code"] for opt in options]
    option_quote_map = fetch_option_quote_map(quote_ctx, option_codes)
    cash = SIM_INITIAL_CASH
    try:
        ret, acc = trade_ctx.accinfo_query(trd_env=TrdEnv.REAL, currency=Currency.HKD)
        if ret == RET_OK and acc is not None and not acc.empty:
            cash = safe_float(acc.iloc[0].get("cash")) or SIM_INITIAL_CASH
    except Exception as exc:
        log("初始化", f"读取真实现金失败，使用默认 {SIM_INITIAL_CASH}: {exc}")
    portfolio.init_from_mirror(stocks, options, cash, option_quote_map)


def collect_price_codes(portfolio: PaperPortfolio, decision: dict[str, Any]) -> list[str]:
    codes = set(portfolio.data["stocks"]) | set(portfolio.data["options"])
    for rec in decision.get("recommendations", []):
        code = rec.get("code")
        if code:
            codes.add(str(code))
        option_plan = rec.get("option_trade_plan") or {}
        contract_code = option_plan.get("contract_code")
        if contract_code:
            codes.add(str(contract_code))
    for order in portfolio.data["pending_orders"]:
        if order.get("code"):
            codes.add(str(order["code"]))
    return sorted(codes)


def enrich_option_quote_map_for_decision(
    quote_ctx: OpenQuoteContext,
    decision: dict[str, Any],
    option_quote_map: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    extra_codes: list[str] = []
    for rec in decision.get("recommendations", []):
        plan = rec.get("option_trade_plan") or {}
        contract_code = plan.get("contract_code")
        if contract_code and contract_code not in option_quote_map:
            extra_codes.append(str(contract_code))
        code = rec.get("code")
        if code and is_option_code(str(code)) and code not in option_quote_map:
            extra_codes.append(str(code))
    if extra_codes:
        option_quote_map.update(fetch_option_quote_map(quote_ctx, extra_codes))
    return option_quote_map


def run_sim_cycle(
    quote_ctx: OpenQuoteContext,
    portfolio: PaperPortfolio,
    engine: LocalSimEngine,
    *,
    source: str,
    decision_file: str | None,
    trade_ctx: Any | None = None,
    ai_client: Any | None = None,
) -> None:
    """
    执行一轮模拟交易。

    Parameters
    ----------
    source :
        ``latest`` | ``file`` 从 ``data/decisions`` 读决策；
        ``main`` 先调用 ``run_analysis_cycle`` 再撮合。
    decision_file :
        ``source=file`` 时的 JSON 路径。
    trade_ctx / ai_client :
        ``source=main`` 时必填。
    """
    if source == "main":
        if trade_ctx is None or ai_client is None:
            raise ValueError("source=main 需要交易上下文与 AI 客户端")
        result = run_analysis_cycle(
            quote_ctx,
            trade_ctx,
            ai_client,
            print_decision=False,
            save_decision=True,
        )
        if not result:
            raise RuntimeError("main 分析未产生有效决策")
        record = {
            "saved_at": datetime.now().isoformat(),
            "decision": result["decision"],
            "required_codes": result["required_codes"],
            "payload_summary": result.get("payload_summary"),
        }
        decision_id = Path(result["saved_path"]).stem if result.get("saved_path") else "main_live"
    else:
        record, decision_id = load_decision_record(source, decision_file)

    decision = record["decision"]
    portfolio.data["last_decision_id"] = decision_id
    codes = collect_price_codes(portfolio, decision)
    prices, option_quote_map = fetch_market_data(quote_ctx, codes)
    option_quote_map = enrich_option_quote_map_for_decision(
        quote_ctx, decision, option_quote_map
    )

    triggered = process_pending_orders(engine, portfolio, prices, decision_id)
    if triggered:
        log("模拟", f"触发成交挂单 {triggered} 笔")

    counters = apply_recommendations(
        engine,
        portfolio,
        decision,
        prices,
        option_quote_map,
        decision_id,
        quote_ctx,
    )
    prices, option_quote_map = fetch_market_data(
        quote_ctx, collect_price_codes(portfolio, decision)
    )
    mtm = mark_to_market(portfolio, prices, option_quote_map)
    save_snapshot(portfolio, mtm, decision_id, counters)
    portfolio.save()

    log(
        "汇总",
        f"成交={counters['executed']} 挂单={counters['pending']} 跳过={counters['skipped']} "
        f"净值={mtm['total_nav']:.2f} 现金={mtm['cash_hkd']:.2f} "
        f"浮盈={mtm['total_unrealized_pnl']:.2f} 已实现={mtm['realized_pnl']:.2f}",
    )
