"""
多标的日内 T+0 轮询监控 CLI。

与 ``futu-intraday-t``（单标的实时推送）互补：本命令在交易时段内按固定间隔
依次拉取各标的 5 分钟 K 线并评估信号，适合同时关注多只港股/美股。

启动方式
--------
- ``python -m futu_ai_quant.cli.intraday_watch``
- ``futu-intraday-watch``（pip install -e . 后）

示例
----
::

    futu-intraday-watch --codes HK.09988,US.AAPL
    futu-intraday-watch --codes HK.00700 --poll-sec 30
    futu-intraday-watch --once --codes HK.09988
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

from dotenv import load_dotenv
from futu import OpenQuoteContext, OpenSecTradeContext

from futu_ai_quant.brokers.futu.intraday_monitor import log_intraday_t
from futu_ai_quant.brokers.futu.intraday_watch import IntradayTWatch
from futu_ai_quant.brokers.futu.positions import maybe_unlock_trade
from futu_ai_quant.market.codes import parse_stock_codes
from futu_ai_quant.notify.bark import bark_is_configured, send_bark
from futu_ai_quant.strategy.intraday_t_cost import resolve_intraday_t_target_spread
from futu_ai_quant.strategy.intraday_t_lot import resolve_lot_sizes_for_codes
from futu_ai_quant.strategy.intraday_t_settings import (
    INTRADAY_T_CODE,
    INTRADAY_T_CODES,
    INTRADAY_T_LOT_PCT,
    INTRADAY_T_LOT_SIZE,
    INTRADAY_T_MIN_PROFIT_COST_RATIO,
    INTRADAY_T_POLL_SEC,
    INTRADAY_T_STATUS_INTERVAL_SEC,
    INTRADAY_T_TARGET_SPREAD,
    INTRADAY_T_TARGET_SPREAD_AUTO,
)
from futu_ai_quant.utils.logging import log


def parse_args() -> argparse.Namespace:
    default_codes = INTRADAY_T_CODES.strip() or INTRADAY_T_CODE
    parser = argparse.ArgumentParser(
        description="多标的日内 T+0 轮询监控（港股/美股，BOLL + RSI + VWAP）",
    )
    parser.add_argument(
        "--codes",
        default=default_codes,
        help=f"逗号分隔标的（默认 {default_codes}）",
    )
    parser.add_argument(
        "--poll-sec",
        type=int,
        default=INTRADAY_T_POLL_SEC,
        help=f"交易时段内轮询间隔秒（默认 {INTRADAY_T_POLL_SEC}）",
    )
    parser.add_argument(
        "--lot-size",
        type=int,
        default=None,
        help="固定做 T 股数（指定则忽略持仓比例）",
    )
    parser.add_argument(
        "--lot-pct",
        type=float,
        default=None,
        help=f"做 T 占持仓比例 %%（默认 {INTRADAY_T_LOT_PCT:g}）",
    )
    parser.add_argument(
        "--target-spread",
        type=float,
        default=None,
        help=f"目标净价差下限（默认 {INTRADAY_T_TARGET_SPREAD}）",
    )
    parser.add_argument(
        "--min-profit-cost-ratio",
        type=float,
        default=None,
        help=f"费用安全系数（默认 {INTRADAY_T_MIN_PROFIT_COST_RATIO:g}）",
    )
    parser.add_argument(
        "--no-spread-auto",
        action="store_true",
        help="禁用按手续费自动抬高目标价差",
    )
    parser.add_argument(
        "--status-interval",
        type=int,
        default=INTRADAY_T_STATUS_INTERVAL_SEC,
        help=f"心跳日志间隔秒（默认 {INTRADAY_T_STATUS_INTERVAL_SEC}）",
    )
    parser.add_argument(
        "--no-bark",
        action="store_true",
        help="禁用 Bark 推送（覆盖 .env 中的 BARK_ENABLED）",
    )
    parser.add_argument(
        "--test-bark",
        action="store_true",
        help="发送一条测试 Bark 推送后退出",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只轮询一轮后退出（调试用）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_dotenv()

    if args.no_bark:
        os.environ["BARK_ENABLED"] = "0"

    if args.test_bark:
        if not bark_is_configured():
            log_intraday_t("Bark 未配置：请在 .env 设置 BARK_ENABLED=1 和 BARK_DEVICE_KEY")
            sys.exit(1)
        ok, msg = send_bark("做T轮询测试", "多标的轮询监控 Bark 配置正常。")
        if ok:
            log_intraday_t(f"Bark 测试推送成功: {msg}")
            sys.exit(0)
        log_intraday_t(f"Bark 测试推送失败: {msg}")
        sys.exit(1)

    try:
        codes = parse_stock_codes(args.codes, fallback_single=INTRADAY_T_CODE)
    except ValueError as exc:
        log_intraday_t(str(exc))
        sys.exit(1)

    host = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))
    quote_ctx: OpenQuoteContext | None = None
    trade_ctx: OpenSecTradeContext | None = None
    shutdown = False

    def _handle_signal(_signum: int, _frame: object) -> None:
        nonlocal shutdown
        shutdown = True
        log_intraday_t("收到退出信号，正在关闭...")

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    try:
        log("连接", f"正在连接 Futu OpenD {host}:{port} ...")
        quote_ctx = OpenQuoteContext(host=host, port=port)
        trade_ctx = OpenSecTradeContext(host=host, port=port)
        maybe_unlock_trade(trade_ctx)

        lot_by_code: dict[str, int] = {}
        target_by_code: dict[str, float] = {}
        manual_spread = (
            args.target_spread
            if args.target_spread is not None
            else INTRADAY_T_TARGET_SPREAD
        )
        cost_ratio = (
            args.min_profit_cost_ratio
            if args.min_profit_cost_ratio is not None
            else INTRADAY_T_MIN_PROFIT_COST_RATIO
        )
        spread_auto = not args.no_spread_auto and INTRADAY_T_TARGET_SPREAD_AUTO

        if args.lot_size is not None and args.lot_size > 0:
            lot_by_code = {code: args.lot_size for code in codes}
            log_intraday_t(f"各标的固定做T股数 {args.lot_size}")
        else:
            lot_pct = args.lot_pct if args.lot_pct is not None else INTRADAY_T_LOT_PCT
            if lot_pct > 0:
                resolved = resolve_lot_sizes_for_codes(
                    quote_ctx,
                    trade_ctx,
                    codes,
                    lot_pct=lot_pct,
                    fallback_lot_size=INTRADAY_T_LOT_SIZE,
                )
                for code, (lot, note) in resolved.items():
                    lot_by_code[code] = lot
                    log_intraday_t(f"{code} | {note}")

        for code in codes:
            lot = lot_by_code.get(code, INTRADAY_T_LOT_SIZE)
            spread, note = resolve_intraday_t_target_spread(
                quote_ctx,
                code,
                lot_size=lot,
                manual_spread=manual_spread,
                cost_ratio=cost_ratio,
                auto=spread_auto,
            )
            target_by_code[code] = spread
            log_intraday_t(f"{code} | {note}")

        watch = IntradayTWatch(
            quote_ctx,
            codes,
            poll_sec=args.poll_sec,
            status_interval_sec=args.status_interval,
            lot_size=INTRADAY_T_LOT_SIZE,
            lot_by_code=lot_by_code or None,
            target_by_code=target_by_code or None,
            target_spread=manual_spread,
        )
        watch.check_connection()
        watch.subscribe_klines()
        watch.log_startup_banner()

        if args.once:
            if watch.any_market_open():
                count = watch.poll_once()
                log_intraday_t(f"单轮轮询完成，处理 {count} 只标的")
            else:
                log_intraday_t("当前无标的处于交易时段，跳过轮询")
            return

        while not shutdown:
            if watch.any_market_open():
                open_codes = ", ".join(sym.code for sym in watch.open_symbols())
                log_intraday_t(f"开始轮询 | 交易中: {open_codes}")
                watch.poll_once()
                time.sleep(max(args.poll_sec, 1))
            else:
                markets = ", ".join(sorted({sym.market for sym in watch.symbols}))
                log("做T", f"标的市场 {markets} 均非交易时段，60 秒后重试...")
                time.sleep(60)
    except KeyboardInterrupt:
        shutdown = True
    except Exception as exc:
        log_intraday_t(f"轮询监控异常退出: {exc}")
        sys.exit(1)
    finally:
        log("连接", "正在释放 Futu 连接...")
        if quote_ctx is not None:
            quote_ctx.close()
        if trade_ctx is not None:
            trade_ctx.close()
        log_intraday_t("轮询监控已停止")


if __name__ == "__main__":
    main()
