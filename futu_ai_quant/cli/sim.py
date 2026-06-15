"""
模拟交易命令行入口。

启动方式
--------
- ``python sim_trader.py``
- ``futu-sim``（pip install -e . 后）

典型流程
--------
1. ``--init-mirror`` 或 ``--init-cash`` 初始化 ``data/sim/portfolio.json``
2. ``--source latest --once`` 按 ``data/decisions/latest.json`` 撮合
3. ``--report`` 查看 ``data/sim/metrics.json``

决策来源（``--source``）
-----------------------
- ``latest``：读取最近一次分析保存的决策
- ``main``：先调用 ``run_analysis_cycle`` 再模拟（需 DeepSeek Key）
- ``file``：指定 ``--decision-file`` 路径

执行后端（``--backend`` / ``SIM_BACKEND``）
-------------------------------------------
- ``local``：仅本地 PaperPortfolio 撮合
- ``futu`` / ``both``：额外调用 Futu 模拟盘 ``place_order``
"""

from __future__ import annotations

import argparse
import os
import time
import traceback
from typing import Any

from dotenv import load_dotenv
from futu import OpenQuoteContext, TrdMarket

from futu_ai_quant.brokers.futu.client import OpenHKTradeContext
from futu_ai_quant.brokers.futu.positions import maybe_unlock_trade
from futu_ai_quant.llm.client import create_llm_client
from futu_ai_quant.market.session import resolve_analysis_interval
from futu_ai_quant.sim.broker import FutuSimBroker
from futu_ai_quant.sim.engine import LocalSimEngine
from futu_ai_quant.sim.fees import HKCostModel
from futu_ai_quant.sim.io import print_report
from futu_ai_quant.sim.portfolio import PaperPortfolio
from futu_ai_quant.sim.runner import init_mirror_portfolio, run_sim_cycle
from futu_ai_quant.sim.settings import SIM_BACKEND, SIM_EXECUTION_MODE
from futu_ai_quant.utils.logging import log


def parse_args() -> argparse.Namespace:
    """解析模拟交易 CLI 参数。"""
    parser = argparse.ArgumentParser(description="基于 main.py 建议的港股模拟交易")
    parser.add_argument("--once", action="store_true", help="只运行一轮后退出")
    parser.add_argument(
        "--source",
        choices=["latest", "main", "file"],
        default="latest",
        help="决策来源：latest=最近保存 / main=实时分析 / file=指定文件",
    )
    parser.add_argument("--decision-file", help="source=file 时指定决策 JSON 路径")
    parser.add_argument("--init-mirror", action="store_true", help="用真实持仓初始化模拟账户")
    parser.add_argument("--init-cash", type=float, help="用指定现金初始化空白模拟账户")
    parser.add_argument("--report", action="store_true", help="打印累计绩效后退出")
    parser.add_argument(
        "--backend",
        choices=["local", "futu", "both"],
        default=None,
        help="local=仅本地撮合；futu=同步提交 Futu 模拟盘；both=本地+Futu",
    )
    return parser.parse_args()


def resolve_backend(args: argparse.Namespace) -> str:
    """解析执行后端：CLI ``--backend`` 优先，否则 ``SIM_BACKEND`` 环境变量。"""
    backend = (args.backend or SIM_BACKEND or "local").lower()
    if backend not in ("local", "futu", "both"):
        raise ValueError(f"不支持的 backend: {backend}")
    return backend


def build_engine(
    portfolio: PaperPortfolio,
    trade_ctx: Any | None,
    backend: str,
) -> LocalSimEngine:
    """
    构建本地模拟引擎。

    ``backend`` 为 ``futu`` 或 ``both`` 时挂载 ``FutuSimBroker``，
    在本地成交后同步向 OpenD 模拟盘提交订单。
    """
    futu_broker = None
    if backend in ("futu", "both"):
        if trade_ctx is None:
            raise RuntimeError("backend=futu/both 需要 Futu 交易上下文")
        futu_broker = FutuSimBroker(trade_ctx)
        log("Futu模拟", f"已连接模拟账户 acc_id={futu_broker.acc_id}")
    return LocalSimEngine(portfolio, HKCostModel(), futu_broker=futu_broker)


def main() -> None:
    """
    模拟交易主入口。

    ``--report`` 仅打印绩效；``--init-*`` 初始化账户；
    否则加载已有 portfolio 并循环/单次执行 ``run_sim_cycle``。
    """
    load_dotenv()
    args = parse_args()
    backend = resolve_backend(args)

    if args.report:
        print_report()
        return

    host = os.getenv("FUTU_OPEND_HOST", "127.0.0.1")
    port = int(os.getenv("FUTU_OPEND_PORT", "11111"))
    portfolio = PaperPortfolio()
    portfolio.load()

    quote_ctx: OpenQuoteContext | None = None
    trade_ctx: Any | None = None
    ai_client: Any | None = None

    try:
        log("连接", f"正在连接 Futu OpenD {host}:{port} ...")
        quote_ctx = OpenQuoteContext(host=host, port=port)

        if args.init_mirror or args.init_cash is not None:
            if args.init_mirror:
                trade_ctx = OpenHKTradeContext(filter_trdmarket=TrdMarket.HK, host=host, port=port)
                maybe_unlock_trade(trade_ctx)
                init_mirror_portfolio(quote_ctx, trade_ctx, portfolio)
            else:
                portfolio.init_from_cash(args.init_cash)
            if not args.once:
                log("初始化", "初始化完成。请再运行 sim_trader.py --source latest --once 开始模拟")
                return

        if not portfolio.exists():
            raise RuntimeError(
                "模拟账户未初始化，请先运行 --init-mirror 或 --init-cash 1000000"
            )

        if args.source == "main":
            ai_client = create_llm_client()
            trade_ctx = trade_ctx or OpenHKTradeContext(
                filter_trdmarket=TrdMarket.HK, host=host, port=port
            )
            maybe_unlock_trade(trade_ctx)

        if backend in ("futu", "both"):
            trade_ctx = trade_ctx or OpenHKTradeContext(
                filter_trdmarket=TrdMarket.HK, host=host, port=port
            )
            maybe_unlock_trade(trade_ctx)

        engine = build_engine(portfolio, trade_ctx, backend)
        log("模拟", f"backend={backend} 执行模式={SIM_EXECUTION_MODE}")

        if args.once:
            run_sim_cycle(
                quote_ctx,
                portfolio,
                engine,
                source=args.source,
                decision_file=args.decision_file,
                trade_ctx=trade_ctx,
                ai_client=ai_client,
            )
            return

        while True:
            try:
                run_sim_cycle(
                    quote_ctx,
                    portfolio,
                    engine,
                    source=args.source,
                    decision_file=args.decision_file,
                    trade_ctx=trade_ctx,
                    ai_client=ai_client,
                )
            except Exception as exc:
                log("循环", f"本轮模拟异常: {exc}")
                traceback.print_exc()

            interval_sec, interval_reason = resolve_analysis_interval()
            log("循环", f"{interval_reason}，等待 {interval_sec} 秒...")
            time.sleep(interval_sec)
    finally:
        if quote_ctx is not None:
            quote_ctx.close()
        if trade_ctx is not None:
            trade_ctx.close()
        log("连接", "连接已关闭")
