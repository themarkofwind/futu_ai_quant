"""股票代码规范化与解析。"""

from __future__ import annotations

import re

_KNOWN_PREFIXES = ("HK.", "US.", "SH.", "SZ.")


def normalize_stock_code(raw: str) -> str:
    """
    兼容输入：
    - HK.09988 / US.AAPL（推荐，带市场前缀，原样返回）
    - 09988 / 988（纯数字补齐 HK. 前缀）
    - HK09988（自动转为 HK.09988）
    - AAPL（纯字母默认视为美股，补齐 US. 前缀）
    """
    code = raw.strip().upper()
    for prefix in _KNOWN_PREFIXES:
        if code.startswith(prefix):
            return code
    m = re.match(r"^HK(\d{5,6})$", code)
    if m:
        return f"HK.{m.group(1)}"
    m = re.match(r"^US([A-Z][A-Z0-9.]*)$", code)
    if m:
        return f"US.{m.group(1)}"
    m = re.match(r"^(\d{5,6})$", code)
    if m:
        return f"HK.{m.group(1)}"
    if re.match(r"^[A-Z][A-Z0-9.]*$", code):
        return f"US.{code}"
    return f"HK.{code}"


def parse_stock_codes(
    raw: str | None = None,
    *,
    fallback_single: str | None = None,
) -> list[str]:
    """解析逗号分隔的标的列表，去重并保持顺序。"""
    text = (raw or "").strip()
    if not text and fallback_single:
        text = fallback_single.strip()
    if not text:
        raise ValueError("未指定监控标的")

    seen: set[str] = set()
    codes: list[str] = []
    for part in text.split(","):
        piece = part.strip()
        if not piece:
            continue
        code = normalize_stock_code(piece)
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)
    if not codes:
        raise ValueError("未指定监控标的")
    return codes
