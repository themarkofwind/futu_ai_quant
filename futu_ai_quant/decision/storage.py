"""
分析结果持久化：模型输入（portfolio_payload）与输出（decision）分文件保存。

目录
----
- ``data/payloads/``：发给大模型 / 规则引擎的完整输入（便于 review）
- ``data/decisions/``：模型输出建议

同一次分析使用相同时间戳文件名，决策 JSON 内 ``payload_path`` 指向对应输入文件。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from futu_ai_quant.config.settings import DECISIONS_DIR, PAYLOADS_DIR
from futu_ai_quant.utils.files import atomic_write_text


def _analysis_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def save_portfolio_payload_record(
    portfolio_payload: dict[str, Any],
    *,
    required_codes: list[str],
    ts: str | None = None,
    decision_source: str = "deepseek",
) -> Path:
    """
    保存发给大模型（或规则引擎参考）的完整 portfolio 输入。

    写入 ``payload_{ts}.json`` 与 ``latest_payload.json``。
    """
    PAYLOADS_DIR.mkdir(parents=True, exist_ok=True)
    ts = ts or _analysis_timestamp()
    record = {
        "saved_at": datetime.now().isoformat(),
        "analysis_id": ts,
        "decision_source": decision_source,
        "required_codes": required_codes,
        "summary": portfolio_payload.get("summary"),
        "portfolio_payload": portfolio_payload,
    }
    text = json.dumps(record, ensure_ascii=False, indent=2)
    path = PAYLOADS_DIR / f"payload_{ts}.json"
    atomic_write_text(path, text)
    atomic_write_text(PAYLOADS_DIR / "latest_payload.json", text)
    return path


def save_decision_record(
    decision: dict[str, Any],
    *,
    required_codes: list[str],
    payload_summary: dict[str, Any] | None = None,
    payload_path: str | Path | None = None,
    ts: str | None = None,
) -> Path:
    """保存决策输出；``payload_path`` 关联同轮分析的输入文件。"""
    DECISIONS_DIR.mkdir(parents=True, exist_ok=True)
    ts = ts or _analysis_timestamp()
    record: dict[str, Any] = {
        "saved_at": datetime.now().isoformat(),
        "analysis_id": ts,
        "required_codes": required_codes,
        "payload_summary": payload_summary,
        "decision": decision,
    }
    if payload_path is not None:
        record["payload_path"] = str(payload_path)
    text = json.dumps(record, ensure_ascii=False, indent=2)
    path = DECISIONS_DIR / f"decision_{ts}.json"
    atomic_write_text(path, text)
    atomic_write_text(DECISIONS_DIR / "latest.json", text)
    return path


def save_analysis_artifacts(
    portfolio_payload: dict[str, Any],
    decision: dict[str, Any],
    *,
    required_codes: list[str],
    decision_source: str = "deepseek",
) -> tuple[Path, Path]:
    """
    同时间戳保存输入与输出，便于对照 review。

    Returns
    -------
    (payload_path, decision_path)
    """
    ts = _analysis_timestamp()
    payload_path = save_portfolio_payload_record(
        portfolio_payload,
        required_codes=required_codes,
        ts=ts,
        decision_source=decision_source,
    )
    decision_path = save_decision_record(
        decision,
        required_codes=required_codes,
        payload_summary=portfolio_payload.get("summary"),
        payload_path=payload_path,
        ts=ts,
    )
    return payload_path, decision_path
