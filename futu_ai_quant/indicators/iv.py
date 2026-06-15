from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from futu_ai_quant.config.settings import (
    IV_HISTORY_DIR,
    IV_HISTORY_MAX_SAMPLES,
    IV_HISTORY_MIN_SAMPLES,
    IV_RANK_HIGH,
    IV_RANK_LOW,
    MAX_OPTION_CONTRACTS_PER_TRADE,
)
from futu_ai_quant.utils.files import atomic_write_text
from futu_ai_quant.utils.numbers import safe_float


def calc_max_covered_calls(qty: float, contract_size: int) -> int:
    """备兑卖 Call 最大张数 = 持仓股数 // 每张合约股数。"""
    if contract_size <= 0:
        return 0
    return max(0, int(abs(qty) // contract_size))


def cap_option_contracts(max_contracts: int) -> int:
    if max_contracts <= 0:
        return 0
    if MAX_OPTION_CONTRACTS_PER_TRADE <= 0:
        return max_contracts
    return min(max_contracts, MAX_OPTION_CONTRACTS_PER_TRADE)


def _iv_history_path(stock_code: str) -> Path:
    safe_code = stock_code.replace(".", "_")
    return IV_HISTORY_DIR / f"{safe_code}.json"


def load_iv_history(stock_code: str) -> list[float]:
    path = _iv_history_path(stock_code)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = payload.get("iv_samples", [])
        return [float(v) for v in values if v is not None]
    except Exception:
        return []


def record_iv_scan_snapshot(stock_code: str, candidates: list[dict[str, Any]]) -> None:
    """每次扫描记录代表 IV，用于后续计算历史 IV Rank。"""
    iv_values = [
        safe_float(item.get("implied_volatility"))
        for item in candidates
        if safe_float(item.get("implied_volatility")) is not None
    ]
    if not iv_values:
        return

    representative_iv = sorted(iv_values)[len(iv_values) // 2]
    history = load_iv_history(stock_code)
    history.append(representative_iv)
    history = history[-IV_HISTORY_MAX_SAMPLES:]

    IV_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = _iv_history_path(stock_code)
    atomic_write_text(
        path,
        json.dumps(
            {
                "code": stock_code,
                "iv_samples": history,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )


def compute_historical_iv_rank(stock_code: str, current_iv: float | None) -> float | None:
    if current_iv is None:
        return None
    history = load_iv_history(stock_code)
    if len(history) < IV_HISTORY_MIN_SAMPLES:
        return None
    count_le = sum(1 for value in history if value <= current_iv)
    return round(count_le / len(history) * 100, 1)


def _iv_level_note(rank: float | None) -> str | None:
    if rank is None:
        return None
    if rank >= IV_RANK_HIGH:
        return "IV偏高，卖权权利金较厚"
    if rank <= IV_RANK_LOW:
        return "IV偏低，卖权权利金偏薄"
    return "IV中等"


def annotate_iv_metrics(
    candidates: list[dict[str, Any]],
    stock_code: str,
) -> list[dict[str, Any]]:
    iv_values = [
        safe_float(item.get("implied_volatility"))
        for item in candidates
        if safe_float(item.get("implied_volatility")) is not None
    ]

    min_iv = min(iv_values) if iv_values else None
    max_iv = max(iv_values) if iv_values else None

    for item in candidates:
        iv = safe_float(item.get("implied_volatility"))
        if iv is None:
            item["iv_relative"] = None
            item["iv_rank"] = None
            item["iv_rank_note"] = "无IV数据"
            continue

        if min_iv is None or max_iv is None or len(iv_values) < 2:
            item["iv_relative"] = None
        elif max_iv == min_iv:
            item["iv_relative"] = 50.0
        else:
            item["iv_relative"] = round((iv - min_iv) / (max_iv - min_iv) * 100, 1)

        item["iv_rank"] = compute_historical_iv_rank(stock_code, iv)
        if item["iv_rank"] is not None:
            hist_note = _iv_level_note(item["iv_rank"])
            rel = item.get("iv_relative")
            if rel is not None:
                item["iv_rank_note"] = f"历史IV Rank={item['iv_rank']}（{hist_note}）；当次候选相对IV={rel}"
            else:
                item["iv_rank_note"] = f"历史IV Rank={item['iv_rank']}（{hist_note}）"
        elif item.get("iv_relative") is not None:
            item["iv_rank_note"] = (
                f"历史样本不足（需≥{IV_HISTORY_MIN_SAMPLES}次），"
                f"当次候选相对IV={item['iv_relative']}"
            )
        else:
            item["iv_rank_note"] = f"IV样本不足（需≥{IV_HISTORY_MIN_SAMPLES}次历史扫描）"

    record_iv_scan_snapshot(stock_code, candidates)

    return sorted(
        candidates,
        key=lambda item: (
            item.get("iv_rank") is None,
            -(item.get("iv_rank") or 0),
            item.get("iv_relative") is None,
            -(item.get("iv_relative") or 0),
        ),
    )
