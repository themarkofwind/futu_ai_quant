"""
日内 T+0 先卖后买监控 CLI。

启动方式
--------
- ``python -m futu_ai_quant.cli.intraday_t``
- ``futu-intraday-t``（pip install -e . 后）

示例
----
::

    futu-intraday-t --code HK.09988
    futu-intraday-t --code HK.00700 --lot-size 500
    futu-intraday-t --replay --code HK.09988
    futu-intraday-t --replay --replay-day 2026-06-16 --no-bark
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

from dotenv import load_dotenv
from futu import OpenQuoteContext, OpenSecTradeContext, RET_OK, SubType

from futu_ai_quant.brokers.futu.intraday_kline import fetch_intraday_5m_klines
from futu_ai_quant.brokers.futu.intraday_monitor import IntradayTMonitor, log_intraday_t
from futu_ai_quant.brokers.futu.positions import maybe_unlock_trade, trd_market_for_code
from futu_ai_quant.market.codes import normalize_stock_code
from futu_ai_quant.market.session import currency_of_market, is_trading_session, market_of_code
from futu_ai_quant.notify.bark import (
    bark_is_configured,
    bark_notify_warning,
    bark_title_for_signal,
    send_bark,
    send_bark_async,
)
from futu_ai_quant.strategy.intraday_t import IntradayTContext, SignalKind
from futu_ai_quant.strategy.intraday_t_cost import resolve_intraday_t_target_spread
from futu_ai_quant.strategy.intraday_t_lot import resolve_intraday_t_lot_size
from futu_ai_quant.strategy.intraday_t_replay import latest_trading_day, replay_intraday_t
from futu_ai_quant.strategy.intraday_t_settings import (
    INTRADAY_T_CODE,
    INTRADAY_T_EVAL_TICK_SEC,
    INTRADAY_T_LOT_PCT,
    INTRADAY_T_LOT_SIZE,
    INTRADAY_T_MIN_PROFIT_COST_RATIO,
    INTRADAY_T_STATUS_INTERVAL_SEC,
    INTRADAY_T_TARGET_SPREAD,
    INTRADAY_T_TARGET_SPREAD_AUTO,
)
from futu_ai_quant.utils.logging import log
from futu_ai_quant.utils.retry import retry_call


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="港股/美股日内 T+0 双向做 T 监控（BOLL + RSI + VWAP）",
    )
    parser.add_argument(
        "--code",
        default=INTRADAY_T_CODE,
        help=f"标的代码（默认 {INTRADAY_T_CODE}）",
    )
    parser.add_argument(
        "--lot-size",
        type=int,
        default=None,
        help=f"固定做 T 股数（指定则忽略持仓比例；未指定则按 INTRADAY_T_LOT_PCT 自动折算）",
    )
    parser.add_argument(
        "--lot-pct",
        type=float,
        default=None,
        help=f"做 T 占持仓比例 %%（默认 {INTRADAY_T_LOT_PCT:g}，0=用固定 INTRADAY_T_LOT_SIZE）",
    )
    parser.add_argument(
        "--target-spread",
        type=float,
        default=None,
        help=f"目标净价差下限（默认 {INTRADAY_T_TARGET_SPREAD}，并与费用估算取较大值）",
    )
    parser.add_argument(
        "--min-profit-cost-ratio",
        type=float,
        default=None,
        help=f"目标价差相对往返费用的安全系数（默认 {INTRADAY_T_MIN_PROFIT_COST_RATIO:g}）",
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
        "--eval-tick",
        type=float,
        default=INTRADAY_T_EVAL_TICK_SEC,
        help=f"本地补帧评估节拍秒（0=关闭，默认 {INTRADAY_T_EVAL_TICK_SEC:g}）",
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
        "--replay",
        action="store_true",
        help="历史 5 分钟 K 线回放（非交易时段也可演练信号与 Bark）",
    )
    parser.add_argument(
        "--replay-day",
        default=None,
        help="回放指定交易日 YYYY-MM-DD（默认取历史数据最近一天）",
    )
    parser.add_argument(
        "--replay-bars",
        type=int,
        default=200,
        help="回放前向 OpenD 拉取的历史 K 线根数（默认 200）",
    )
    parser.add_argument(
        "--replay-speed",
        type=float,
        default=0.0,
        help="回放节拍秒（0=尽快跑完，默认 0）",
    )
    return parser.parse_args()


def _run_replay(args: argparse.Namespace, code: str, ctx: IntradayTContext) -> int:
    host = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))
    quote_ctx: OpenQuoteContext | None = None

    notify_kinds = {
        SignalKind.SELL,
        SignalKind.BUY_T,
        SignalKind.BUY_BACK,
        SignalKind.SELL_OFF,
    }
    if bark_notify_warning():
        notify_kinds.add(SignalKind.WARNING)

    def _on_event(event, header: str) -> None:
        log_intraday_t(f"[回放] {header}\n{event.message}")
        if not bark_is_configured() or event.kind not in notify_kinds:
            return
        title = bark_title_for_signal(event.kind.value, code)
        # 回放结束后进程立即退出，异步推送会被 daemon 线程掐断
        ok, msg = send_bark(title, f"{header}\n{event.message}")
        if ok:
            log_intraday_t(f"Bark 推送成功: {title}")
        else:
            log_intraday_t(f"Bark 推送失败: {msg}")

    try:
        log("连接", f"正在连接 Futu OpenD {host}:{port}（历史回放）...")
        quote_ctx = OpenQuoteContext(host=host, port=port)

        def _sub():
            return quote_ctx.subscribe(
                [code],
                [SubType.K_5M],
                subscribe_push=False,
            )

        ret, err = retry_call(_sub, label="replay_subscribe_k5m", expect_ret_ok=True)
        if ret != RET_OK:
            log_intraday_t(f"K 线订阅失败: {err}")
            return 1

        kline, source = fetch_intraday_5m_klines(
            quote_ctx,
            code,
            window=args.replay_bars,
            history_bars=args.replay_bars,
            prefer_cur_kline=True,
        )
        log_intraday_t(f"回放 K 线来源: {source}")

        replay_day = args.replay_day or latest_trading_day(kline)
        log_intraday_t(
            f"开始历史回放 | 标的={code} | 交易日={replay_day} | "
            f"拉取K线={len(kline)} 根 | 节拍={args.replay_speed:g}s | "
            f"Bark={'开启' if bark_is_configured() else '关闭'}"
        )

        result = replay_intraday_t(
            kline,
            code=code,
            ctx=ctx,
            day=replay_day,
            speed_sec=args.replay_speed,
            on_event=_on_event,
        )

        log_intraday_t(
            f"回放完成 | 交易日={result.day} | K线={result.bars_total} 根 | "
            f"评估点={result.ticks_processed} | "
            f"卖出T={result.sell_count} | 买入T={result.buy_t_count} | "
            f"买回={result.buy_back_count} | 卖出平仓={result.sell_off_count} | "
            f"预警={result.warning_count}"
        )
        if result.sell_count == 0 and result.buy_back_count == 0 and result.buy_t_count == 0:
            log_intraday_t("当日未触发买卖信号（属正常情况，可换 --replay-day 或调低阈值试演）")
        return 0
    except Exception as exc:
        log_intraday_t(f"回放异常退出: {exc}")
        return 1
    finally:
        if quote_ctx is not None:
            quote_ctx.close()


def _resolve_lot_size(
    args: argparse.Namespace,
    quote_ctx: OpenQuoteContext,
    trade_ctx: OpenSecTradeContext,
    code: str,
) -> int:
    if args.lot_size is not None and args.lot_size > 0:
        log_intraday_t(f"使用固定做T股数 {args.lot_size}（--lot-size）")
        return args.lot_size

    lot_pct = args.lot_pct if args.lot_pct is not None else INTRADAY_T_LOT_PCT
    if lot_pct <= 0:
        log_intraday_t(f"使用固定做T股数 {INTRADAY_T_LOT_SIZE}（INTRADAY_T_LOT_PCT=0）")
        return INTRADAY_T_LOT_SIZE

    lot, note = resolve_intraday_t_lot_size(
        quote_ctx,
        trade_ctx,
        code,
        lot_pct=lot_pct,
        fallback_lot_size=INTRADAY_T_LOT_SIZE,
    )
    log_intraday_t(note)
    return lot


def _resolve_target_spread(
    args: argparse.Namespace,
    quote_ctx: OpenQuoteContext,
    code: str,
    lot_size: int,
) -> float:
    manual = (
        args.target_spread
        if args.target_spread is not None
        else INTRADAY_T_TARGET_SPREAD
    )
    cost_ratio = (
        args.min_profit_cost_ratio
        if args.min_profit_cost_ratio is not None
        else INTRADAY_T_MIN_PROFIT_COST_RATIO
    )
    spread, note = resolve_intraday_t_target_spread(
        quote_ctx,
        code,
        lot_size=lot_size,
        manual_spread=manual,
        cost_ratio=cost_ratio,
        auto=not args.no_spread_auto and INTRADAY_T_TARGET_SPREAD_AUTO,
    )
    log_intraday_t(note)
    return spread


def main() -> None:
    args = parse_args()
    load_dotenv()

    if args.no_bark:
        os.environ["BARK_ENABLED"] = "0"

    if args.test_bark:
        if not bark_is_configured():
            log_intraday_t("Bark 未配置：请在 .env 设置 BARK_ENABLED=1 和 BARK_DEVICE_KEY")
            sys.exit(1)
        ok, msg = send_bark("做T测试", "Bark 推送配置正常，监控程序可以发送买卖信号。")
        if ok:
            log_intraday_t(f"Bark 测试推送成功: {msg}")
            sys.exit(0)
        log_intraday_t(f"Bark 测试推送失败: {msg}")
        sys.exit(1)

    code = normalize_stock_code(args.code)

    host = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))

    if args.replay:
        ctx = IntradayTContext(
            lot_size=args.lot_size or INTRADAY_T_LOT_SIZE,
            target_spread=args.target_spread or INTRADAY_T_TARGET_SPREAD,
            currency=currency_of_market(market_of_code(code)),
        )
        sys.exit(_run_replay(args, code, ctx))

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
        trade_ctx = OpenSecTradeContext(
            filter_trdmarket=trd_market_for_code(code),
            host=host,
            port=port,
        )
        maybe_unlock_trade(trade_ctx)

        lot_size = _resolve_lot_size(args, quote_ctx, trade_ctx, code)
        target_spread = _resolve_target_spread(args, quote_ctx, code, lot_size)
        ctx = IntradayTContext(
            lot_size=lot_size,
            target_spread=target_spread,
            currency=currency_of_market(market_of_code(code)),
        )

        monitor = IntradayTMonitor(
            quote_ctx,
            code,
            ctx=ctx,
            status_interval_sec=args.status_interval,
            eval_tick_sec=args.eval_tick,
        )
        monitor.check_connection()
        monitor.subscribe()
        monitor.bootstrap_history()
        monitor.log_startup_banner()
        monitor.maybe_print_status(force=True)

        market = market_of_code(code)
        while not shutdown:
            if is_trading_session(market):
                monitor.maybe_print_status()
                monitor.maybe_eval_tick()
                time.sleep(1)
            else:
                log("做T", f"非{market}交易时段，60 秒后重试...")
                time.sleep(60)
    except KeyboardInterrupt:
        shutdown = True
    except Exception as exc:
        log_intraday_t(f"监控异常退出: {exc}")
        sys.exit(1)
    finally:
        log("连接", "正在释放 Futu 连接...")
        if quote_ctx is not None:
            quote_ctx.close()
        if trade_ctx is not None:
            trade_ctx.close()
        log_intraday_t("监控已停止")


if __name__ == "__main__":
    main()
