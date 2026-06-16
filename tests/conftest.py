"""pytest 全局 fixture：隔离进程内缓存，避免测试间串扰。"""

from __future__ import annotations

import pytest

from futu_ai_quant.history.trades import clear_trade_history_memory_cache
from futu_ai_quant.indicators import kline_cache


@pytest.fixture(autouse=True)
def _isolate_process_caches() -> None:
    clear_trade_history_memory_cache()
    kline_cache.clear_kline_cache()
    yield
    clear_trade_history_memory_cache()
    kline_cache.clear_kline_cache()
