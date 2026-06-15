#!/usr/bin/env python3
"""One-off script: split sim_trader.py into futu_ai_quant.sim package modules."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIM = ROOT / "sim_trader.py"
PKG = ROOT / "futu_ai_quant"

MODULE_MAP: dict[str, str] = {
    "resolve_underlying_code": "sim/options.py",
    "resolve_contract_size": "sim/options.py",
    "fetch_option_quote_map": "sim/options.py",
    "find_roll_open_leg": "sim/options.py",
    "FutuSimBroker": "sim/broker.py",
    "append_jsonl": "sim/io.py",
    "FeeBreakdown": "sim/fees.py",
    "HKCostModel": "sim/fees.py",
    "PaperPortfolio": "sim/portfolio.py",
    "LocalSimEngine": "sim/engine.py",
    "load_decision_record": "sim/io.py",
    "fetch_market_data": "sim/market_data.py",
    "mark_to_market": "sim/market_data.py",
    "process_pending_orders": "sim/runner.py",
    "apply_recommendations": "sim/runner.py",
    "save_snapshot": "sim/io.py",
    "print_report": "sim/io.py",
    "init_mirror_portfolio": "sim/runner.py",
    "collect_price_codes": "sim/runner.py",
    "enrich_option_quote_map_for_decision": "sim/runner.py",
    "run_sim_cycle": "sim/runner.py",
    "parse_args": "cli/sim.py",
    "resolve_backend": "cli/sim.py",
    "build_engine": "cli/sim.py",
    "main": "cli/sim.py",
}

MODULE_IMPORTS: dict[str, str] = {
    "sim/settings.py": textwrap.dedent("""\
        from __future__ import annotations

        import os
        from pathlib import Path
    """),
    "sim/options.py": textwrap.dedent("""\
        from __future__ import annotations

        import re
        from typing import Any

        from futu import IndexOptionType, OpenQuoteContext, OptionType, RET_OK

        from futu_ai_quant.brokers.futu.options import _build_option_quote_leg
        from futu_ai_quant.config.settings import OPTION_MAX_DAYS, OPTION_MIN_DAYS
        from futu_ai_quant.domain.positions import is_option_code
        from futu_ai_quant.sim.portfolio import PaperPortfolio
        from futu_ai_quant.sim.settings import OPTION_PREFIX_TO_STOCK, SIM_OPTION_CONTRACT_SIZE
        from futu_ai_quant.utils.logging import log
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "sim/broker.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any

        from futu import OrderType, RET_OK, TrdEnv, TrdMarket, TrdSide

        from futu_ai_quant.utils.logging import log
    """),
    "sim/io.py": textwrap.dedent("""\
        from __future__ import annotations

        import json
        from pathlib import Path
        from typing import Any

        from futu_ai_quant.config.settings import DECISIONS_DIR
        from futu_ai_quant.sim.portfolio import PaperPortfolio
        from futu_ai_quant.sim.settings import METRICS_FILE, SNAPSHOTS_FILE
        from futu_ai_quant.utils.logging import log
    """),
    "sim/fees.py": textwrap.dedent("""\
        from __future__ import annotations

        from dataclasses import dataclass

        from futu_ai_quant.sim.settings import (
            SIM_COMMISSION_RATE,
            SIM_MIN_COMMISSION,
            SIM_PLATFORM_FEE,
            SIM_STAMP_DUTY_RATE,
        )
    """),
    "sim/portfolio.py": textwrap.dedent("""\
        from __future__ import annotations

        import json
        from datetime import datetime
        from pathlib import Path
        from typing import Any

        from futu_ai_quant.sim.fees import FeeBreakdown
        from futu_ai_quant.sim.io import append_jsonl
        from futu_ai_quant.sim.options import resolve_underlying_code
        from futu_ai_quant.sim.settings import (
            PORTFOLIO_FILE,
            SIM_INITIAL_CASH,
            SIM_OPTION_CONTRACT_SIZE,
            TRADES_FILE,
        )
        from futu_ai_quant.utils.logging import log
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "sim/engine.py": textwrap.dedent("""\
        from __future__ import annotations

        import uuid
        from datetime import datetime
        from typing import Any

        from futu_ai_quant.sim.broker import FutuSimBroker
        from futu_ai_quant.sim.fees import HKCostModel
        from futu_ai_quant.sim.portfolio import PaperPortfolio
        from futu_ai_quant.sim.settings import SIM_EXECUTION_MODE
        from futu_ai_quant.utils.logging import log
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "sim/market_data.py": textwrap.dedent("""\
        from __future__ import annotations

        from typing import Any

        from futu import OpenQuoteContext

        from futu_ai_quant.brokers.futu.quotes import fetch_snapshot_map
        from futu_ai_quant.domain.positions import is_option_code
        from futu_ai_quant.sim.options import fetch_option_quote_map, resolve_contract_size
        from futu_ai_quant.sim.portfolio import PaperPortfolio
        from futu_ai_quant.utils.numbers import safe_float
    """),
    "sim/runner.py": textwrap.dedent("""\
        from __future__ import annotations

        import uuid
        from datetime import datetime
        from pathlib import Path
        from typing import Any

        from futu import Currency, OpenQuoteContext, RET_OK, TrdEnv

        from futu_ai_quant.brokers.futu.positions import get_position_list
        from futu_ai_quant.domain.positions import classify_positions, is_option_code
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
    """),
    "cli/sim.py": textwrap.dedent("""\
        from __future__ import annotations

        import argparse
        import os
        import time
        import traceback
        from typing import Any

        from dotenv import load_dotenv
        from futu import OpenQuoteContext, TrdMarket
        from openai import OpenAI

        from futu_ai_quant.brokers.futu.client import OpenHKTradeContext
        from futu_ai_quant.brokers.futu.positions import maybe_unlock_trade
        from futu_ai_quant.market.session import resolve_analysis_interval
        from futu_ai_quant.sim.broker import FutuSimBroker
        from futu_ai_quant.sim.engine import LocalSimEngine
        from futu_ai_quant.sim.fees import HKCostModel
        from futu_ai_quant.sim.io import print_report
        from futu_ai_quant.sim.portfolio import PaperPortfolio
        from futu_ai_quant.sim.runner import init_mirror_portfolio, run_sim_cycle
        from futu_ai_quant.sim.settings import SIM_BACKEND, SIM_EXECUTION_MODE
        from futu_ai_quant.utils.logging import log
    """),
}


def extract_defs(source: str) -> dict[str, str]:
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    defs: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            defs[node.name] = "".join(lines[node.lineno - 1 : node.end_lineno])
    return defs


def extract_sim_constants(source: str) -> str:
    lines = source.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith("SIM_DATA_DIR"))
    end = next(i for i, line in enumerate(lines) if line.strip() == "}")
    return "".join(lines[start : end + 1])


def write_module(rel_path: str, header: str, body: str) -> None:
    path = PKG / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header.rstrip() + "\n\n\n" + body.strip() + "\n", encoding="utf-8")


def main() -> None:
    source = SIM.read_text(encoding="utf-8")
    defs = extract_defs(source)
    constants = extract_sim_constants(source)
    write_module("sim/settings.py", MODULE_IMPORTS["sim/settings.py"], constants)

    by_module: dict[str, list[str]] = {}
    for name, rel in MODULE_MAP.items():
        if name not in defs:
            raise SystemExit(f"Missing in sim_trader.py: {name}")
        by_module.setdefault(rel, []).append(defs[name])

    for rel, bodies in by_module.items():
        header = MODULE_IMPORTS.get(rel, "from __future__ import annotations\n")
        write_module(rel, header, "\n\n".join(bodies))

    print(f"Wrote sim modules under {PKG}")


if __name__ == "__main__":
    main()
