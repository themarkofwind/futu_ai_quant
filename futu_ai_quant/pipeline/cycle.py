"""
分析主流程编排。

核心 API
--------
``run_analysis_cycle``：执行完整的一轮持仓分析，被 ``cli.analyze`` 与 ``sim.runner``（source=main）调用。

单轮步骤见各阶段 log 标签：持仓 → 分类 → 成交 → 指标 → 期权 → 模型/规则 → 决策。
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any

from futu import RET_OK, OpenQuoteContext, OpenSecTradeContext
from openai import OpenAI

from futu_ai_quant.analysis.portfolio import (
    attach_stock_option_context,
    build_portfolio_payload,
    collect_required_codes,
)
from futu_ai_quant.analysis.stock import compute_stock_indicators
from futu_ai_quant.brokers.futu.options import fetch_option_metrics
from futu_ai_quant.brokers.futu.positions import get_position_list
from futu_ai_quant.brokers.futu.quotes import fetch_snapshot_map
from futu_ai_quant.config.settings import TRADE_RECENT_SWING_DAYS
from futu_ai_quant.decision.ai import call_deepseek
from futu_ai_quant.decision.rules import build_rules_decision
from futu_ai_quant.decision.storage import save_analysis_artifacts
from futu_ai_quant.decision.validation import validate_decision_schema
from futu_ai_quant.domain.positions import classify_positions
from futu_ai_quant.history.trades import attach_trade_history_to_stocks, load_ytd_trade_history
from futu_ai_quant.utils.logging import log


def _resolve_decision(
    *,
    use_ai: bool,
    ai_client: OpenAI | None,
    payload: dict[str, Any],
    stocks: list[dict[str, Any]],
    options: list[dict[str, Any]],
    required_codes: list[str],
    stocks_by_code: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    """生成并校验决策；AI 失败时自动降级为规则引擎。"""
    if not use_ai:
        log("规则", f"跳过 DeepSeek，使用规则引擎生成 {len(required_codes)} 条建议...")
        decision = build_rules_decision(stocks, options)
        return validate_decision_schema(decision, required_codes, stocks_by_code), "rules"

    if ai_client is None:
        raise RuntimeError("use_ai=True 但未提供 DeepSeek 客户端")

    log("模型", f"开始调用 DeepSeek，需为 {len(required_codes)} 个持仓逐一生成建议...")
    try:
        decision = call_deepseek(ai_client, payload)
        decision = validate_decision_schema(decision, required_codes, stocks_by_code)
        return decision, "deepseek"
    except Exception as exc:
        log("模型", f"DeepSeek 决策失败，降级规则引擎: {exc}")
        decision = build_rules_decision(stocks, options)
        decision = validate_decision_schema(decision, required_codes, stocks_by_code)
        return decision, "rules_fallback"


def run_analysis_cycle(
    quote_ctx: OpenQuoteContext,
    trade_ctx: OpenSecTradeContext,
    ai_client: OpenAI | None,
    *,
    use_ai: bool = True,
    print_decision: bool = True,
    save_decision: bool = True,
    save_payload: bool = True,
) -> dict[str, Any] | None:
    """
    执行一轮完整的持仓量化分析与决策生成。

    Parameters
    ----------
    quote_ctx :
        Futu ``OpenQuoteContext``，用于 K 线、快照、期权链/报价。
    trade_ctx :
        Futu ``OpenSecTradeContext``，用于持仓与历史成交查询。
    ai_client :
        ``use_ai=True`` 时必填，OpenAI 兼容客户端（指向 DeepSeek）。
    use_ai :
        True 调用 ``decision.ai.call_deepseek``；False 使用 ``decision.rules``。
    print_decision :
        是否将 JSON 决策打印到 stdout。
    save_decision :
        是否写入 ``data/decisions/``（含 ``latest.json``）。
    save_payload :
        是否写入 ``data/payloads/``（含 ``latest_payload.json``），
        保存发给大模型/规则引擎的完整 ``portfolio_payload``。

    Returns
    -------
    dict | None
        成功时含 ``decision``、``required_codes``、``saved_path`` 等；
        校验/解析失败时返回 None。

    调用的主要内部模块
    ------------------
    - ``get_position_list`` → Futu ``position_list_query``
    - ``compute_stock_indicators`` → K 线 + 卖权扫描 + 交易计划
    - ``load_ytd_trade_history`` → 成交缓存 + ``history_deal_list_query``
    - ``fetch_option_metrics`` → ``get_option_quote``
    - ``call_deepseek`` → DeepSeek Chat Completions API
    """
    log("持仓", "开始拉取港股持仓...")
    ret, positions = get_position_list(trade_ctx)
    if ret != RET_OK:
        raise RuntimeError(f"持仓拉取失败: {positions}")
    log("持仓", f"原始持仓 {len(positions)} 条")

    stocks_raw, options_raw = classify_positions(positions, quote_ctx)
    log("分类", f"正股 {len(stocks_raw)} 个，期权 {len(options_raw)} 个")

    stock_codes = [s["code"] for s in stocks_raw]
    snapshot_map = fetch_snapshot_map(quote_ctx, stock_codes)

    log("成交", "加载当年历史成交（本地缓存优先）...")
    ytd_deals = load_ytd_trade_history(trade_ctx)

    log("指标", "开始计算正股盈亏、日K/周K波段指标与卖权候选...")
    stocks: list[dict[str, Any]] = []
    for stock in stocks_raw:
        enriched = compute_stock_indicators(
            quote_ctx,
            stock,
            snapshot_map.get(stock["code"]),
        )
        stocks.append(enriched)
        tier = (enriched.get("swing_strategy") or {}).get("loss_tier", "?")
        pnl = enriched.get("pnl") or {}
        daily = enriched.get("daily") or {}
        weekly = enriched.get("weekly") or {}
        combined = enriched.get("combined_swing_signal") or {}
        overlay = enriched.get("option_overlay") or {}

        if enriched.get("indicator_error"):
            log("指标", f"{stock['code']} 部分指标失败: {enriched['indicator_error']}")
        log(
            "指标",
            f"{stock['code']} [{tier}] "
            f"现价={pnl.get('market_price')} 盈亏={pnl.get('pl_ratio')}% "
            f"日K={daily.get('swing_signal')} 周K={weekly.get('swing_signal')} "
            f"MACD={daily.get('macd_bias')} 量比={daily.get('volume_ratio')} "
            f"有效信号={combined.get('effective_signal')} "
            f"(主={combined.get('primary_signal')}/次={combined.get('secondary_signal')})"
            + (f" {combined.get('signal_note')}" if combined.get("signal_note") else ""),
        )
        call_count = len(overlay.get("sell_call_candidates") or [])
        put_count = len(overlay.get("sell_put_candidates") or [])
        if call_count or put_count:
            log("卖权", f"{stock['code']} 候选 Call={call_count} Put={put_count}")
        elif overlay.get("scan_note"):
            log("卖权", f"{stock['code']} {overlay['scan_note']}")

        trade = enriched.get("stock_trade_plan") or {}
        opt_plan = enriched.get("option_trade_plan")
        if trade.get("direction") != "none":
            atr_note = (
                f" ATR={trade.get('atr_used')}" if trade.get("atr_used") is not None else ""
            )
            log(
                "仓位",
                f"{stock['code']} 每手{trade.get('lot_size')}股 "
                f"建议{trade.get('direction')} {trade.get('suggested_lots')}手"
                f"({trade.get('suggested_qty')}股) "
                f"触发价 {trade.get('trigger_price_low')}-{trade.get('trigger_price_high')}"
                f"{atr_note}",
            )
        elif trade.get("trade_note"):
            log("仓位", f"{stock['code']} 每手{trade.get('lot_size')}股 {trade['trade_note']}")
        if opt_plan:
            log("仓位", f"{stock['code']} 期权方案: {opt_plan.get('label')}")

    attach_trade_history_to_stocks(stocks, ytd_deals)
    for stock in stocks:
        hist = stock.get("trade_history") or {}
        recent = hist.get("recent_swing_window") or {}
        ytd = hist.get("ytd_summary") or {}
        if recent.get("stock_trade_count") or recent.get("option_trade_count") or ytd.get("trade_count"):
            log(
                "成交",
                f"{stock['code']} 当年正股{ytd.get('trade_count', 0)}笔 "
                f"近{hist.get('recent_swing_days', TRADE_RECENT_SWING_DAYS)}日"
                f"正股{recent.get('stock_trade_count', 0)}笔/期权{recent.get('option_trade_count', 0)}笔"
                + (f" | {hist.get('swing_hint')}" if hist.get("swing_hint") else ""),
            )

    log("期权", "开始拉取期权 IV / Greeks...")
    options = fetch_option_metrics(quote_ctx, options_raw)
    for opt in options:
        if opt.get("quote_error"):
            log("期权", f"{opt['code']} 行情失败: {opt['quote_error']}")
        else:
            log(
                "期权",
                f"{opt['code']} [{opt.get('position_direction')}] "
                f"price={opt.get('last_price')} iv={opt.get('implied_volatility')} "
                f"delta={opt.get('delta')} theta={opt.get('theta')}",
            )

    attach_stock_option_context(stocks, options)
    payload = build_portfolio_payload(stocks, options)
    required_codes = collect_required_codes(payload)
    stocks_by_code = {s["code"]: s for s in stocks}

    if save_payload and not save_decision:
        from futu_ai_quant.decision.storage import save_portfolio_payload_record

        payload_saved_path = save_portfolio_payload_record(
            payload,
            required_codes=required_codes,
            decision_source="deepseek" if use_ai else "rules",
        )
        log("输入", f"模型输入已保存: {payload_saved_path}")
    else:
        payload_saved_path = None

    try:
        decision, decision_source = _resolve_decision(
            use_ai=use_ai,
            ai_client=ai_client,
            payload=payload,
            stocks=stocks,
            options=options,
            required_codes=required_codes,
            stocks_by_code=stocks_by_code,
        )
        saved_path: Path | None = None
        if save_decision:
            if save_payload:
                payload_path, saved_path = save_analysis_artifacts(
                    payload,
                    decision,
                    required_codes=required_codes,
                    decision_source=decision_source,
                )
                payload_saved_path = payload_path
                log("输入", f"模型输入已保存: {payload_path}")
            else:
                from futu_ai_quant.decision.storage import save_decision_record

                saved_path = save_decision_record(
                    decision,
                    required_codes=required_codes,
                    payload_summary=payload.get("summary"),
                )
            log("决策", f"决策已保存: {saved_path}")
        if print_decision:
            title_map = {
                "deepseek": "DeepSeek 交易决策 JSON",
                "rules": "规则引擎交易决策 JSON",
                "rules_fallback": "规则引擎交易决策 JSON（DeepSeek 降级）",
            }
            title = title_map.get(decision_source, "交易决策 JSON")
            print(f"\n===== {title} =====")
            print(json.dumps(decision, ensure_ascii=False, indent=2))
            print(
                f"===== 建议覆盖 {len(decision['recommendations'])}/{len(required_codes)} 个持仓 =====\n"
            )
        return {
            "decision": decision,
            "required_codes": required_codes,
            "stocks_by_code": stocks_by_code,
            "payload": payload,
            "payload_summary": payload.get("summary"),
            "saved_path": str(saved_path) if saved_path else None,
            "payload_saved_path": str(payload_saved_path) if payload_saved_path else None,
            "decision_source": decision_source,
        }
    except json.JSONDecodeError as exc:
        log("决策", f"JSON 解析失败: {exc}")
    except Exception as exc:
        log("决策", f"决策生成失败: {exc}")
        traceback.print_exc()
    return None
