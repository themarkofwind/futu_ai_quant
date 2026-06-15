#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
港股持仓量化分析常驻脚本：
连接 Futu OpenD，拉取正股/期权持仓，计算技术指标与 Greeks，并调用 DeepSeek 输出 JSON 交易建议。

用法：
  python main.py          # 常驻循环，按交易时段自动调节间隔
  python main.py --once   # 只跑一轮后退出
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pandas_ta as ta
from dotenv import load_dotenv
from openai import OpenAI

from futu import (
    AuType,
    IndexOptionType,
    KLType,
    OpenQuoteContext,
    OpenSecTradeContext,
    OptionStrategyLeg,
    OptionType,
    RET_OK,
    StrategyLegAction,
    TrdEnv,
    TrdMarket,
)

# 当前 futu-api 中港股交易上下文为 OpenSecTradeContext(filter_trdmarket=TrdMarket.HK)
OpenHKTradeContext = OpenSecTradeContext

HK_OPTION_CODE_PATTERN = re.compile(r"^[A-Z]+\d{6}[CP]\d+$", re.IGNORECASE)
# ANALYSIS_INTERVAL_SEC=0 表示自动按港股交易时段调节；>0 则使用固定秒数
ANALYSIS_INTERVAL_SEC = int(os.getenv("ANALYSIS_INTERVAL_SEC", "0"))
INTRADAY_INTERVAL_SEC = int(os.getenv("INTRADAY_INTERVAL_SEC", "1800"))   # 交易日盘中默认 30 分钟
OFFHOURS_INTERVAL_SEC = int(os.getenv("OFFHOURS_INTERVAL_SEC", "14400"))  # 非交易时段默认 4 小时
KLINE_COUNT = 60
WEEKLY_KLINE_COUNT = 52
RSI_LENGTH = 14
BOLL_LENGTH = 20
BOLL_STD = 2
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
ATR_LENGTH = 14
VOLUME_MA_LENGTH = 20
VOLUME_CONFIRM_RATIO = 1.2
IV_RANK_HIGH = 70.0
IV_RANK_LOW = 30.0
DEEP_LOSS_THRESHOLD = -50.0
MODERATE_LOSS_THRESHOLD = 0.0
OPTION_MIN_DAYS = 14
OPTION_MAX_DAYS = 45
OPTION_DELTA_MIN = 0.10
OPTION_DELTA_MAX = 0.30
MAX_OPTION_CANDIDATES_EACH_SIDE = 2
DECISIONS_DIR = Path(os.getenv("DECISIONS_DIR", "data/decisions"))

SYSTEM_PROMPT = """你是一位资深港股量化对冲基金经理，精通正股技术面、波段降本与期权卖方策略。

请基于输入的投资组合数据，结合以下港股交易环境给出专业研判：
1. 港股股票交易印花税（卖方单边约 0.13%），日K频繁交易会侵蚀收益；深套仓位应降低日K交易频率；
2. 价格字段必须严格区分：
   - pnl.nominal_price / market_price：持仓未复权现价，用于触发价、回本计算、期权虚实值；
   - daily.technical_close / weekly.technical_close：前复权K线收盘价，仅用于 RSI/布林带技术指标；
   - 禁止混用复权技术价与未复权现价做比较；
3. 分层降本策略（必须遵循 swing_strategy 字段）：
   - deep_loss（亏损>50%）：周K定方向为主，日K仅小仓位(≤10%)波段，优先等待周线支撑；
   - moderate_loss（亏损0~50%）：日K波段降本为主，周K确认大趋势，可配合卖Call收权利金；
   - profitable（盈利或持平）：周K止盈为主，卖Call备兑，不必刻意降本；
4. 正股波段信号解读（综合 RSI、布林带、MACD、成交量、ATR）：
   - daily/weekly 的 swing_signal：BUY_SWING=低吸降本；SELL_SWING=反弹减仓；HOLD=观望；WAIT=方向不明；
   - macd_bias：golden_cross/death_cross 用于确认或否决波段信号；
   - volume_confirmed=true 表示成交量放大（≥20日均量1.2倍），日K信号更可靠；
   - atr 用于动态触发价区间（stock_trade_plan.trigger_price_low/high）；
   - 周K与 dayK 信号冲突时，以 swing_strategy.primary_timeframe 为主；
5. 期权卖方扫描（option_overlay 字段）：
   - sell_call_candidates：反弹时卖出虚值Call收权利金（备兑/增强收益）；
   - iv_rank：当前IV在候选合约中的百分位，≥70 表示IV偏高、卖Call权利金较厚；≤30 表示IV偏低；
   - sell_put_candidates：愿意加仓时，在周线支撑附近卖Put低接（仅 moderate_loss / 现金流充足时）；
   - 深套仓位慎卖Put，避免被动加仓；
6. 期权方向性判定（必须优先使用 position_side、qty、position_direction）：
   - 买入期权：Theta负=买方损耗；卖出期权：Theta负=卖方受益；
   - 严禁将卖出期权按买入逻辑分析；
   - ROLL 对卖方=买回平仓+卖出远月；对买方=卖出平仓+买入远月。

输出要求：
- 必须返回合法 JSON 对象，不要包含 Markdown 代码块或额外说明文字；
- 严格遵循以下 schema：
{
  "portfolio_risk_summary": "涵盖全部持仓的整体风险与降本策略总览",
  "recommendations": [
    {
      "code": "标的代码",
      "name": "标的名称",
      "action": "BUY / SELL / HOLD / ROLL",
      "confidence": 0.90,
      "reasoning": "须结合 loss_tier、日K/周K信号、pnl、trade_plan 给出推导",
      "suggested_trigger": "具体价格触发区间",
      "stock_trade_plan": {
        "direction": "buy / sell / none",
        "suggested_qty": 500,
        "suggested_lots": 5,
        "lot_size": 100,
        "pct_of_holding": 10.0,
        "trigger_price_low": 112.0,
        "trigger_price_high": 115.0
      },
      "option_trade_plan": {
        "action": "sell_call / sell_put / roll / close / none",
        "contract_code": "HK.ALB260629C120000",
        "expire_date": "2026-06-29",
        "strike_price": 120.0,
        "contracts": 1,
        "premium_per_share": 0.85,
        "estimated_total_premium": 425.0
      }
    }
  ]
}
- 正股标的：必须填写 stock_trade_plan（无操作则 direction=none，qty/lots=0）；
- 期权标的或需卖权配合的正股：必须填写 option_trade_plan（无操作则 action=none）；
- option_trade_plan 必须包含完整 contract_code 与 expire_date，禁止仅用 C120000 等缩写；
- 港股正股买卖必须按整手（lot_size）交易，suggested_qty 必须是 lot_size 的整数倍，禁止碎股；
- stock_trade_plan 的 suggested_qty = suggested_lots × lot_size，三者必须自洽；
- 卖 Call 合约数不得超过正股可备兑张数（stock_qty / contract_size）；
- action 仅允许：BUY、SELL、HOLD、ROLL；
- 正股亏损仓位：reasoning 须说明日K还是周K主导，以及是否配合卖Call/Put；
- 已有卖出期权持仓：须按卖方逻辑评估是否 HOLD/ROLL/平仓；
- recommendations 必须覆盖 required_positions 全部标的，长度一致，不可省略；
- 无需调仓亦须输出 HOLD 及完整 reasoning。"""


