"""日内 T+0 先卖后买监控策略参数（环境变量可覆盖）。"""

from __future__ import annotations

import os

INTRADAY_T_CODE = os.getenv("INTRADAY_T_CODE", "HK.09988")
# 多标的监控（intraday_watch）：逗号分隔，可港股/美股混合，如 "HK.09988,US.AAPL"
INTRADAY_T_CODES = os.getenv("INTRADAY_T_CODES", "")
INTRADAY_T_LOT_SIZE = int(os.getenv("INTRADAY_T_LOT_SIZE", "1000"))
# 单次做 T 占正股持仓比例（%）；>0 时从 Futu 持仓自动折算整手，0 则用固定 INTRADAY_T_LOT_SIZE
INTRADAY_T_LOT_PCT = float(os.getenv("INTRADAY_T_LOT_PCT", "30"))
INTRADAY_T_TARGET_SPREAD = float(os.getenv("INTRADAY_T_TARGET_SPREAD", "1.2"))
# 1=按手续费自动抬高目标价差下限（与 INTRADAY_T_TARGET_SPREAD 取较大值）
INTRADAY_T_TARGET_SPREAD_AUTO = os.getenv("INTRADAY_T_TARGET_SPREAD_AUTO", "1").lower() not in (
    "0",
    "false",
    "no",
)
# 目标价差相对往返费用的安全系数（净利建议 ≥ 费用×该倍数）
INTRADAY_T_MIN_PROFIT_COST_RATIO = float(os.getenv("INTRADAY_T_MIN_PROFIT_COST_RATIO", "2.0"))
# 美股费用估算（富途常见档位，可按账户实际费率覆盖）
INTRADAY_T_US_COMMISSION_PER_SHARE = float(os.getenv("INTRADAY_T_US_COMMISSION_PER_SHARE", "0.0049"))
INTRADAY_T_US_MIN_COMMISSION = float(os.getenv("INTRADAY_T_US_MIN_COMMISSION", "0.99"))
INTRADAY_T_US_PLATFORM_FEE = float(os.getenv("INTRADAY_T_US_PLATFORM_FEE", "0"))
# 多标的轮询间隔（秒）：交易时段内每隔该时长拉取一次各标的最新 5 分钟 K 线
INTRADAY_T_POLL_SEC = int(os.getenv("INTRADAY_T_POLL_SEC", "60"))

INTRADAY_T_BOLL_LENGTH = int(os.getenv("INTRADAY_T_BOLL_LENGTH", "20"))
INTRADAY_T_BOLL_STD = float(os.getenv("INTRADAY_T_BOLL_STD", "2"))
INTRADAY_T_RSI_LENGTH = int(os.getenv("INTRADAY_T_RSI_LENGTH", "14"))
INTRADAY_T_RSI_SELL = float(os.getenv("INTRADAY_T_RSI_SELL", "75"))
INTRADAY_T_RSI_BUY = float(os.getenv("INTRADAY_T_RSI_BUY", "35"))
INTRADAY_T_VWAP_PREMIUM = float(os.getenv("INTRADAY_T_VWAP_PREMIUM", "1.015"))
INTRADAY_T_VWAP_DISCOUNT = float(os.getenv("INTRADAY_T_VWAP_DISCOUNT", "0.985"))

INTRADAY_T_KLINE_WINDOW = int(os.getenv("INTRADAY_T_KLINE_WINDOW", "120"))
INTRADAY_T_HISTORY_BARS = int(os.getenv("INTRADAY_T_HISTORY_BARS", "60"))
INTRADAY_T_STATUS_INTERVAL_SEC = int(os.getenv("INTRADAY_T_STATUS_INTERVAL_SEC", "30"))
# 本地补帧评估节拍：0=关闭；建议 1~3 秒（仅用最近一次 RT_DATA 的 price/vwap，不额外请求 OpenD）
INTRADAY_T_EVAL_TICK_SEC = float(os.getenv("INTRADAY_T_EVAL_TICK_SEC", "2"))

# 强趋势防御：最新 5 分钟 K 线放量 + 连续 N 根收盘站上布林上轨
INTRADAY_T_VOLUME_SURGE_RATIO = float(os.getenv("INTRADAY_T_VOLUME_SURGE_RATIO", "2.5"))
INTRADAY_T_CONSECUTIVE_ABOVE_BAND = int(os.getenv("INTRADAY_T_CONSECUTIVE_ABOVE_BAND", "3"))

# Bark 推送（见 notify/bark.py）
# BARK_ENABLED=1
# BARK_DEVICE_KEY=your_bark_device_key
# BARK_SERVER=https://api.day.app
# BARK_GROUP=日内做T
# BARK_LEVEL=timeSensitive
# BARK_NOTIFY_WARNING=0
