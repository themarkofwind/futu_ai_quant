"""
自选股定时分析命令行入口。

启动方式
--------
- ``python -m futu_ai_quant.cli.watchlist``
- ``futu-watchlist``（pip install -e . 后）

按港股三槽推送：盘前竞价 / 午后开盘 / 收盘前半小时。
不含个人持仓与成交数据；PushPlus 默认走群组 ``PUSHPLUS_TOPIC``。
"""

from __future__ import annotations

import argparse
import os
import time
import traceback
from datetime import datetime, timedelta

from dotenv import load_dotenv
from futu import OpenQuoteContext

from futu_ai_quant.brokers.futu.market_state import FutuMarketSessionGate
from futu_ai_quant.config.watchlist import (
    SLOT_LABELS,
    load_watchlist_codes,
    slot_label,
)
from futu_ai_quant.llm.cli import add_llm_cli_arguments, apply_llm_cli_overrides, log_llm_runtime_config
from futu_ai_quant.llm.client import create_llm_client
from futu_ai_quant.market.watchlist_schedule import (
    next_watchlist_slot,
    seconds_until,
    should_run_watchlist_slot,
)
from futu_ai_quant.notify.pushplus import (
    notify_watchlist_decision,
    pushplus_is_configured,
    send_pushplus,
)
from futu_ai_quant.pipeline.watchlist_cycle import run_watchlist_cycle
from futu_ai_quant.utils.logging import log


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="港股自选股定时分析（无个人持仓）")
    parser.add_argument(
        "--once",
        action="store_true",
        help="立即跑一轮后退出（不进入三槽等待）",
    )
    parser.add_argument(
        "--slot",
        choices=["auction", "lunch", "preclose", "manual"],
        default=None,
        help="标注推送时段；--once 时默认 manual",
    )
    parser.add_argument(
        "--codes",
        default=None,
        help="逗号分隔自选代码，覆盖配置文件与 WATCHLIST_CODES",
    )
    parser.add_argument(
        "--codes-file",
        default=None,
        help="自选配置 JSON 路径（默认 data/watchlist/codes.json）",
    )
    ai_group = parser.add_mutually_exclusive_group()
    ai_group.add_argument(
        "--ai",
        dest="use_ai",
        action="store_true",
        help="启用 LLM 分析（默认）",
    )
    ai_group.add_argument(
        "--no-ai",
        dest="use_ai",
        action="store_false",
        help="仅使用规则引擎，不调用 LLM",
    )
    parser.set_defaults(use_ai=True)
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="禁用 PushPlus 推送",
    )
    parser.add_argument(
        "--test-pushplus",
        action="store_true",
        help="发送一条自选测试推送后退出（不连接 OpenD）",
    )
    add_llm_cli_arguments(parser)
    return parser.parse_args()


def _resolve_slot(args: argparse.Namespace) -> tuple[str | None, str | None]:
    if args.slot == "manual" or (args.once and args.slot is None):
        return "manual", "手动"
    if args.slot:
        return args.slot, slot_label(args.slot)
    return None, None


def _run_one(
    quote_ctx: OpenQuoteContext,
    ai_client,
    codes: list[str],
    *,
    use_ai: bool,
    slot_key: str | None,
    slot_label_text: str | None,
    do_push: bool,
) -> None:
    result = run_watchlist_cycle(
        quote_ctx,
        codes,
        ai_client,
        use_ai=use_ai,
        slot_key=slot_key,
        slot_label_text=slot_label_text,
    )
    if result is None:
        log("自选", "本轮未产出决策，跳过推送")
        return
    if do_push:
        notify_watchlist_decision(result)


def _load_hk_trading_days(
    gate: FutuMarketSessionGate,
    *,
    now: datetime | None = None,
    days_ahead: int = 21,
) -> tuple[dict[str, dict[str, str]] | None, bool]:
    """返回 (trading_days映射, api_failed)。"""
    now = now or datetime.now()
    start = now.strftime("%Y-%m-%d")
    end = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
    rows = gate.fetch_trading_days("HK", start, end)
    if rows is None:
        return None, True
    return rows, False


