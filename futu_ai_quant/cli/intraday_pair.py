"""
同时启动主力实时做 T + 多标的轮询（两个子进程，各自热加载 .env）。

默认：
- ``futu-intraday-t`` → 华虹 ``HK.01347``
- ``futu-intraday-watch`` → 阿里 + 腾讯 ``HK.09988,HK.00700``

启动方式
--------
::

    python -m futu_ai_quant.cli.intraday_pair
    futu-intraday-pair
"""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys
import time

from dotenv import load_dotenv

from futu_ai_quant.brokers.futu.intraday_monitor import log_intraday_t
from futu_ai_quant.strategy import intraday_t_settings as its


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="同时运行华虹实时做T + 阿里/腾讯轮询（Bark）",
    )
    parser.add_argument(
        "--primary-code",
        default=None,
        help=f"实时监控标的（默认 {its.INTRADAY_T_CODE}）",
    )
    parser.add_argument(
        "--watch-codes",
        default=None,
        help=f"轮询标的逗号分隔（默认 {its.INTRADAY_T_CODES}）",
    )
    parser.add_argument(
        "--no-bark",
        action="store_true",
        help="两个子进程都禁用 Bark",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    its.refresh_from_environ()
    args = parse_args()

    primary = args.primary_code or its.INTRADAY_T_CODE
    watch_codes = args.watch_codes or its.INTRADAY_T_CODES
    py = sys.executable

    t_cmd = [py, "-u", "-m", "futu_ai_quant.cli.intraday_t"]
    w_cmd = [py, "-u", "-m", "futu_ai_quant.cli.intraday_watch"]
    # 仅当用户显式指定时才写入 CLI，否则子进程从 .env 读取并可热加载标的列表
    if args.primary_code:
        t_cmd.extend(["--code", args.primary_code])
    if args.watch_codes:
        w_cmd.extend(["--codes", args.watch_codes])
    if args.no_bark:
        t_cmd.append("--no-bark")
        w_cmd.append("--no-bark")

    log_intraday_t(f"组合启动 | 实时={primary} | 轮询={watch_codes}")
    log_intraday_t(
        "修改 .env 后约 2 秒热加载策略阈值/价差/轮询间隔；"
        "未用 --primary-code/--watch-codes 时也可热加载标的列表"
        "（实时标的变更仍需重启）"
    )

    procs = [
        subprocess.Popen(t_cmd),
        subprocess.Popen(w_cmd),
    ]
    shutdown = False

    def _stop(_signum: int, _frame: object) -> None:
        nonlocal shutdown
        shutdown = True
        log_intraday_t("收到退出信号，正在停止子进程...")
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    try:
        while not shutdown:
            for p in procs:
                code = p.poll()
                if code is not None:
                    log_intraday_t(f"子进程退出 code={code}，正在停止其余进程...")
                    shutdown = True
                    break
            if shutdown:
                break
            time.sleep(1)
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        log_intraday_t("组合进程已全部停止")


if __name__ == "__main__":
    main()
