#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
根目录兼容入口 — 基于分析决策的本地模拟交易。

用法
----
初始化（镜像真实持仓）::

    python sim_trader.py --init-mirror

按最新决策跑一轮::

    python sim_trader.py --source latest --once

分析 + 模拟一步完成::

    python sim_trader.py --source main --once

查看绩效::

    python sim_trader.py --report

实际逻辑在 ``futu_ai_quant.cli.sim``。详见 ``docs/GUIDE.md``。
"""

from futu_ai_quant.cli.sim import main

if __name__ == "__main__":
    main()
