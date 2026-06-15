"""
信号级历史回测 CLI（规则引擎，不调用 LLM）。
"""

from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv
from futu import OpenQuoteContext

from futu_ai_quant.backtest.signals import run_signal_backtest
from futu_ai_quant.utils.logging import log


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="港股波段信号历史回测（规则引擎）")
    parser.add_argument(
        "--code",
        required=True,
        help="正股代码，如 HK.09988",
    )
    parser.add_argument(
        "--pl-ratio",
        type=float,
        default=-30.0,
        help="模拟持仓盈亏比例（%%），用于分层策略，默认 -30",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 输出完整结果",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv()

    host = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))

    quote_ctx: OpenQuoteContext | None = None
    try:
        log("连接", f"正在连接 Futu OpenD {host}:{port} ...")
        quote_ctx = OpenQuoteContext(host=host, port=port)
        result = run_signal_backtest(quote_ctx, args.code, pl_ratio=args.pl_ratio)

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return

        if result.get("error"):
            log("回测", f"{args.code} 失败: {result['error']}")
            return

        stats = result.get("stats") or {}
        print(f"\n===== 信号回测 {args.code} (pl_ratio={args.pl_ratio}%) =====")
        print(f"信号总数: {result.get('signal_count', 0)} "
              f"(买入={result.get('buy_count', 0)}, 卖出={result.get('sell_count', 0)})")
        print(f"买入 5日平均前瞻收益: {stats.get('buy_avg_forward_5d_pct')}% "
              f"胜率: {stats.get('buy_win_rate_5d_pct')}%")
        print(f"卖出 5日平均前瞻收益: {stats.get('sell_avg_forward_5d_pct')}% "
              f"胜率: {stats.get('sell_win_rate_5d_pct')}%")
        print(f"买入 10日平均前瞻收益: {stats.get('buy_avg_forward_10d_pct')}%")
        print(f"卖出 10日平均前瞻收益: {stats.get('sell_avg_forward_10d_pct')}%")
        print()
    finally:
        if quote_ctx is not None:
            quote_ctx.close()
