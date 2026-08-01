"""
分析程序命令行入口。

启动方式
--------
- ``python main.py``
- ``python -m futu_ai_quant``
- ``futu-analyze``（pip install -e . 后）

环境变量
--------
- ``FUTU_OPEND_HOST`` / ``FUTU_OPEND_PORT``：OpenD 地址
- ``DEEPSEEK_API_KEY`` / ``DEEPSEEK_BASE_URL``：AI 决策（``--no-ai`` 时可省略 Key）
- ``FUTU_TRADE_UNLOCK_PWD``：实盘持仓查询解锁（可选）
- ``PUSHPLUS_ENABLED`` / ``PUSHPLUS_TOKEN``：持仓分析完成后推送微信（可选）

外部 API
--------
- ``OpenQuoteContext`` / ``OpenSecTradeContext``：连接 Futu OpenD
- ``openai.OpenAI``：调用 DeepSeek Chat Completions
- PushPlus：可选微信摘要推送
"""

from __future__ import annotations

import argparse
import os
import time
import traceback

from dotenv import load_dotenv
from futu import OpenQuoteContext, OpenSecTradeContext, TrdMarket

from futu_ai_quant.brokers.futu.client import OpenHKTradeContext
from futu_ai_quant.brokers.futu.positions import maybe_unlock_trade
from futu_ai_quant.llm.client import create_llm_client
from futu_ai_quant.llm.cli import add_llm_cli_arguments, apply_llm_cli_overrides, log_llm_runtime_config
from futu_ai_quant.market.session import resolve_analysis_interval
from futu_ai_quant.notify.pushplus import (
    notify_analyze_decision,
    pushplus_is_configured,
    send_pushplus,
)
from futu_ai_quant.pipeline.cycle import run_analysis_cycle
from futu_ai_quant.utils.logging import log


def parse_args() -> argparse.Namespace:
    """解析分析 CLI 参数（``--once``、``--no-ai``、``--test-pushplus``）。"""
    parser = argparse.ArgumentParser(description="港股持仓量化分析")
    parser.add_argument(
        "--once",
        action="store_true",
        help="只运行一轮分析后退出，不进入等待循环",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="仅使用规则引擎生成决策，不调用 LLM",
    )
    parser.add_argument(
        "--test-pushplus",
        action="store_true",
        help="发送一条测试 PushPlus 微信推送后退出（不连接 OpenD）",
    )
    add_llm_cli_arguments(parser)
    return parser.parse_args()


def main() -> None:
    """
    分析程序主入口。

    流程
    ----
    1. 加载 ``.env``，创建 Futu 行情/交易上下文
    2. 可选 ``unlock_trade`` 解锁实盘查询
    3. 循环调用 ``run_analysis_cycle``，间隔由 ``resolve_analysis_interval`` 决定
    4. 若配置了 PushPlus，每轮成功决策后推送微信摘要
    5. ``finally`` 中关闭 OpenD 连接

    ``--once`` 时只跑一轮；否则按港股交易时段自动调节等待时间。
    ``--test-pushplus`` 仅验证推送通路，不跑分析。
    """
    args = parse_args()
    load_dotenv()
    apply_llm_cli_overrides(args)

    if args.test_pushplus:
        if not pushplus_is_configured():
            log("PushPlus", "未配置：请在 .env 设置 PUSHPLUS_ENABLED=1 和 PUSHPLUS_TOKEN")
            return
        ok, msg = send_pushplus(
            "持仓分析测试",
            "PushPlus 配置正常，futu-analyze 完成后可推送决策摘要到微信。",
        )
        if ok:
            log("PushPlus", f"测试推送成功: {msg}")
        else:
            log("PushPlus", f"测试推送失败: {msg}")
        return

    host = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))
    use_ai = not args.no_ai

    if use_ai:
        log_llm_runtime_config()
        ai_client = create_llm_client()
    else:
        ai_client = None
    quote_ctx: OpenQuoteContext | None = None
    trade_ctx: OpenSecTradeContext | None = None

    try:
        log("连接", f"正在连接 Futu OpenD {host}:{port} ...")
        quote_ctx = OpenQuoteContext(host=host, port=port)
        trade_ctx = OpenHKTradeContext(filter_trdmarket=TrdMarket.HK, host=host, port=port)
        log("连接", "行情与交易上下文初始化完成")
        if pushplus_is_configured():
            log("PushPlus", "已启用：每轮分析成功后推送微信摘要")

        maybe_unlock_trade(trade_ctx)

        interval_sec, interval_reason = resolve_analysis_interval()
        if args.once:
            log("循环", "单次运行模式（--once）")
        else:
            log("循环", f"运行间隔策略：{interval_reason}")

        while True:
            try:
                result = run_analysis_cycle(quote_ctx, trade_ctx, ai_client, use_ai=use_ai)
                if result is not None:
                    notify_analyze_decision(result)
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