def log(stage: str, message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{stage}] {message}")


def safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_lot_size(snapshot: dict[str, Any] | None, stock: dict[str, Any] | None = None) -> int:
    """从行情快照读取每手股数，港股交易须按整手下单。"""
    for source in (snapshot, stock):
        if not source:
            continue
        lot_size = safe_float(source.get("lot_size"))
        if lot_size is not None and int(lot_size) > 0:
            return int(lot_size)
    return 100


def calc_full_lot_trade_qty(
    holding_qty: float,
    tradable_qty: float,
    lot_size: int,
    max_pct: float,
    for_sell: bool,
) -> tuple[int, int, str | None]:
    """
    计算整手交易数量。
    返回 (suggested_qty, suggested_lots, note)；不足一手时 suggested_qty=0。
    """
    if lot_size <= 0:
        return 0, 0, "每手股数未知，无法计算整手仓位"

    holding = int(abs(holding_qty))
    tradable = int(abs(tradable_qty))
    max_by_pct = round_down_to_lot(holding * max_pct / 100.0, lot_size)

    if for_sell:
        capacity = min(max_by_pct, round_down_to_lot(tradable, lot_size))
    else:
        capacity = max_by_pct

    if capacity <= 0:
        note = (
            f"按 {max_pct:g}% 波段比例折算不足一手（每手 {lot_size} 股），"
            "为避免碎股暂不自动建议交易"
        )
        return 0, 0, note

    lots = capacity // lot_size
    return capacity, lots, None


