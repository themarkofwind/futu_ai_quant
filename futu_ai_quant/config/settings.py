"""
全局配置：从环境变量读取，供各层模块引用。

分析相关
--------
- ``ANALYSIS_INTERVAL_SEC``：0=按港股时段自动间隔；>0 为固定秒数
- ``INTRADAY_INTERVAL_SEC`` / ``OFFHOURS_INTERVAL_SEC``：盘中/盘外默认间隔
- ``VOLUME_FILTER``：日K量比策略（session_adjusted / raw / close_only）
- ``DECISIONS_DIR``：决策 JSON 输出目录
- ``PAYLOADS_DIR``：大模型输入 portfolio_payload 存档目录

策略阈值
--------
- ``DEEP_LOSS_THRESHOLD`` / ``MODERATE_LOSS_THRESHOLD``：亏损分层（%）
- ``OPTION_MIN_DAYS`` ~ ``OPTION_MAX_DAYS``：卖权扫描到期范围
- ``PORTFOLIO_MAX_SINGLE_WEIGHT_PCT``：单票集中度预警

数据目录
--------
- ``IV_HISTORY_DIR``：IV Rank 历史快照
- ``TRADE_HISTORY_DIR``：当年成交缓存
- ``KLINE_CACHE_*``：K 线缓存（默认关闭，可选开启）

LLM（见 ``llm/settings.py``）
------------------------------
- ``LLM_PROVIDER`` / ``LLM_MODEL`` / ``DEEPSEEK_API_KEY`` 等

完整列表见 ``.env.example`` 与 ``docs/GUIDE.md``。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

HK_OPTION_CODE_PATTERN = re.compile(r"^[A-Z]+\d{6}[CP]\d+$", re.IGNORECASE)
# ANALYSIS_INTERVAL_SEC=0 表示自动按港股交易时段调节；>0 则使用固定秒数
ANALYSIS_INTERVAL_SEC = int(os.getenv("ANALYSIS_INTERVAL_SEC", "0"))
INTRADAY_INTERVAL_SEC = int(os.getenv("INTRADAY_INTERVAL_SEC", "1800"))  # 交易日盘中默认 30 分钟
OFFHOURS_INTERVAL_SEC = int(os.getenv("OFFHOURS_INTERVAL_SEC", "14400"))  # 非交易时段默认 4 小时
KLINE_COUNT = 60
WEEKLY_KLINE_COUNT = 52
KLINE_CACHE_DIR = Path(os.getenv("KLINE_CACHE_DIR", "data/kline_cache"))
# 默认关闭；效果/限频不理想时可 KLINE_CACHE_ENABLED=1 并设置 TTL（日K TTL 建议小于分析间隔）
KLINE_CACHE_ENABLED = os.getenv("KLINE_CACHE_ENABLED", "0").lower() not in ("0", "false", "no")
KLINE_CACHE_TTL_SEC = int(os.getenv("KLINE_CACHE_TTL_SEC", "0"))
KLINE_WEEKLY_CACHE_TTL_SEC = int(os.getenv("KLINE_WEEKLY_CACHE_TTL_SEC", "14400"))
RSI_LENGTH = 14
BOLL_LENGTH = 20
BOLL_STD = 2
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
ATR_LENGTH = 14
VOLUME_MA_LENGTH = 20
VOLUME_CONFIRM_RATIO = 1.2
# session_adjusted=盘中量比按已过交易时段折算；raw=不折算；close_only=14:00前不校验量比
VOLUME_FILTER = os.getenv("VOLUME_FILTER", "session_adjusted").lower()
MIN_SESSION_VOLUME_FRACTION = 0.05
IV_RANK_HIGH = 70.0
IV_RANK_LOW = 30.0
DEEP_LOSS_THRESHOLD = float(os.getenv("DEEP_LOSS_THRESHOLD", "-50"))
MODERATE_LOSS_THRESHOLD = float(os.getenv("MODERATE_LOSS_THRESHOLD", "0"))
OPTION_STRIKE_ATR_MULT_LOW = float(os.getenv("OPTION_STRIKE_ATR_MULT_LOW", "0.5"))
OPTION_STRIKE_ATR_MULT_HIGH = float(os.getenv("OPTION_STRIKE_ATR_MULT_HIGH", "2.0"))
PORTFOLIO_MAX_SINGLE_WEIGHT_PCT = float(os.getenv("PORTFOLIO_MAX_SINGLE_WEIGHT_PCT", "40"))
OPTION_MIN_DAYS = 14
OPTION_MAX_DAYS = 45
OPTION_DELTA_MIN = 0.10
OPTION_DELTA_MAX = 0.30
MAX_OPTION_CANDIDATES_EACH_SIDE = 2
# 单次卖权建议上限；0 表示仅受备兑股数限制
MAX_OPTION_CONTRACTS_PER_TRADE = int(os.getenv("MAX_OPTION_CONTRACTS_PER_TRADE", "0"))
IV_HISTORY_DIR = Path(os.getenv("IV_HISTORY_DIR", "data/iv_history"))
IV_HISTORY_MIN_SAMPLES = int(os.getenv("IV_HISTORY_MIN_SAMPLES", "10"))
IV_HISTORY_MAX_SAMPLES = int(os.getenv("IV_HISTORY_MAX_SAMPLES", "60"))
SWING_COMMISSION_RATE = float(os.getenv("SWING_COMMISSION_RATE", os.getenv("SIM_COMMISSION_RATE", "0.0003")))
SWING_MIN_COMMISSION = float(os.getenv("SWING_MIN_COMMISSION", os.getenv("SIM_MIN_COMMISSION", "3")))
SWING_PLATFORM_FEE = float(os.getenv("SWING_PLATFORM_FEE", os.getenv("SIM_PLATFORM_FEE", "15")))
SWING_STAMP_DUTY_RATE = float(os.getenv("SWING_STAMP_DUTY_RATE", os.getenv("SIM_STAMP_DUTY_RATE", "0.0013")))
SWING_MIN_PROFIT_COST_RATIO = float(os.getenv("SWING_MIN_PROFIT_COST_RATIO", "2.0"))
DECISIONS_DIR = Path(os.getenv("DECISIONS_DIR", "data/decisions"))
PAYLOADS_DIR = Path(os.getenv("PAYLOADS_DIR", "data/payloads"))
TRADE_HISTORY_DIR = Path(os.getenv("TRADE_HISTORY_DIR", "data/trade_history"))
TRADE_HISTORY_CACHE_HOURS = int(os.getenv("TRADE_HISTORY_CACHE_HOURS", "12"))
TRADE_RECENT_STOCK_COUNT = int(os.getenv("TRADE_RECENT_STOCK_COUNT", os.getenv("TRADE_RECENT_TRADE_COUNT", "5")))
TRADE_RECENT_OPTION_COUNT = int(os.getenv("TRADE_RECENT_OPTION_COUNT", os.getenv("TRADE_RECENT_TRADE_COUNT", "5")))
STOCK_NAMES_DIR = Path(os.getenv("STOCK_NAMES_DIR", "data/stock_names"))
STOCK_NAMES_CACHE_HOURS = int(os.getenv("STOCK_NAMES_CACHE_HOURS", "168"))
STOCK_NAME_ZH_ENRICH = os.getenv("STOCK_NAME_ZH_ENRICH", "1").lower() not in ("0", "false", "no")
FUTU_HISTORY_QUERY_DAYS = 90

# ---------- 宏观风险 overlay ----------
MACRO_RISK_ENABLED = os.getenv("MACRO_RISK_ENABLED", "1").lower() not in ("0", "false", "no")
MACRO_CALENDAR_PATH = Path(os.getenv("MACRO_CALENDAR_PATH", "data/macro_calendar.json"))
MACRO_HSI_CODE = os.getenv("MACRO_HSI_CODE", "HK.800000")
MACRO_GOLD_CODE = os.getenv("MACRO_GOLD_CODE", "HK.02840").strip()  # 留空则禁用黄金指标
MACRO_HSI_5D_DROP_PCT = float(os.getenv("MACRO_HSI_5D_DROP_PCT", "-5"))
MACRO_HSI_TODAY_DROP_PCT = float(os.getenv("MACRO_HSI_TODAY_DROP_PCT", "-2.5"))
MACRO_GOLD_5D_RISE_PCT = float(os.getenv("MACRO_GOLD_5D_RISE_PCT", "3"))
MACRO_FED_BLACKOUT_DAYS = int(os.getenv("MACRO_FED_BLACKOUT_DAYS", "2"))
MACRO_SWING_MULTIPLIER_ELEVATED = float(os.getenv("MACRO_SWING_MULTIPLIER_ELEVATED", "0.7"))
MACRO_SWING_MULTIPLIER_HIGH = float(os.getenv("MACRO_SWING_MULTIPLIER_HIGH", "0.5"))
