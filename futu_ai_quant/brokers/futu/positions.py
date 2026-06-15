"""
Futu OpenD 港股交易接口封装。

API 映射
--------
- ``get_position_list`` → ``OpenSecTradeContext.position_list_query``（实盘港股）
- ``maybe_unlock_trade`` → ``unlock_trade``（需 ``FUTU_TRADE_UNLOCK_PWD``）
"""

from __future__ import annotations

import os

import pandas as pd
from futu import RET_OK, OpenSecTradeContext, TrdEnv, TrdMarket

from futu_ai_quant.utils.logging import log
from futu_ai_quant.utils.retry import retry_call


def get_position_list(trade_ctx: OpenSecTradeContext) -> tuple[int, pd.DataFrame | str]:
    """
    查询港股实盘持仓列表。

    Futu API: ``position_list_query(trd_env=REAL, position_market=HK)``

    Returns (ret_code, DataFrame | error_msg)。
    """
    return retry_call(
        lambda: trade_ctx.position_list_query(
            trd_env=TrdEnv.REAL,
            position_market=TrdMarket.HK,
            refresh_cache=True,
        ),
        label="持仓查询",
        expect_ret_ok=True,
    )


def maybe_unlock_trade(trade_ctx: OpenSecTradeContext) -> None:
    """若配置了 FUTU_TRADE_UNLOCK_PWD 则调用 unlock_trade 解锁实盘查询。"""
    unlock_pwd = os.getenv("FUTU_TRADE_UNLOCK_PWD", "").strip()
    if not unlock_pwd:
        log("交易", "未配置 FUTU_TRADE_UNLOCK_PWD，跳过解锁（若查询失败请配置交易密码）")
        return

    ret, msg = trade_ctx.unlock_trade(unlock_pwd)
    if ret == RET_OK:
        log("交易", "交易解锁成功")
    else:
        log("交易", f"交易解锁失败: {msg}")

