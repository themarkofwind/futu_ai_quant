"""
futu_ai_quant — 港股持仓量化分析与模拟交易。

项目入口
--------
- 分析：``python main.py`` 或 ``python -m futu_ai_quant`` → ``cli.analyze.main``
- 模拟：``python sim_trader.py`` → ``cli.sim.main``
- 单轮分析 API：``pipeline.cycle.run_analysis_cycle``

完整说明见仓库 ``docs/GUIDE.md``。
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