def main() -> None:
    args = parse_args()
    load_dotenv()
    apply_llm_cli_overrides(args)

    if args.test_pushplus:
        if not pushplus_is_configured():
            log("PushPlus", "未配置：请在 .env 设置 PUSHPLUS_ENABLED=1 和 PUSHPLUS_TOKEN")
            return
        ok, msg = send_pushplus(
            "自选分析测试",
            "PushPlus 自选通路正常（可走群组 PUSHPLUS_TOPIC）。",
        )
        if ok:
            log("PushPlus", f"测试推送成功: {msg}")
        else:
            log("PushPlus", f"测试推送失败: {msg}")
        return

    codes = load_watchlist_codes(codes_arg=args.codes, codes_file=args.codes_file)
    use_ai = bool(args.use_ai)
    do_push = (not args.no_push) and pushplus_is_configured()

    if use_ai:
        log_llm_runtime_config()
        ai_client = create_llm_client()
    else:
        log("规则", "已指定 --no-ai，跳过 LLM，仅用规则引擎")
        ai_client = None

    host = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))
    quote_ctx: OpenQuoteContext | None = None

    try:
        log("连接", f"正在连接 Futu OpenD {host}:{port} ...")
        quote_ctx = OpenQuoteContext(host=host, port=port)
        session_gate = FutuMarketSessionGate(quote_ctx)
        log("连接", "行情上下文初始化完成")
        log("自选", f"标的 ({len(codes)}): {', '.join(codes)}")
        log("循环", "交易日门禁=OpenD request_trading_days（跳过假期；半日市跳过午后槽）")
        if do_push:
            topic = os.getenv("PUSHPLUS_TOPIC", "").strip()
            log("PushPlus", f"已启用（{'群组 ' + topic if topic else '一对一'}）")
        else:
            log("PushPlus", "未推送（未配置或 --no-push）")

        if args.once:
            slot_key, slot_label_text = _resolve_slot(args)
            log("循环", f"单次运行 slot={slot_label_text or '手动'}（--once 不拦假期）")
            _run_one(
                quote_ctx,
                ai_client,
                codes,
                use_ai=use_ai,
                slot_key=slot_key,
                slot_label_text=slot_label_text,
                do_push=do_push,
            )
            return

        log("循环", "三槽守护模式：盘前竞价 / 午后开盘 / 收盘前半小时")
        log(
            "循环",
            "时段: "
            + ", ".join(f"{k}={SLOT_LABELS[k]}" for k in ("auction", "lunch", "preclose")),
        )
        while True:
            try:
                codes = load_watchlist_codes(codes_arg=args.codes, codes_file=args.codes_file)
                trading_days, api_failed = _load_hk_trading_days(session_gate)
                if api_failed:
                    log("循环", "交易日历拉取失败，暂按本地周一至五调度（触发时再确认）")
                slot_key, fire_at, label = next_watchlist_slot(trading_days=trading_days)
                wait_sec = seconds_until(fire_at)
                log(
                    "循环",
                    f"下一槽 {label}（{slot_key}）@ {fire_at.strftime('%Y-%m-%d %H:%M')}，"
                    f"等待 {int(wait_sec)} 秒...",
                )
                while True:
                    remaining = seconds_until(fire_at)
                    if remaining <= 0:
                        break
                    time.sleep(min(remaining, 30.0))

                # 触发时重新拉日历，避免长等待后跨假期
                session_gate.invalidate()
                trading_days, api_failed = _load_hk_trading_days(session_gate)
                ok, reason = should_run_watchlist_slot(
                    slot_key,
                    trading_days=trading_days,
                    day=fire_at,
                    api_failed=api_failed,
                )
                if not ok:
                    log("循环", f"跳过槽位 {label}: {reason}")
                    time.sleep(1)
                    continue

                log("循环", f"到达槽位 {label}，开始分析（{reason}）...")
                codes = load_watchlist_codes(codes_arg=args.codes, codes_file=args.codes_file)
                _run_one(
                    quote_ctx,
                    ai_client,
                    codes,
                    use_ai=use_ai,
                    slot_key=slot_key,
                    slot_label_text=label,
                    do_push=do_push,
                )
            except Exception as exc:
                log("循环", f"本轮异常: {exc}")
                traceback.print_exc()
                time.sleep(60)
    finally:
        log("连接", "正在释放 Futu 连接...")
        if quote_ctx is not None:
            quote_ctx.close()
        log("连接", "连接已关闭，脚本退出")


if __name__ == "__main__":
    main()
