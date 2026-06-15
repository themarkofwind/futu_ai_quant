"""Futu OpenD 等外部调用的简单重试。"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from futu import RET_OK

from futu_ai_quant.utils.logging import log

T = TypeVar("T")


def retry_call(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    label: str = "",
    expect_ret_ok: bool = False,
) -> T:
    """
    重试可调用对象；``expect_ret_ok=True`` 时要求返回元组且首元素为 ``RET_OK``。
    """
    last: T | None = None
    tag = label or "API"
    for attempt in range(1, max_attempts + 1):
        try:
            result = fn()
            last = result
            if expect_ret_ok:
                if isinstance(result, tuple) and result and result[0] == RET_OK:
                    return result
                if attempt < max_attempts:
                    err = result[1] if isinstance(result, tuple) and len(result) > 1 else result
                    log("重试", f"{tag} 第{attempt}次失败: {err}")
            else:
                return result
        except Exception as exc:
            if attempt >= max_attempts:
                raise
            log("重试", f"{tag} 第{attempt}次异常: {exc}")
        if attempt < max_attempts:
            time.sleep(base_delay * (2 ** (attempt - 1)))
    assert last is not None
    return last