def is_hk_trading_session(now: datetime | None = None) -> bool:
    """港股交易时段：周一至五 09:30-12:00、13:00-16:00（本地北京时间）。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    morning = (9 * 60 + 30) <= minutes < (12 * 60)
    afternoon = (13 * 60) <= minutes < (16 * 60)
    return morning or afternoon


def resolve_analysis_interval() -> tuple[int, str]:
    if ANALYSIS_INTERVAL_SEC > 0:
        return ANALYSIS_INTERVAL_SEC, f"固定间隔 {ANALYSIS_INTERVAL_SEC} 秒（.env 手动配置）"
    if is_hk_trading_session():
        return INTRADAY_INTERVAL_SEC, (
            f"港股交易时段，自动间隔 {INTRADAY_INTERVAL_SEC // 60} 分钟"
        )
    return OFFHOURS_INTERVAL_SEC, (
        f"非交易时段，自动间隔 {OFFHOURS_INTERVAL_SEC // 3600} 小时"
    )


def round_down_to_lot(shares: float, lot_size: int) -> int:
    share_count = int(abs(shares))
    if lot_size <= 0:
        return share_count
    return (share_count // lot_size) * lot_size


def build_stock_trade_plan(
    stock: dict[str, Any],
    swing_strategy: dict[str, Any],
    combined_signal: dict[str, Any],
    snapshot: dict[str, Any] | None,
    pnl: dict[str, Any],
) -> dict[str, Any]:
    qty = safe_float(stock.get("qty")) or 0.0
    can_sell = safe_float(stock.get("can_sell_qty")) or qty
    lot_size = resolve_lot_size(snapshot, stock)
    max_pct = float(swing_strategy.get("max_swing_position_pct") or 10)
    market_price = safe_float(pnl.get("market_price"))
    signal = combined_signal.get("primary_signal", "HOLD")

    plan: dict[str, Any] = {
        "current_qty": int(qty),
        "can_sell_qty": int(can_sell),
        "lot_size": lot_size,
        "shares_per_lot": lot_size,
        "current_lots": int(qty // lot_size) if lot_size else 0,
        "can_sell_lots": int(can_sell // lot_size) if lot_size else 0,
        "max_swing_position_pct": max_pct,
        "direction": "none",
        "suggested_qty": 0,
        "suggested_lots": 0,
        "pct_of_holding": 0.0,
        "trigger_price_low": None,
        "trigger_price_high": None,
        "atr_used": None,
        "trade_note": None,
    }

    daily = stock.get("daily") or {}
    atr_market = scale_atr_to_market(
        safe_float(daily.get("atr")),
        safe_float(daily.get("technical_close")),
        market_price,
    )
    if atr_market is not None:
        plan["atr_used"] = atr_market

    if market_price is not None:
        if signal == "SELL_SWING":
            if atr_market is not None:
                plan["trigger_price_low"] = round(market_price + 0.5 * atr_market, 3)
                plan["trigger_price_high"] = round(market_price + 1.5 * atr_market, 3)
            else:
                plan["trigger_price_low"] = round(market_price * 1.01, 3)
                plan["trigger_price_high"] = round(market_price * 1.04, 3)
        elif signal == "BUY_SWING":
            if atr_market is not None:
                plan["trigger_price_low"] = round(market_price - 1.5 * atr_market, 3)
                plan["trigger_price_high"] = round(market_price - 0.5 * atr_market, 3)
            else:
                plan["trigger_price_low"] = round(market_price * 0.96, 3)
                plan["trigger_price_high"] = round(market_price * 0.99, 3)

    if signal == "SELL_SWING" and can_sell >= lot_size:
        suggested_qty, suggested_lots, note = calc_full_lot_trade_qty(
            qty, can_sell, lot_size, max_pct, for_sell=True
        )
        if suggested_qty > 0:
            plan.update(
                {
                    "direction": "sell",
                    "suggested_qty": suggested_qty,
                    "suggested_lots": suggested_lots,
                    "pct_of_holding": round(suggested_qty / abs(qty) * 100, 2) if qty else 0.0,
                    "trade_note": f"建议卖出 {suggested_lots} 手（{suggested_qty} 股，每手 {lot_size} 股）",
                }
            )
        else:
            plan["trade_note"] = note
    elif signal == "BUY_SWING":
        suggested_qty, suggested_lots, note = calc_full_lot_trade_qty(
            qty, qty, lot_size, max_pct, for_sell=False
        )
        if suggested_qty > 0:
            plan.update(
                {
                    "direction": "buy",
                    "suggested_qty": suggested_qty,
                    "suggested_lots": suggested_lots,
                    "pct_of_holding": round(suggested_qty / abs(qty) * 100, 2) if qty else 0.0,
                    "trade_note": f"建议买入 {suggested_lots} 手（{suggested_qty} 股，每手 {lot_size} 股）",
                }
            )
        else:
            plan["trade_note"] = note

    return plan


def build_option_trade_plan_for_stock(
    stock: dict[str, Any],
    option_overlay: dict[str, Any],
    swing_strategy: dict[str, Any],
    combined_signal: dict[str, Any],
) -> dict[str, Any] | None:
    qty = safe_float(stock.get("qty")) or 0.0
    signal = combined_signal.get("primary_signal", "HOLD")
    lot_size = resolve_lot_size(None, stock)
    candidates = option_overlay.get("sell_call_candidates") or []
    put_candidates = option_overlay.get("sell_put_candidates") or []

    if signal in ("SELL_SWING", "HOLD") and candidates and swing_strategy.get("prefer_sell_call"):
        best = candidates[0]
        contract_size = int(safe_float(best.get("contract_size")) or lot_size or 100)
        max_contracts = max(0, int(abs(qty) // lot_size)) if lot_size else 0
        if max_contracts <= 0:
            return None
        contracts = min(max_contracts, 2)
        premium = safe_float(best.get("last_price")) or 0.0
        iv_suffix = (
            f" IV Rank={best.get('iv_rank')}" if best.get("iv_rank") is not None else ""
        )
        return {
            "action": "sell_call",
            "contract_code": best.get("code"),
            "expire_date": best.get("expire_time"),
            "strike_price": best.get("strike_price"),
            "days_to_expiry": best.get("days_to_expiry"),
            "delta": best.get("delta"),
            "contracts": contracts,
            "contract_size": contract_size,
            "shares_per_lot": lot_size,
            "implied_volatility": best.get("implied_volatility"),
            "iv_rank": best.get("iv_rank"),
            "iv_rank_note": best.get("iv_rank_note"),
            "premium_per_share": premium,
            "estimated_total_premium": round(premium * contract_size * contracts, 2),
            "label": (
                f"卖出 {contracts} 张（备兑 {contracts} 手×{lot_size} 股）"
                f"{best.get('expire_time')} 到期 {best.get('strike_price')} Call"
                f"{iv_suffix}"
            ),
        }

    if (
        signal == "BUY_SWING"
        and put_candidates
        and swing_strategy.get("allow_sell_put")
    ):
        best = put_candidates[0]
        contract_size = int(safe_float(best.get("contract_size")) or lot_size or 100)
        contracts = 1
        premium = safe_float(best.get("last_price")) or 0.0
        iv_suffix = (
            f" IV Rank={best.get('iv_rank')}" if best.get("iv_rank") is not None else ""
        )
        return {
            "action": "sell_put",
            "contract_code": best.get("code"),
            "expire_date": best.get("expire_time"),
            "strike_price": best.get("strike_price"),
            "days_to_expiry": best.get("days_to_expiry"),
            "delta": best.get("delta"),
            "contracts": contracts,
            "contract_size": contract_size,
            "implied_volatility": best.get("implied_volatility"),
            "iv_rank": best.get("iv_rank"),
            "iv_rank_note": best.get("iv_rank_note"),
            "premium_per_share": premium,
            "estimated_total_premium": round(premium * contract_size * contracts, 2),
            "label": (
                f"卖出 {contracts} 张 {best.get('expire_time')} 到期 "
                f"{best.get('strike_price')} Put"
                f"{iv_suffix}"
            ),
        }

    return None


def build_option_position_trade_plan(option: dict[str, Any]) -> dict[str, Any]:
    side = resolve_position_side(
        str(option.get("position_side", "")),
        safe_float(option.get("qty")) or 0.0,
    )
    contracts = int(abs(safe_float(option.get("qty")) or 0))
    days = safe_float(option.get("days_to_expiry"))
    plan: dict[str, Any] = {
        "action": "none",
        "contract_code": option.get("code"),
        "expire_date": option.get("expire_time") or option.get("expire_date"),
        "strike_price": option.get("strike_price"),
        "contracts": contracts,
        "premium_per_share": safe_float(option.get("last_price")),
        "estimated_total_premium": None,
        "label": "持有观望",
    }

    if side == "SHORT" and days is not None and days <= 21:
        plan.update(
            {
                "action": "roll",
                "label": (
                    f"距到期 {int(days)} 天，可考虑买回平仓并卖出更远月份同方向合约"
                ),
            }
        )
    elif side == "SHORT":
        plan.update(
            {
                "action": "hold_short",
                "label": "卖方持有，等待时间价值衰减",
            }
        )

    return plan


def get_position_list(trade_ctx: OpenSecTradeContext) -> tuple[int, pd.DataFrame | str]:
    """封装持仓查询，对应设计文档中的 get_position_list。"""
    return trade_ctx.position_list_query(
        trd_env=TrdEnv.REAL,
        position_market=TrdMarket.HK,
        refresh_cache=True,
    )


def resolve_position_side(position_side: str, qty: float) -> str:
    side = str(position_side or "").upper()
    if side in ("LONG", "SHORT"):
        return side
    if qty < 0:
        return "SHORT"
    if qty > 0:
        return "LONG"
    return "UNKNOWN"


def infer_option_right(code: str, option_type: str = "") -> str | None:
    normalized = str(option_type or "").upper()
    if normalized in ("CALL", "PUT"):
        return normalized
    if not code or "." not in code:
        return None
    symbol = code.split(".", 1)[1]
    match = re.search(r"([CP])\d+$", symbol, re.IGNORECASE)
    if not match:
        return None
    return "CALL" if match.group(1).upper() == "C" else "PUT"


def build_position_direction(
    position_side: str,
    qty: float,
    code: str,
    option_type: str = "",
    is_option: bool = False,
) -> str:
    side = resolve_position_side(position_side, qty)
    side_label = {"LONG": "买入", "SHORT": "卖出", "UNKNOWN": "未知方向"}.get(side, "未知方向")

    if not is_option:
        return "买入持仓" if side == "LONG" else "卖空持仓" if side == "SHORT" else "未知方向持仓"

    right = infer_option_right(code, option_type)
    right_label = {"CALL": "Call", "PUT": "Put"}.get(right or "", "期权")
    return f"{side_label}{right_label}"


def enrich_option_context(option: dict[str, Any]) -> dict[str, Any]:
    enriched = {**option}
    qty = safe_float(enriched.get("qty")) or 0.0
    side = resolve_position_side(str(enriched.get("position_side", "")), qty)
    option_type = str(enriched.get("option_type", ""))

    enriched["abs_qty"] = abs(qty)
    enriched["position_side"] = side if side != "UNKNOWN" else enriched.get("position_side", "N/A")
    enriched["position_direction"] = build_position_direction(
        str(enriched.get("position_side", "")),
        qty,
        str(enriched.get("code", "")),
        option_type=option_type,
        is_option=True,
    )

    theta = safe_float(enriched.get("theta"))
    if side == "SHORT":
        enriched["theta_position_effect"] = (
            "空头卖方受益于时间价值衰减；若维持虚值，临近到期权利金有望加速收敛为盈利"
        )
        if theta is not None:
            enriched["theta_daily_benefit_estimate"] = round(abs(theta) * enriched["abs_qty"], 6)
    elif side == "LONG":
        enriched["theta_position_effect"] = (
            "多头买方承担时间价值衰减；临近到期剩余权利金损耗加速，虚值合约面临归零风险"
        )
        if theta is not None:
            enriched["theta_daily_cost_estimate"] = round(abs(theta) * enriched["abs_qty"], 6)
    else:
        enriched["theta_position_effect"] = "持仓方向未知，请结合 position_side 与 qty 判断 Theta 影响"

    return enriched


def is_option_code(code: str) -> bool:
    if not code or "." not in code:
        return False
    symbol = code.split(".", 1)[1]
    if HK_OPTION_CODE_PATTERN.match(symbol):
        return True
    # 兜底：含 C/P 行权标识且长度明显大于普通正股代码
    return bool(re.search(r"[CP]\d{3,}$", symbol)) and len(symbol) > 8


def classify_positions(
    positions: pd.DataFrame,
    quote_ctx: OpenQuoteContext | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    stocks: list[dict[str, Any]] = []
    options: list[dict[str, Any]] = []
    option_valid_map: dict[str, bool] = {}

    if positions.empty:
        return stocks, options

    candidate_codes = positions["code"].tolist()
    if quote_ctx is not None:
        try:
            ret, snapshot = quote_ctx.get_market_snapshot(candidate_codes)
            if ret == RET_OK and not snapshot.empty:
                for _, row in snapshot.iterrows():
                    option_valid_map[row["code"]] = bool(row.get("option_valid", False))
        except Exception as exc:
            log("分类", f"快照辅助分类失败，将仅使用代码规则: {exc}")

    for _, row in positions.iterrows():
        code = str(row.get("code", ""))
        qty = safe_float(row.get("qty")) or 0.0
        if qty == 0:
            continue

        position_side = str(row.get("position_side", "N/A"))
        is_option = option_valid_map.get(code, False) or is_option_code(code)
        item = {
            "code": code,
            "name": str(row.get("stock_name", "")),
            "qty": qty,
            "can_sell_qty": safe_float(row.get("can_sell_qty")),
            "cost_price": safe_float(row.get("cost_price")),
            "nominal_price": safe_float(row.get("nominal_price")),
            "market_val": safe_float(row.get("market_val")),
            "pl_ratio": safe_float(row.get("pl_ratio")),
            "pl_val": safe_float(row.get("pl_val")),
            "today_pl_val": safe_float(row.get("today_pl_val")),
            "position_side": resolve_position_side(position_side, qty),
            "position_type": str(row.get("position_type", "N/A")),
            "strategy_type": str(row.get("strategy_type", "N/A")),
            "position_direction": build_position_direction(
                position_side,
                qty,
                code,
                is_option=is_option,
            ),
        }

        if is_option:
            options.append(enrich_option_context(item))
        else:
            stocks.append(item)

    return stocks, options


def classify_loss_tier(pl_ratio: float | None) -> str:
    if pl_ratio is None:
        return "unknown"
    if pl_ratio < DEEP_LOSS_THRESHOLD:
        return "deep_loss"
    if pl_ratio < MODERATE_LOSS_THRESHOLD:
        return "moderate_loss"
    return "profitable"


def build_swing_strategy_profile(pl_ratio: float | None) -> dict[str, Any]:
    tier = classify_loss_tier(pl_ratio)
    profiles = {
        "deep_loss": {
            "loss_tier": tier,
            "primary_timeframe": "weekly",
            "secondary_timeframe": "daily",
            "guidance": "周K定方向为主，日K仅小仓位波段降本，避免频繁交易",
            "max_swing_position_pct": 10,
            "prefer_sell_call": True,
            "allow_sell_put": False,
        },
        "moderate_loss": {
            "loss_tier": tier,
            "primary_timeframe": "daily",
            "secondary_timeframe": "weekly",
            "guidance": "日K波段降本为主，周K确认趋势，可配合卖Call收权利金",
            "max_swing_position_pct": 20,
            "prefer_sell_call": True,
            "allow_sell_put": True,
        },
        "profitable": {
            "loss_tier": tier,
            "primary_timeframe": "weekly",
            "secondary_timeframe": "daily",
            "guidance": "周K止盈为主，卖Call备兑增强收益，不必刻意降本",
            "max_swing_position_pct": 15,
            "prefer_sell_call": True,
            "allow_sell_put": False,
        },
        "unknown": {
            "loss_tier": tier,
            "primary_timeframe": "weekly",
            "secondary_timeframe": "daily",
            "guidance": "盈亏未知，保守操作，周K定方向",
            "max_swing_position_pct": 10,
            "prefer_sell_call": False,
            "allow_sell_put": False,
        },
    }
    return profiles.get(tier, profiles["unknown"])


def fetch_snapshot_map(
    quote_ctx: OpenQuoteContext,
    codes: list[str],
) -> dict[str, dict[str, Any]]:
    snapshot_map: dict[str, dict[str, Any]] = {}
    if not codes:
        return snapshot_map

    batch_size = 200
    for idx in range(0, len(codes), batch_size):
        batch = codes[idx : idx + batch_size]
        try:
            ret, snapshot = quote_ctx.get_market_snapshot(batch)
            if ret != RET_OK or snapshot is None or snapshot.empty:
                log("快照", f"批量快照失败: {snapshot}")
                continue
            for _, row in snapshot.iterrows():
                snapshot_map[str(row["code"])] = row.to_dict()
        except Exception as exc:
            log("快照", f"快照拉取异常: {exc}")
    return snapshot_map


def enrich_stock_pnl(stock: dict[str, Any], snapshot: dict[str, Any] | None) -> dict[str, Any]:
    nominal = safe_float(stock.get("nominal_price"))
    cost = safe_float(stock.get("cost_price"))
    pl_ratio = safe_float(stock.get("pl_ratio"))

    market_price = nominal
    prev_close = None
    today_change_pct = None
    if snapshot:
        market_price = safe_float(snapshot.get("last_price")) or nominal
        prev_close = safe_float(snapshot.get("prev_close_price"))
        if market_price is not None and prev_close not in (None, 0):
            today_change_pct = round((market_price - prev_close) / prev_close * 100, 2)

    cost_gap_pct = None
    if cost not in (None, 0) and market_price is not None:
        cost_gap_pct = round((cost - market_price) / cost * 100, 2)

    return {
        "nominal_price": nominal,
        "market_price": market_price,
        "cost_price": cost,
        "pl_ratio": pl_ratio,
        "pl_val": safe_float(stock.get("pl_val")),
        "today_pl_val": safe_float(stock.get("today_pl_val")),
        "cost_gap_pct": cost_gap_pct,
        "today_change_pct": today_change_pct,
        "prev_close_price": prev_close,
    }


def _resolve_indicator_columns(frame: pd.DataFrame) -> tuple[str, str, str, str]:
    rsi_col = f"RSI_{RSI_LENGTH}"
    boll_upper_col = f"BBU_{BOLL_LENGTH}_{float(BOLL_STD)}_{float(BOLL_STD)}"
    boll_mid_col = f"BBM_{BOLL_LENGTH}_{float(BOLL_STD)}_{float(BOLL_STD)}"
    boll_lower_col = f"BBL_{BOLL_LENGTH}_{float(BOLL_STD)}_{float(BOLL_STD)}"

    if rsi_col not in frame.columns:
        rsi_col = next((c for c in frame.columns if c.startswith("RSI_")), rsi_col)
    if boll_upper_col not in frame.columns:
        boll_upper_col = next((c for c in frame.columns if c.startswith("BBU_")), boll_upper_col)
    if boll_mid_col not in frame.columns:
        boll_mid_col = next((c for c in frame.columns if c.startswith("BBM_")), boll_mid_col)
    if boll_lower_col not in frame.columns:
        boll_lower_col = next((c for c in frame.columns if c.startswith("BBL_")), boll_lower_col)
    return rsi_col, boll_upper_col, boll_mid_col, boll_lower_col


def _resolve_macd_columns(frame: pd.DataFrame) -> tuple[str, str, str]:
    macd_col = f"MACD_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
    hist_col = f"MACDh_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
    signal_col = f"MACDs_{MACD_FAST}_{MACD_SLOW}_{MACD_SIGNAL}"
    if macd_col not in frame.columns:
        macd_col = next((c for c in frame.columns if c.startswith("MACD_")), macd_col)
    if hist_col not in frame.columns:
        hist_col = next((c for c in frame.columns if c.startswith("MACDh_")), hist_col)
    if signal_col not in frame.columns:
        signal_col = next((c for c in frame.columns if c.startswith("MACDs_")), signal_col)
    return macd_col, hist_col, signal_col


def _resolve_atr_column(frame: pd.DataFrame) -> str:
    atr_col = f"ATRr_{ATR_LENGTH}"
    if atr_col not in frame.columns:
        atr_col = next((c for c in frame.columns if c.startswith("ATR")), f"ATRr_{ATR_LENGTH}")
    return atr_col


def derive_macd_bias(
    macd_line: float | None,
    macd_signal: float | None,
    macd_hist: float | None,
    prev_macd_line: float | None,
    prev_macd_signal: float | None,
    prev_macd_hist: float | None,
) -> str:
    if None in (macd_line, macd_signal, macd_hist):
        return "unknown"
    if (
        prev_macd_line is not None
        and prev_macd_signal is not None
        and prev_macd_line <= prev_macd_signal
        and macd_line > macd_signal
    ):
        return "golden_cross"
    if (
        prev_macd_line is not None
        and prev_macd_signal is not None
        and prev_macd_line >= prev_macd_signal
        and macd_line < macd_signal
    ):
        return "death_cross"
    if macd_hist > 0 and (prev_macd_hist is None or macd_hist >= prev_macd_hist):
        return "bullish"
    if macd_hist < 0 and (prev_macd_hist is None or macd_hist <= prev_macd_hist):
        return "bearish"
    return "neutral"


def scale_atr_to_market(
    atr: float | None,
    technical_close: float | None,
    market_price: float | None,
) -> float | None:
    if atr is None or technical_close in (None, 0) or market_price is None:
        return None
    return round(atr / technical_close * market_price, 4)


def annotate_iv_rank(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    iv_values = [
        safe_float(item.get("implied_volatility"))
        for item in candidates
        if safe_float(item.get("implied_volatility")) is not None
    ]
    if len(iv_values) < 2:
        for item in candidates:
            item["iv_rank"] = None
            item["iv_rank_note"] = "IV样本不足"
        return candidates

    min_iv = min(iv_values)
    max_iv = max(iv_values)
    for item in candidates:
        iv = safe_float(item.get("implied_volatility"))
        if iv is None:
            item["iv_rank"] = None
            item["iv_rank_note"] = "无IV数据"
            continue
        if max_iv == min_iv:
            rank = 50.0
        else:
            rank = round((iv - min_iv) / (max_iv - min_iv) * 100, 1)
        item["iv_rank"] = rank
        if rank >= IV_RANK_HIGH:
            item["iv_rank_note"] = "IV偏高，卖权权利金较厚"
        elif rank <= IV_RANK_LOW:
            item["iv_rank_note"] = "IV偏低，卖权权利金偏薄"
        else:
            item["iv_rank_note"] = "IV中等"
    return sorted(
        candidates,
        key=lambda item: (
            item.get("iv_rank") is None,
            -(item.get("iv_rank") or 0),
        ),
    )


def describe_boll_position(
    price: float | None,
    upper: float | None,
    mid: float | None,
    lower: float | None,
) -> str:
    if price is None or upper is None or mid is None or lower is None:
        return "unknown"
    if price >= upper:
        return "above_upper"
    if price >= mid + (upper - mid) * 0.6:
        return "near_upper"
    if price <= lower:
        return "below_lower"
    if price <= mid - (mid - lower) * 0.6:
        return "near_lower"
    return "around_mid"


def derive_swing_signal(
    rsi: float | None,
    boll_position: str,
    timeframe: str,
    macd_bias: str = "unknown",
    volume_confirmed: bool = False,
) -> str:
    if rsi is None or boll_position == "unknown":
        return "WAIT"

    base_signal = "HOLD"
    if timeframe == "weekly":
        if rsi < 40 and boll_position in ("below_lower", "near_lower"):
            base_signal = "BUY_SWING"
        elif rsi > 60 and boll_position in ("above_upper", "near_upper"):
            base_signal = "SELL_SWING"
    else:
        if rsi < 35 and boll_position in ("below_lower", "near_lower"):
            base_signal = "BUY_SWING"
        elif rsi > 65 and boll_position in ("above_upper", "near_upper"):
            base_signal = "SELL_SWING"

    if base_signal == "HOLD":
        return "HOLD"

    # MACD 冲突时降级
    if base_signal == "BUY_SWING" and macd_bias in ("death_cross", "bearish"):
        return "HOLD"
    if base_signal == "SELL_SWING" and macd_bias in ("golden_cross", "bullish"):
        return "HOLD"

    # 日K需成交量确认；MACD 同向时强化信号
    if timeframe == "daily" and not volume_confirmed:
        if macd_bias not in ("golden_cross", "death_cross"):
            return "HOLD"

    return base_signal


def compute_timeframe_indicators(
    quote_ctx: OpenQuoteContext,
    code: str,
    ktype: KLType,
    max_count: int,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "timeframe": "daily" if ktype == KLType.K_DAY else "weekly",
        "technical_close": None,
        "rsi": None,
        "boll_upper": None,
        "boll_mid": None,
        "boll_lower": None,
        "boll_position": "unknown",
        "macd_line": None,
        "macd_signal": None,
        "macd_hist": None,
        "macd_bias": "unknown",
        "atr": None,
        "volume": None,
        "volume_ma": None,
        "volume_ratio": None,
        "volume_confirmed": False,
        "swing_signal": "WAIT",
        "error": None,
    }

    try:
        ret, kline, _ = quote_ctx.request_history_kline(
            code,
            ktype=ktype,
            autype=AuType.QFQ,
            max_count=max_count,
        )
        if ret != RET_OK or kline is None or kline.empty:
            result["error"] = f"K线拉取失败: {kline}"
            return result

        frame = kline.copy()
        frame.ta.rsi(close="close", length=RSI_LENGTH, append=True)
        frame.ta.bbands(close="close", length=BOLL_LENGTH, std=BOLL_STD, append=True)
        frame.ta.macd(
            close="close",
            fast=MACD_FAST,
            slow=MACD_SLOW,
            signal=MACD_SIGNAL,
            append=True,
        )
        frame.ta.atr(high="high", low="low", close="close", length=ATR_LENGTH, append=True)

        latest = frame.iloc[-1]
        prev = frame.iloc[-2] if len(frame) >= 2 else latest
        rsi_col, boll_upper_col, boll_mid_col, boll_lower_col = _resolve_indicator_columns(frame)
        macd_col, hist_col, signal_col = _resolve_macd_columns(frame)
        atr_col = _resolve_atr_column(frame)

        technical_close = safe_float(latest.get("close"))
        rsi = safe_float(latest.get(rsi_col))
        boll_upper = safe_float(latest.get(boll_upper_col))
        boll_mid = safe_float(latest.get(boll_mid_col))
        boll_lower = safe_float(latest.get(boll_lower_col))
        boll_position = describe_boll_position(technical_close, boll_upper, boll_mid, boll_lower)
        timeframe = result["timeframe"]

        macd_line = safe_float(latest.get(macd_col))
        macd_signal_val = safe_float(latest.get(signal_col))
        macd_hist = safe_float(latest.get(hist_col))
        macd_bias = derive_macd_bias(
            macd_line,
            macd_signal_val,
            macd_hist,
            safe_float(prev.get(macd_col)),
            safe_float(prev.get(signal_col)),
            safe_float(prev.get(hist_col)),
        )

        atr = safe_float(latest.get(atr_col))
        volume = safe_float(latest.get("volume"))
        volume_ma = (
            round(float(frame["volume"].tail(VOLUME_MA_LENGTH).mean()), 2)
            if "volume" in frame.columns and len(frame) >= 5
            else None
        )
        volume_ratio = (
            round(volume / volume_ma, 2)
            if volume is not None and volume_ma not in (None, 0)
            else None
        )
        volume_confirmed = (
            volume_ratio is not None and volume_ratio >= VOLUME_CONFIRM_RATIO
        )

        swing_signal = derive_swing_signal(
            rsi,
            boll_position,
            timeframe,
            macd_bias=macd_bias,
            volume_confirmed=volume_confirmed,
        )

        result.update(
            {
                "technical_close": technical_close,
                "rsi": rsi,
                "boll_upper": boll_upper,
                "boll_mid": boll_mid,
                "boll_lower": boll_lower,
                "boll_position": boll_position,
                "macd_line": macd_line,
                "macd_signal": macd_signal_val,
                "macd_hist": macd_hist,
                "macd_bias": macd_bias,
                "atr": atr,
                "volume": volume,
                "volume_ma": volume_ma,
                "volume_ratio": volume_ratio,
                "volume_confirmed": volume_confirmed,
                "swing_signal": swing_signal,
            }
        )
    except Exception as exc:
        result["error"] = str(exc)

    return result


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

            call_rows = call_rows[
                (call_rows["strike_price"] >= market_price * 1.03)
                & (call_rows["strike_price"] <= market_price * 1.15)
            ].sort_values("strike_price")
            put_rows = put_rows[
                (put_rows["strike_price"] <= market_price * 0.97)
                & (put_rows["strike_price"] >= market_price * 0.85)
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
            overlay["sell_call_candidates"] = annotate_iv_rank(
                _quote_option_contracts(
                    quote_ctx, call_codes[: MAX_OPTION_CANDIDATES_EACH_SIDE + 1]
                )[:MAX_OPTION_CANDIDATES_EACH_SIDE]
            )

        if put_codes:
            overlay["sell_put_candidates"] = annotate_iv_rank(
                _quote_option_contracts(
                    quote_ctx, put_codes[: MAX_OPTION_CANDIDATES_EACH_SIDE + 1]
                )[:MAX_OPTION_CANDIDATES_EACH_SIDE]
            )

        if not overlay["sell_call_candidates"] and not overlay["sell_put_candidates"]:
            overlay["scan_note"] = "未找到满足 Delta 条件的卖权候选合约"
    except Exception as exc:
        overlay["scan_note"] = str(exc)

    return overlay


def analyze_stock_position(
    quote_ctx: OpenQuoteContext,
    stock: dict[str, Any],
    snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    pnl = enrich_stock_pnl(stock, snapshot)
    lot_size = resolve_lot_size(snapshot, stock)
    stock = {**stock, "lot_size": lot_size, "shares_per_lot": lot_size}
    swing_strategy = build_swing_strategy_profile(pnl.get("pl_ratio"))

    daily = compute_timeframe_indicators(quote_ctx, stock["code"], KLType.K_DAY, KLINE_COUNT)
    weekly = compute_timeframe_indicators(
        quote_ctx, stock["code"], KLType.K_WEEK, WEEKLY_KLINE_COUNT
    )

    primary = swing_strategy["primary_timeframe"]
    primary_signal = weekly["swing_signal"] if primary == "weekly" else daily["swing_signal"]
    secondary_signal = daily["swing_signal"] if primary == "weekly" else weekly["swing_signal"]

    option_overlay = scan_sell_option_candidates(quote_ctx, {**stock, "pnl": pnl}, swing_strategy)
    combined_swing_signal = {
        "primary_timeframe": primary,
        "primary_signal": primary_signal,
        "secondary_signal": secondary_signal,
        "aligned": primary_signal == secondary_signal,
    }
    stock_trade_plan = build_stock_trade_plan(
        {**stock, "daily": daily, "weekly": weekly},
        swing_strategy,
        combined_swing_signal,
        snapshot,
        pnl,
    )
    option_trade_plan = build_option_trade_plan_for_stock(
        stock,
        option_overlay,
        swing_strategy,
        combined_swing_signal,
    )

    return {
        **stock,
        "pnl": pnl,
        "swing_strategy": swing_strategy,
        "daily": daily,
        "weekly": weekly,
        "combined_swing_signal": combined_swing_signal,
        "stock_trade_plan": stock_trade_plan,
        "option_trade_plan": option_trade_plan,
        "option_overlay": option_overlay,
        "indicator_error": daily.get("error") or weekly.get("error"),
    }


def compute_stock_indicators(
    quote_ctx: OpenQuoteContext,
    stock: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return analyze_stock_position(quote_ctx, stock, snapshot)


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
                    }
                )
            )
            enriched[-1]["option_trade_plan"] = build_option_position_trade_plan(enriched[-1])
            enriched[-1]["iv_rank"] = None
            enriched[-1]["iv_rank_note"] = "持仓合约，请结合标的正股 option_overlay.iv_rank 判断"
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


def build_portfolio_payload(
    stocks: list[dict[str, Any]],
    options: list[dict[str, Any]],
) -> dict[str, Any]:
    required_positions = [
        {
            "code": item["code"],
            "name": item.get("name", ""),
            "asset_type": "stock",
            "position_direction": item.get("position_direction"),
            "loss_tier": (item.get("swing_strategy") or {}).get("loss_tier"),
            "lot_size": item.get("lot_size"),
            "shares_per_lot": item.get("shares_per_lot"),
        }
        for item in stocks
    ] + [
        {
            "code": item["code"],
            "name": item.get("name", ""),
            "asset_type": "option",
            "position_direction": item.get("position_direction"),
        }
        for item in options
    ]

    return {
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
        "market": "HK",
        "stocks": stocks,
        "options": options,
        "required_positions": required_positions,
        "summary": {
            "stock_count": len(stocks),
            "option_count": len(options),
            "total_position_count": len(required_positions),
            "total_stock_market_val": sum(s.get("market_val") or 0 for s in stocks),
            "total_option_market_val": sum(o.get("market_val") or 0 for o in options),
        },
    }


def collect_required_codes(portfolio_payload: dict[str, Any]) -> list[str]:
    return [item["code"] for item in portfolio_payload.get("required_positions", [])]


def find_missing_recommendation_codes(
    decision: dict[str, Any],
    required_codes: list[str],
) -> list[str]:
    returned_codes = {
        rec.get("code")
        for rec in decision.get("recommendations", [])
        if isinstance(rec, dict) and rec.get("code")
    }
    return [code for code in required_codes if code not in returned_codes]


def call_deepseek(client: OpenAI, portfolio_payload: dict[str, Any]) -> dict[str, Any]:
    required_codes = collect_required_codes(portfolio_payload)
    required_count = len(required_codes)
    code_list_text = "、".join(required_codes)

    user_prompt = (
        f"请分析以下港股账户持仓数据，并输出符合 schema 的 JSON 交易建议。\n"
        f"本次共有 {required_count} 个持仓标的，recommendations 必须逐一生成 {required_count} 条建议，"
        f"与 required_positions 一一对应，不得遗漏。\n"
        f"必须覆盖的全部代码：{code_list_text}\n"
        "策略框架：周K定方向、日K找时机；综合 RSI/布林带/MACD/成交量/ATR 研判。\n"
        "价格字段：pnl.market_price 是未复权现价；daily/weekly.technical_close 是复权技术价，禁止混用。\n"
        "每个正股已预计算 stock_trade_plan（整手股数 lot_size、具体手数/股数）与 option_trade_plan，"
        "输出时必须原样填入 recommendations 对应字段；suggested_qty 必须是 lot_size 整数倍。\n"
        "务必严格区分 position_direction（如「卖出Call」「买入Put」），"
        "卖出期权与买入期权的 Theta/到期逻辑完全相反。\n"
        f"{json.dumps(portfolio_payload, ensure_ascii=False, indent=2)}"
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    last_missing: list[str] = required_codes
    for attempt in range(1, 3):
        response = client.chat.completions.create(
            model="deepseek-chat",
            response_format={"type": "json_object"},
            messages=messages,
            temperature=0.2,
            max_tokens=8192,
        )

        content = response.choices[0].message.content
        if not content:
            raise ValueError("DeepSeek 返回空内容")

        decision = json.loads(content)
        last_missing = find_missing_recommendation_codes(decision, required_codes)
        if not last_missing:
            return decision

        log(
            "模型",
            f"第 {attempt} 次返回缺少 {len(last_missing)} 个标的建议: {last_missing}",
        )
        messages.append({"role": "assistant", "content": content})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"上一次 recommendations 不完整，缺少以下 {len(last_missing)} 个标的，"
                    f"请补全并重新输出完整 JSON（仍需包含全部 {required_count} 个标的建议）：\n"
                    + "\n".join(f"- {code}" for code in last_missing)
                ),
            }
        )

    raise ValueError(f"模型未返回全部持仓建议，仍缺少: {last_missing}")


def validate_decision_schema(
    decision: dict[str, Any],
    required_codes: list[str] | None = None,
    stocks_by_code: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if "portfolio_risk_summary" not in decision:
        raise ValueError("缺少 portfolio_risk_summary 字段")
    if "recommendations" not in decision or not isinstance(decision["recommendations"], list):
        raise ValueError("缺少 recommendations 字段或类型错误")

    for idx, rec in enumerate(decision["recommendations"]):
        required = (
            "code",
            "name",
            "action",
            "confidence",
            "reasoning",
            "suggested_trigger",
            "stock_trade_plan",
            "option_trade_plan",
        )
        missing = [field for field in required if field not in rec]
        if missing:
            raise ValueError(f"recommendations[{idx}] 缺少字段: {missing}")

        stock_plan = rec.get("stock_trade_plan")
        option_plan = rec.get("option_trade_plan")
        if not isinstance(stock_plan, dict):
            raise ValueError(f"recommendations[{idx}] stock_trade_plan 必须为对象")
        if not isinstance(option_plan, dict):
            raise ValueError(f"recommendations[{idx}] option_trade_plan 必须为对象")

        lot_size = int(stock_plan.get("lot_size") or 0)
        suggested_qty = int(stock_plan.get("suggested_qty") or 0)
        suggested_lots = int(stock_plan.get("suggested_lots") or 0)
        if suggested_qty > 0:
            if lot_size <= 0:
                raise ValueError(f"recommendations[{idx}] 有交易数量但缺少 lot_size")
            if suggested_qty % lot_size != 0:
                raise ValueError(
                    f"recommendations[{idx}] suggested_qty={suggested_qty} "
                    f"不是整手（lot_size={lot_size}）"
                )
            if suggested_lots * lot_size != suggested_qty:
                raise ValueError(
                    f"recommendations[{idx}] suggested_lots({suggested_lots}) 与 "
                    f"suggested_qty({suggested_qty}) 不自洽"
                )

        if stocks_by_code and rec.get("code") in stocks_by_code:
            ref_plan = stocks_by_code[rec["code"]].get("stock_trade_plan") or {}
            ref_qty = int(ref_plan.get("suggested_qty") or 0)
            if ref_qty > 0 and suggested_qty != ref_qty:
                raise ValueError(
                    f"recommendations[{idx}] suggested_qty 应为预计算的 {ref_qty}，"
                    f"实际为 {suggested_qty}"
                )

        if option_plan.get("action") not in (None, "none"):
            if not option_plan.get("contract_code") or not option_plan.get("expire_date"):
                raise ValueError(
                    f"recommendations[{idx}] 期权操作缺少 contract_code 或 expire_date"
                )

    if required_codes:
        missing_codes = find_missing_recommendation_codes(decision, required_codes)
        if missing_codes:
            raise ValueError(f"recommendations 未覆盖全部持仓，缺少: {missing_codes}")
        if len(decision["recommendations"]) != len(required_codes):
            raise ValueError(
                f"recommendations 数量应为 {len(required_codes)}，"
                f"实际为 {len(decision['recommendations'])}"
            )
    return decision


def maybe_unlock_trade(trade_ctx: OpenSecTradeContext) -> None:
    unlock_pwd = os.getenv("FUTU_TRADE_UNLOCK_PWD", "").strip()
    if not unlock_pwd:
        log("交易", "未配置 FUTU_TRADE_UNLOCK_PWD，跳过解锁（若查询失败请配置交易密码）")
        return

    ret, msg = trade_ctx.unlock_trade(unlock_pwd)
    if ret == RET_OK:
        log("交易", "交易解锁成功")
    else:
        log("交易", f"交易解锁失败: {msg}")


def save_decision_record(
    decision: dict[str, Any],
    *,
    required_codes: list[str],
    payload_summary: dict[str, Any] | None = None,
) -> Path:
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    record = {
        "saved_at": datetime.now().isoformat(),
        "required_codes": required_codes,
        "payload_summary": payload_summary,
        "decision": decision,
    }
    path = DECISIONS_DIR / f"decision_{ts}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    latest = DECISIONS_DIR / "latest.json"
    latest.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_analysis_cycle(
    quote_ctx: OpenQuoteContext,
    trade_ctx: OpenSecTradeContext,
    ai_client: OpenAI,
    *,
    print_decision: bool = True,
    save_decision: bool = True,
) -> dict[str, Any] | None:
    log("持仓", "开始拉取港股持仓...")
    ret, positions = get_position_list(trade_ctx)
    if ret != RET_OK:
        raise RuntimeError(f"持仓拉取失败: {positions}")
    log("持仓", f"原始持仓 {len(positions)} 条")

    stocks_raw, options_raw = classify_positions(positions, quote_ctx)
    log("分类", f"正股 {len(stocks_raw)} 个，期权 {len(options_raw)} 个")

    stock_codes = [s["code"] for s in stocks_raw]
    snapshot_map = fetch_snapshot_map(quote_ctx, stock_codes)

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
            f"主信号={combined.get('primary_signal')}",
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

    payload = build_portfolio_payload(stocks, options)
    required_codes = collect_required_codes(payload)
    log("模型", f"开始调用 DeepSeek，需为 {len(required_codes)} 个持仓逐一生成建议...")
    try:
        decision = call_deepseek(ai_client, payload)
        stocks_by_code = {s["code"]: s for s in stocks}
        decision = validate_decision_schema(decision, required_codes, stocks_by_code)
        saved_path: Path | None = None
        if save_decision:
            saved_path = save_decision_record(
                decision,
                required_codes=required_codes,
                payload_summary=payload.get("summary"),
            )
            log("模型", f"决策已保存: {saved_path}")
        if print_decision:
            print("\n===== DeepSeek 交易决策 JSON =====")
            print(json.dumps(decision, ensure_ascii=False, indent=2))
            print(
                f"===== 建议覆盖 {len(decision['recommendations'])}/{len(required_codes)} 个持仓 =====\n"
            )
        return {
            "decision": decision,
            "required_codes": required_codes,
            "stocks_by_code": stocks_by_code,
            "payload_summary": payload.get("summary"),
            "saved_path": str(saved_path) if saved_path else None,
        }
    except json.JSONDecodeError as exc:
        log("模型", f"JSON 解析失败: {exc}")
    except Exception as exc:
        log("模型", f"模型调用失败: {exc}")
        traceback.print_exc()
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="港股持仓量化分析")
    parser.add_argument(
        "--once",
        action="store_true",
        help="只运行一轮分析后退出，不进入等待循环",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv()

    host = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()

    if not api_key:
        raise RuntimeError("请在 .env 中配置 DEEPSEEK_API_KEY")

    ai_client = OpenAI(api_key=api_key, base_url=base_url)
    quote_ctx: OpenQuoteContext | None = None
    trade_ctx: OpenSecTradeContext | None = None

    try:
        log("连接", f"正在连接 Futu OpenD {host}:{port} ...")
        quote_ctx = OpenQuoteContext(host=host, port=port)
        trade_ctx = OpenHKTradeContext(filter_trdmarket=TrdMarket.HK, host=host, port=port)
        log("连接", "行情与交易上下文初始化完成")

        maybe_unlock_trade(trade_ctx)

        interval_sec, interval_reason = resolve_analysis_interval()
        if args.once:
            log("循环", "单次运行模式（--once）")
        else:
            log("循环", f"运行间隔策略：{interval_reason}")

        while True:
            try:
                run_analysis_cycle(quote_ctx, trade_ctx, ai_client)
            except Exception as exc:
                log("循环", f"本轮分析异常: {exc}")
                traceback.print_exc()

            if args.once:
                log("循环", "单次运行完成，退出")
                break

            interval_sec, interval_reason = resolve_analysis_interval()
            log("循环", f"{interval_reason}，等待 {interval_sec} 秒...")
            time.sleep(interval_sec)
    finally:
        log("连接", "正在释放 Futu 连接...")
        if quote_ctx is not None:
            quote_ctx.close()
        if trade_ctx is not None:
            trade_ctx.close()
        log("连接", "连接已关闭，脚本退出")


if __name__ == "__main__":
    main()
