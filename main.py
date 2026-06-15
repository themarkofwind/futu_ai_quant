#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根目录兼容入口 — 港股持仓量化分析。

用法
----
单次分析（调试推荐）::

    python main.py --once

不调用 DeepSeek，仅用规则引擎::

    python main.py --once --no-ai

常驻循环（盘中约 30 分钟、盘外约 4 小时一轮）::

    python main.py

实际逻辑在 ``futu_ai_quant.cli.analyze``；本文件仅转发并保留历史 ``from main import ...`` 兼容导出。

详见 ``docs/GUIDE.md``。
"""

from futu_ai_quant.cli.analyze import main

# 向后兼容：sim_trader 等脚本曾 from main import ...
from futu_ai_quant.brokers.futu.client import OpenHKTradeContext
from futu_ai_quant.brokers.futu.options import _build_option_quote_leg
from futu_ai_quant.brokers.futu.positions import get_position_list, maybe_unlock_trade
from futu_ai_quant.brokers.futu.quotes import fetch_snapshot_map
from futu_ai_quant.config.settings import (
    DECISIONS_DIR,
    OPTION_MAX_DAYS,
    OPTION_MIN_DAYS,
)
from futu_ai_quant.domain.positions import classify_positions, is_option_code
from futu_ai_quant.market.session import resolve_analysis_interval
from futu_ai_quant.pipeline.cycle import run_analysis_cycle

__all__ = [
    "DECISIONS_DIR",
    "OPTION_MAX_DAYS",
    "OPTION_MIN_DAYS",
    "OpenHKTradeContext",
    "_build_option_quote_leg",
    "classify_positions",
    "fetch_snapshot_map",
    "get_position_list",
    "is_option_code",
    "main",
    "maybe_unlock_trade",
    "resolve_analysis_interval",
    "run_analysis_cycle",
]

if __name__ == "__main__":
    main()
