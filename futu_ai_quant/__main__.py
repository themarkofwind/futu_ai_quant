"""
包入口：``python -m futu_ai_quant`` 与 ``main.py`` 等价。

执行 ``python -m futu_ai_quant [--once] [--no-ai]`` 启动分析 CLI。
"""

from futu_ai_quant.cli.analyze import main

if __name__ == "__main__":
    main()
