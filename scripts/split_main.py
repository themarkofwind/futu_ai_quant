#!/usr/bin/env python3
"""One-off script: split main.py into futu_ai_quant package modules."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
PKG = ROOT / "futu_ai_quant"

# function name -> (module path relative to PKG, public?)
MODULE_MAP: dict[str, str] = {
    # utils
    "log": "utils/logging.py",
    "safe_float": "utils/numbers.py",
    # config re-exports handled separately
    # market
    "is_hk_trading_session": "market/session.py",
    "hk_session_volume_fraction": "market/session.py",
    "evaluate_volume_confirmed": "market/session.py",
    "resolve_analysis_interval": "market/session.py",
    "resolve_lot_size": "market/lot.py",
    "calc_full_lot_trade_qty": "market/lot.py",
    "round_down_to_lot": "market/lot.py",
    "estimate_hk_stock_trade_fees": "market/fees.py",
    "swing_trade_meets_cost_threshold": "market/fees.py",
    # strategy
    "resolve_effective_swing_signal": "strategy/signals.py",
    "classify_loss_tier": "strategy/profile.py",
    "build_swing_strategy_profile": "strategy/profile.py",
    "derive_macd_bias": "strategy/signals.py",
    "describe_boll_position": "strategy/signals.py",
    "derive_swing_signal": "strategy/signals.py",
    # domain positions
    "resolve_position_side": "domain/positions.py",
    "infer_option_right": "domain/positions.py",
    "build_position_direction": "domain/positions.py",
    "enrich_option_context": "domain/positions.py",
    "is_option_code": "domain/positions.py",
    "classify_positions": "domain/positions.py",
    "resolve_option_underlying_code": "domain/positions.py",
    # indicators
    "_resolve_indicator_columns": "indicators/technical.py",
    "_resolve_macd_columns": "indicators/technical.py",
    "_resolve_atr_column": "indicators/technical.py",
    "compute_timeframe_indicators": "indicators/technical.py",
    "scale_atr_to_market": "indicators/technical.py",
    "calc_max_covered_calls": "indicators/iv.py",
    "cap_option_contracts": "indicators/iv.py",
    "_iv_history_path": "indicators/iv.py",
    "load_iv_history": "indicators/iv.py",
    "record_iv_scan_snapshot": "indicators/iv.py",
    "compute_historical_iv_rank": "indicators/iv.py",
    "_iv_level_note": "indicators/iv.py",
    "annotate_iv_metrics": "indicators/iv.py",
    # brokers futu
    "get_position_list": "brokers/futu/positions.py",
    "maybe_unlock_trade": "brokers/futu/positions.py",
    "fetch_snapshot_map": "brokers/futu/quotes.py",
    "enrich_stock_pnl": "brokers/futu/quotes.py",
    "_build_option_quote_leg": "brokers/futu/options.py",
    "_quote_option_contracts": "brokers/futu/options.py",
    "scan_sell_option_candidates": "brokers/futu/options.py",
    "build_option_leg": "brokers/futu/options.py",
    "fetch_option_metrics": "brokers/futu/options.py",
    "fetch_option_metrics_one_by_one": "brokers/futu/options.py",
    # planning
    "apply_swing_trade_to_plan": "planning/stock.py",
    "build_stock_trade_plan": "planning/stock.py",
    "build_option_trade_plan_for_stock": "planning/option.py",
    "build_option_position_trade_plan": "planning/option.py",
    "empty_stock_trade_plan": "planning/stock.py",
    "empty_option_trade_plan": "planning/option.py",
    # history
    "_parse_deal_time": "history/trades.py",
    "_ytd_trade_cache_path": "history/trades.py",
    "_normalize_trd_side": "history/trades.py",
    "_is_stock_deal_code": "history/trades.py",
    "deal_underlying_code": "history/trades.py",
    "_deal_record_from_row": "history/trades.py",
    "_load_ytd_trade_cache": "history/trades.py",
    "_cache_is_fresh": "history/trades.py",
    "_merge_deal_records": "history/trades.py",
    "_fetch_history_deals_between": "history/trades.py",
    "_fetch_ytd_deals_from_api": "history/trades.py",
    "_save_ytd_trade_cache": "history/trades.py",
    "load_ytd_trade_history": "history/trades.py",
    "_compact_trade_row": "history/trades.py",
    "_summarize_stock_trades": "history/trades.py",
    "_build_swing_hint": "history/trades.py",
    "summarize_trade_history_for_stock": "history/trades.py",
    "attach_trade_history_to_stocks": "history/trades.py",
    # analysis
    "analyze_stock_position": "analysis/stock.py",
    "compute_stock_indicators": "analysis/stock.py",
    "summarize_existing_option_position": "analysis/portfolio.py",
    "attach_stock_option_context": "analysis/portfolio.py",
    "build_portfolio_risk_overlay": "analysis/portfolio.py",
    "build_portfolio_payload": "analysis/portfolio.py",
    "collect_required_codes": "analysis/portfolio.py",
    # decision
    "infer_stock_action": "decision/rules.py",
    "infer_option_action": "decision/rules.py",
    "build_rules_reasoning": "decision/rules.py",
    "build_rules_portfolio_summary": "decision/rules.py",
    "serialize_trade_plan_for_decision": "decision/rules.py",
    "serialize_option_plan_for_decision": "decision/rules.py",
    "build_rules_decision": "decision/rules.py",
    "find_missing_recommendation_codes": "decision/validation.py",
    "call_deepseek": "decision/ai.py",
    "validate_decision_schema": "decision/validation.py",
    "save_decision_record": "decision/storage.py",
    # pipeline
    "run_analysis_cycle": "pipeline/cycle.py",
    "parse_args": "cli/analyze.py",
    "main": "cli/analyze.py",
}

MODULE_IMPORTS: dict[str, str] = {
    "utils/logging.py": textwrap.dedent("""\
        from __future__ import annotations

        import time
    """),
    "utils/numbers.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any

        import pandas as pd
    """),
    "config/settings.py": textwrap.dedent("""\
        from __future__ import annotations

        import os
        import re
        from pathlib import Path
    """),
    "config/prompts.py": textwrap.dedent("""\
        from __future__ import annotations
    """),
    "market/session.py": textwrap.dedent("""\
        from __future__ import annotations

        from datetime import datetime
        from typing import Any

        from futu_ai_quant.config.settings import (
            ANALYSIS_INTERVAL_SEC,
            INTRADAY_INTERVAL_SEC,
            MIN_SESSION_VOLUME_FRACTION,
            OFFHOURS_INTERVAL_SEC,
            VOLUME_CONFIRM_RATIO,
            VOLUME_FILTER,
        )
    """),
    "market/lot.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any

        from futu_ai_quant.utils.numbers import safe_float
    """),
    "market/fees.py": textwrap.dedent("""\
        from __future__ import annotations

        from futu_ai_quant.config.settings import (
            SWING_COMMISSION_RATE,
            SWING_MIN_COMMISSION,
            SWING_MIN_PROFIT_COST_RATIO,
            SWING_PLATFORM_FEE,
            SWING_STAMP_DUTY_RATE,
        )
    """),
    "strategy/signals.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any
    """),
    "strategy/profile.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any

        from futu_ai_quant.config.settings import (
            DEEP_LOSS_THRESHOLD,
            MODERATE_LOSS_THRESHOLD,
        )
    """),
    "domain/positions.py": textwrap.dedent("""\
        from __future__ import annotations

        import re
        from typing import Any

        import pandas as pd
        from futu import OpenQuoteContext, RET_OK

        from futu_ai_quant.config.settings import HK_OPTION_CODE_PATTERN
        from futu_ai_quant.utils.logging import log
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "indicators/technical.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any

        import pandas as pd
        import pandas_ta as ta
        from futu import AuType, KLType, OpenQuoteContext, RET_OK

        from futu_ai_quant.config.settings import (
            ATR_LENGTH,
            BOLL_LENGTH,
            BOLL_STD,
            KLINE_COUNT,
            MACD_FAST,
            MACD_SIGNAL,
            MACD_SLOW,
            RSI_LENGTH,
            VOLUME_MA_LENGTH,
        )
        from futu_ai_quant.market.session import evaluate_volume_confirmed
        from futu_ai_quant.strategy.signals import (
            derive_macd_bias,
            derive_swing_signal,
            describe_boll_position,
        )
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "indicators/iv.py": textwrap.dedent("""\
        from __future__ import annotations

        import json
        import time
        from pathlib import Path
        from typing import Any

        from futu_ai_quant.config.settings import (
            IV_HISTORY_DIR,
            IV_HISTORY_MAX_SAMPLES,
            IV_HISTORY_MIN_SAMPLES,
            IV_RANK_HIGH,
            IV_RANK_LOW,
            MAX_OPTION_CONTRACTS_PER_TRADE,
        )
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "brokers/futu/positions.py": textwrap.dedent("""\
        from __future__ import annotations

        import os

        import pandas as pd
        from futu import OpenSecTradeContext, RET_OK, TrdEnv, TrdMarket

        from futu_ai_quant.utils.logging import log
    """),
    "brokers/futu/quotes.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any

        from futu import OpenQuoteContext, RET_OK

        from futu_ai_quant.utils.logging import log
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "brokers/futu/options.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any

        from futu import (
            IndexOptionType,
            OpenQuoteContext,
            OptionStrategyLeg,
            OptionType,
            RET_OK,
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
        from futu_ai_quant.planning.stock import empty_stock_trade_plan
        from futu_ai_quant.utils.logging import log
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "planning/stock.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any

        from futu_ai_quant.indicators.technical import scale_atr_to_market
        from futu_ai_quant.market.fees import (
            estimate_hk_stock_trade_fees,
            swing_trade_meets_cost_threshold,
        )
        from futu_ai_quant.market.lot import calc_full_lot_trade_qty, resolve_lot_size
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "planning/option.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any

        from futu_ai_quant.domain.positions import resolve_position_side
        from futu_ai_quant.indicators.iv import calc_max_covered_calls, cap_option_contracts
        from futu_ai_quant.market.lot import resolve_lot_size
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "history/trades.py": textwrap.dedent("""\
        from __future__ import annotations

        import json
        from datetime import datetime, timedelta
        from pathlib import Path
        from typing import Any

        import pandas as pd
        from futu import OpenSecTradeContext, RET_OK, TrdEnv, TrdMarket

        from futu_ai_quant.config.settings import (
            FUTU_HISTORY_QUERY_DAYS,
            TRADE_HISTORY_CACHE_HOURS,
            TRADE_HISTORY_DIR,
            TRADE_RECENT_SWING_DAYS,
        )
        from futu_ai_quant.domain.positions import is_option_code, resolve_option_underlying_code
        from futu_ai_quant.utils.logging import log
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "analysis/stock.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any

        from futu import KLType, OpenQuoteContext

        from futu_ai_quant.brokers.futu.options import scan_sell_option_candidates
        from futu_ai_quant.brokers.futu.quotes import enrich_stock_pnl
        from futu_ai_quant.config.settings import KLINE_COUNT, WEEKLY_KLINE_COUNT
        from futu_ai_quant.indicators.technical import compute_timeframe_indicators
        from futu_ai_quant.market.lot import resolve_lot_size
        from futu_ai_quant.planning.option import build_option_trade_plan_for_stock
        from futu_ai_quant.planning.stock import build_stock_trade_plan
        from futu_ai_quant.strategy.profile import build_swing_strategy_profile
        from futu_ai_quant.strategy.signals import resolve_effective_swing_signal
    """),
    "analysis/portfolio.py": textwrap.dedent("""\
        from __future__ import annotations

        import time
        from typing import Any

        from futu_ai_quant.config.settings import PORTFOLIO_MAX_SINGLE_WEIGHT_PCT
        from futu_ai_quant.domain.positions import resolve_option_underlying_code
        from futu_ai_quant.planning.option import empty_option_trade_plan
        from futu_ai_quant.planning.stock import empty_stock_trade_plan
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "decision/rules.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any

        from futu_ai_quant.analysis.portfolio import build_portfolio_risk_overlay
        from futu_ai_quant.config.settings import TRADE_RECENT_SWING_DAYS
        from futu_ai_quant.planning.option import empty_option_trade_plan
        from futu_ai_quant.planning.stock import empty_stock_trade_plan
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "decision/validation.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any
    """),
    "decision/ai.py": textwrap.dedent("""\
        from __future__ import annotations

        import json
        from typing import Any

        from openai import OpenAI

        from futu_ai_quant.analysis.portfolio import collect_required_codes
        from futu_ai_quant.config.prompts import SYSTEM_PROMPT
        from futu_ai_quant.decision.validation import find_missing_recommendation_codes
        from futu_ai_quant.utils.logging import log
    """),
    "decision/storage.py": textwrap.dedent("""\
        from __future__ import annotations

        import json
        from datetime import datetime
        from pathlib import Path
        from typing import Any

        from futu_ai_quant.config.settings import DECISIONS_DIR
    """),
    "pipeline/cycle.py": textwrap.dedent("""\
        from __future__ import annotations

        import json
        import traceback
        from pathlib import Path
        from typing import Any

        from futu import OpenQuoteContext, OpenSecTradeContext, RET_OK
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
        from futu_ai_quant.decision.storage import save_decision_record
        from futu_ai_quant.decision.validation import validate_decision_schema
        from futu_ai_quant.domain.positions import classify_positions
        from futu_ai_quant.history.trades import attach_trade_history_to_stocks, load_ytd_trade_history
        from futu_ai_quant.utils.logging import log
    """),
    "cli/analyze.py": textwrap.dedent("""\
        from __future__ import annotations

        import argparse
        import os
        import time
        import traceback

        from dotenv import load_dotenv
        from futu import OpenQuoteContext, OpenSecTradeContext, TrdMarket
        from openai import OpenAI

        from futu_ai_quant.brokers.futu.client import OpenHKTradeContext
        from futu_ai_quant.brokers.futu.positions import maybe_unlock_trade
        from futu_ai_quant.market.session import resolve_analysis_interval
        from futu_ai_quant.pipeline.cycle import run_analysis_cycle
        from futu_ai_quant.utils.logging import log
    """),
}


def extract_functions(source: str) -> dict[str, str]:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    funcs: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            funcs[node.name] = "".join(lines[node.lineno - 1 : node.end_lineno])
    return funcs


def extract_constants_and_prompt(source: str) -> tuple[str, str]:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    const_lines: list[str] = []
    prompt_lines: list[str] = []
    in_prompt = False
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SYSTEM_PROMPT":
                    prompt_lines.append("".join(lines[node.lineno - 1 : node.end_lineno]))
                    in_prompt = True
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "OpenHKTradeContext":
                const_lines.append("".join(lines[node.lineno - 1 : node.end_lineno]))
        elif isinstance(node, ast.Assign) and not in_prompt:
            const_lines.append("".join(lines[node.lineno - 1 : node.end_lineno]))
    return "".join(const_lines), "".join(prompt_lines)


def write_module(rel_path: str, header: str, body: str) -> None:
    path = PKG / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    content = header.rstrip() + "\n\n\n" + body.strip() + "\n"
    path.write_text(content, encoding="utf-8")


def main() -> None:
    source = MAIN.read_text(encoding="utf-8")
    funcs = extract_functions(source)
    constants, prompt = extract_constants_and_prompt(source)

    # settings
    settings_header = MODULE_IMPORTS["config/settings.py"]
    settings_body = constants.replace(
        "OpenHKTradeContext = OpenSecTradeContext\n",
        "",
    )
    write_module("config/settings.py", settings_header, settings_body)
    write_module("config/prompts.py", MODULE_IMPORTS["config/prompts.py"], prompt)

    # client alias
    client_body = textwrap.dedent("""\
        from futu import OpenSecTradeContext, TrdMarket

        OpenHKTradeContext = OpenSecTradeContext
    """)
    write_module(
        "brokers/futu/client.py",
        "from __future__ import annotations\n",
        client_body,
    )

    by_module: dict[str, list[str]] = {}
    for name, rel in MODULE_MAP.items():
        if name not in funcs:
            raise SystemExit(f"Missing function in main.py: {name}")
        by_module.setdefault(rel, []).append(funcs[name])

    for rel, bodies in by_module.items():
        header = MODULE_IMPORTS.get(rel, "from __future__ import annotations\n")
        write_module(rel, header, "\n\n".join(bodies))

    print(f"Wrote {len(by_module)} modules under {PKG}")


if __name__ == "__main__":
    main()
