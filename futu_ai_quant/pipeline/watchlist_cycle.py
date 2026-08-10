"""
自选股分析流水线（无个人持仓 / 成交 / 期权持仓）。

与 ``run_analysis_cycle`` 分离：只依赖行情上下文，不读写实盘账户。
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from futu import OpenQuoteContext
from openai import OpenAI

from futu_ai_quant.analysis.analysts import attach_analyst_signals
from futu_ai_quant.analysis.portfolio import (
    build_portfolio_payload,
    collect_required_codes,
)
from futu_ai_quant.analysis.watchlist_stock import analyze_watchlist_stock
from futu_ai_quant.brokers.futu.quotes import fetch_snapshot_map
from futu_ai_quant.config.watchlist import (
    DEFAULT_DECISIONS_DIR,
    DEFAULT_PAYLOADS_DIR,
    synthetic_watchlist_stock,
    watchlist_assumed_pl_ratio,
    watchlist_slot_uses_intraday,
)
from futu_ai_quant.decision.display import enrich_decision_for_display, format_decision_summary
from futu_ai_quant.decision.rules import build_watchlist_rules_decision
from futu_ai_quant.decision.storage import save_analysis_artifacts
from futu_ai_quant.decision.validation import validate_decision_schema
from futu_ai_quant.llm.settings import llm_provider
from futu_ai_quant.market.symbol_names import resolve_symbol_names
from futu_ai_quant.decision.ai import call_watchlist_llm_decision
from futu_ai_quant.planning.stock import (
    build_stock_trade_plan,
    format_price_band,
    format_watch_triggers,
    overlay_intraday_onto_daily_for_plan,
)
from futu_ai_quant.risk.macro_overlay import attach_macro_risk_overlay
from futu_ai_quant.risk.position_limits import attach_portfolio_risk_limits
from futu_ai_quant.utils.logging import log


def _merge_watchlist_rules_into_ai(
    decision: dict[str, Any],
    stocks: list[dict[str, Any]],
) -> dict[str, Any]:
    """价带/tip 以规则为准；保留 AI 的 reasoning / confidence / action（若合法）。"""
    rules = build_watchlist_rules_decision(stocks)
    rules_by_code = {r["code"]: r for r in rules.get("recommendations", [])}
    for rec in decision.get("recommendations", []):
        if not isinstance(rec, dict):
            continue
        tip_src = rules_by_code.get(str(rec.get("code") or ""), {})
        if not tip_src:
            continue
        # 价带与提示用语统一用规则，避免模型乱报价
        rec["tip"] = tip_src.get("tip") or rec.get("tip")
        rec["action_label"] = tip_src.get("action_label") or rec.get("action_label")
        if tip_src.get("stock_trade_plan"):
            rec["stock_trade_plan"] = tip_src["stock_trade_plan"]
        if tip_src.get("suggested_trigger"):
            rec["suggested_trigger"] = tip_src["suggested_trigger"]
        if tip_src.get("option_trade_plan"):
            rec["option_trade_plan"] = tip_src["option_trade_plan"]
        # action 若模型给了非法值则回退规则
        action = str(rec.get("action") or "").upper()
        if action not in {"BUY", "SELL", "HOLD"}:
            rec["action"] = tip_src.get("action", "HOLD")
            rec["action_label"] = tip_src.get("action_label")
            rec["tip"] = tip_src.get("tip")
    decision["analysis_mode"] = "watchlist"
    return decision


def _resolve_watchlist_decision(
    *,
    use_ai: bool,
    ai_client: OpenAI | None,
    payload: dict[str, Any],
    stocks: list[dict[str, Any]],
    required_codes: list[str],
    stocks_by_code: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    if not use_ai:
        log("规则", f"自选规则引擎生成 {len(required_codes)} 条观察建议...")
        decision = build_watchlist_rules_decision(stocks)
        decision = validate_decision_schema(decision, required_codes, stocks_by_code)
        decision["analysis_mode"] = "watchlist"
        return decision, "rules"

    if ai_client is None:
        raise RuntimeError("use_ai=True 但未提供 LLM 客户端")

    log("模型", f"开始调用 LLM（自选观察），共 {len(required_codes)} 只...")
    try:
        decision = call_watchlist_llm_decision(ai_client, payload)
        decision = validate_decision_schema(decision, required_codes, stocks_by_code)
        decision = _merge_watchlist_rules_into_ai(decision, stocks)
        return decision, llm_provider()
    except Exception as exc:
        log("模型", f"LLM 决策失败，降级自选规则引擎: {exc}")
        decision = build_watchlist_rules_decision(stocks)
        decision = validate_decision_schema(decision, required_codes, stocks_by_code)
        decision["analysis_mode"] = "watchlist"
        return decision, "rules_fallback"


def _rebuild_watchlist_trade_plans(
    stocks: list[dict[str, Any]],
    snapshot_map: dict[str, dict[str, Any]],
) -> None:
    for stock in stocks:
        daily = stock.get("daily") or {}
        intraday = stock.get("intraday") or {}
        use_intraday = bool(stock.get("use_intraday")) and not intraday.get("error")
        enriched = stock
        if use_intraday and intraday.get("atr") is not None:
            enriched = {
                **stock,
                "daily": overlay_intraday_onto_daily_for_plan(daily, intraday),
            }
        stock["stock_trade_plan"] = build_stock_trade_plan(
            enriched,
            stock.get("swing_strategy") or {},
            stock.get("combined_swing_signal") or {},
            snapshot_map.get(stock["code"]),
            stock.get("pnl") or {},
        )


def run_watchlist_cycle(
    quote_ctx: OpenQuoteContext,
    codes: list[str],
    ai_client: OpenAI | None,
    *,
    use_ai: bool = True,
    print_decision: bool = True,
    save_decision: bool = True,
    save_payload: bool = True,
    slot_key: str | None = None,
    slot_label_text: str | None = None,
    decisions_dir: Path | None = None,
    payloads_dir: Path | None = None,
) -> dict[str, Any] | None:
    """
    对自选代码跑一轮技术/规则（或 LLM）决策，不含个人交易数据。

    Parameters
    ----------
    codes :
        已规范化的标的列表，如 ``HK.00700``。
    slot_key / slot_label_text :
        可选推送时段标记（auction / lunch / preclose）。
        lunch/preclose 默认启用盘中 30 分钟 K 以提高灵敏度。
    """
    if not codes:
        raise ValueError("自选股列表为空")

    assumed_pl = watchlist_assumed_pl_ratio()
    stocks_raw = [
        synthetic_watchlist_stock(code, assumed_pl_ratio=assumed_pl) for code in codes
    ]
    use_intraday = watchlist_slot_uses_intraday(slot_key)
    mode_note = "盘中30m主导" if use_intraday else "日K/周K"
    log(
        "自选",
        f"开始分析 {len(stocks_raw)} 只标的（无持仓，{mode_note}）: {', '.join(codes)}",
    )

    snapshot_map = fetch_snapshot_map(quote_ctx, codes)

    stocks: list[dict[str, Any]] = []
    for stock in stocks_raw:
        enriched = analyze_watchlist_stock(
            quote_ctx,
            stock,
            snapshot_map.get(stock["code"]),
            slot_key=slot_key,
        )
        stocks.append(enriched)
        tier = (enriched.get("swing_strategy") or {}).get("loss_tier", "?")
        pnl = enriched.get("pnl") or {}
        daily = enriched.get("daily") or {}
        weekly = enriched.get("weekly") or {}
        intraday = enriched.get("intraday") or {}
        combined = enriched.get("combined_swing_signal") or {}
        if enriched.get("indicator_error"):
            log("指标", f"{stock['code']} 部分指标失败: {enriched['indicator_error']}")
        intra_txt = ""
        if enriched.get("use_intraday"):
            intra_txt = f" 盘中={intraday.get('swing_signal')}"
        log(
            "指标",
            f"{stock['code']} [{tier}] "
            f"现价={pnl.get('market_price')} "
            f"日K={daily.get('swing_signal')} 周K={weekly.get('swing_signal')}"
            f"{intra_txt} "
            f"有效信号={combined.get('effective_signal')}"
            + (f" ({combined.get('signal_note')})" if combined.get("signal_note") else ""),
        )
        trade = enriched.get("stock_trade_plan") or {}
        if trade.get("direction") != "none":
            band = format_price_band(
                trade.get("trigger_price_low"),
                trade.get("trigger_price_high"),
                trade.get("preferred_trigger_price"),
            )
            log(
                "计划",
                f"{stock['code']} 建议{trade.get('direction')} 触发 {band}",
            )
        else:
            watch_text = format_watch_triggers(trade)
            if watch_text:
                log("计划", f"{stock['code']} 观望参考 {watch_text}")

    log("风控", "计算波动率与相关性动态上限...")
    dynamic_risk = attach_portfolio_risk_limits(stocks)

    log("宏观", "评估恒指/黄金/FOMC 宏观风险...")
    macro_risk = attach_macro_risk_overlay(quote_ctx, stocks)
    if macro_risk.get("risk_level") not in (None, "normal"):
        log("宏观", macro_risk.get("summary", "宏观风险收紧波段上限"))

    _rebuild_watchlist_trade_plans(stocks, snapshot_map)

    # 自选模式：无期权持仓、无成交史
    options: list[dict[str, Any]] = []
    log("分析师", "生成规则化虚拟分析师信号...")
    analyst_summary = attach_analyst_signals(stocks)
    payload = build_portfolio_payload(
        stocks,
        options,
        dynamic_risk=dynamic_risk,
        analyst_summary=analyst_summary,
        macro_risk=macro_risk,
    )
    # 标记为自选，避免与持仓分析混淆
    payload["analysis_mode"] = "watchlist"
    payload["watchlist_slot"] = slot_key
    if isinstance(payload.get("summary"), dict):
        payload["summary"]["analysis_mode"] = "watchlist"
        payload["summary"]["personal_positions"] = False
        payload["summary"]["use_intraday"] = use_intraday
        if slot_label_text:
            payload["summary"]["watchlist_slot"] = slot_label_text

    required_codes = collect_required_codes(payload)
    stocks_by_code = {s["code"]: s for s in stocks}
    options_by_code: dict[str, dict[str, Any]] = {}

    log("名称", "加载股票中英文名称缓存...")
    symbol_names = resolve_symbol_names(quote_ctx, required_codes, position_names={})

    try:
        decision, decision_source = _resolve_watchlist_decision(
            use_ai=use_ai,
            ai_client=ai_client,
            payload=payload,
            stocks=stocks,
            required_codes=required_codes,
            stocks_by_code=stocks_by_code,
        )
        decision["analysis_mode"] = "watchlist"
        decision = enrich_decision_for_display(
            decision,
            stocks_by_code=stocks_by_code,
            options_by_code=options_by_code,
            symbol_names=symbol_names,
        )
        if slot_label_text:
            risk = str(decision.get("portfolio_risk_summary") or "").strip()
            prefix = f"【自选·{slot_label_text}】"
            decision["portfolio_risk_summary"] = f"{prefix}{risk}" if risk else prefix

        out_decisions = decisions_dir or DEFAULT_DECISIONS_DIR
        out_payloads = payloads_dir or DEFAULT_PAYLOADS_DIR
        saved_path: Path | None = None
        payload_saved_path: Path | None = None
        if save_decision and save_payload:
            payload_saved_path, saved_path = save_analysis_artifacts(
                payload,
                decision,
                required_codes=required_codes,
                decision_source=decision_source,
                payloads_dir=out_payloads,
                decisions_dir=out_decisions,
            )
            log("输入", f"模型输入已保存: {payload_saved_path}")
            log("决策", f"决策已保存: {saved_path}")
        elif save_decision:
            from futu_ai_quant.decision.storage import save_decision_record

            saved_path = save_decision_record(
                decision,
                required_codes=required_codes,
                payload_summary=payload.get("summary"),
                decisions_dir=out_decisions,
            )
            log("决策", f"决策已保存: {saved_path}")

        if print_decision:
            title_map = {
                "rules": "自选规则决策",
                "rules_fallback": "自选规则决策（LLM 降级）",
            }
            title = title_map.get(decision_source, f"自选 {decision_source.upper()} 决策")
            if slot_label_text:
                title = f"{title} · {slot_label_text}"
            print(f"\n===== {title} =====")
            print(format_decision_summary(decision))
            print(f"\n===== 覆盖 {len(decision['recommendations'])}/{len(required_codes)} 只自选 =====\n")

        return {
            "analyzed_at": datetime.now().astimezone().isoformat(),
            "decision": decision,
            "required_codes": required_codes,
            "stocks_by_code": stocks_by_code,
            "payload": payload,
            "payload_summary": payload.get("summary"),
            "saved_path": str(saved_path) if saved_path else None,
            "payload_saved_path": str(payload_saved_path) if payload_saved_path else None,
            "decision_source": decision_source,
            "slot_key": slot_key,
            "slot_label": slot_label_text,
            "analysis_mode": "watchlist",
        }
    except json.JSONDecodeError as exc:
        log("决策", f"JSON 解析失败: {exc}")
    except Exception as exc:
        log("决策", f"自选决策生成失败: {exc}")
        traceback.print_exc()
    return None
