from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from futu_ai_quant.config.settings import DECISIONS_DIR
from futu_ai_quant.sim.jsonl import append_jsonl
from futu_ai_quant.sim.metrics import compute_metrics_from_snapshots
from futu_ai_quant.sim.portfolio import PaperPortfolio
from futu_ai_quant.sim.settings import METRICS_FILE, SNAPSHOTS_FILE
from futu_ai_quant.utils.files import atomic_write_text
from futu_ai_quant.utils.logging import log


def load_decision_record(source: str, decision_file: str | None) -> tuple[dict[str, Any], str]:
    if source == "file":
        if not decision_file:
            raise ValueError("source=file 需要 --decision-file")
        path = Path(decision_file)
    elif source == "latest":
        path = DECISIONS_DIR / "latest.json"
    else:
        raise ValueError(f"未知 decision source: {source}")

    if not path.exists():
        raise FileNotFoundError(f"决策文件不存在: {path}")

    record = json.loads(path.read_text(encoding="utf-8"))
    decision_id = path.stem
    return record, decision_id


def save_snapshot(
    portfolio: PaperPortfolio,
    mtm: dict[str, Any],
    decision_id: str,
    counters: dict[str, int],
) -> None:
    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "decision_id": decision_id,
        "execution": counters,
        **mtm,
    }
    append_jsonl(SNAPSHOTS_FILE, snapshot)
    risk_metrics = compute_metrics_from_snapshots()
    metrics = {
        "updated_at": snapshot["timestamp"],
        "latest_nav": mtm["total_nav"],
        "cash_hkd": mtm["cash_hkd"],
        "total_unrealized_pnl": mtm["total_unrealized_pnl"],
        "realized_pnl": mtm["realized_pnl"],
        "total_fees": mtm["total_fees"],
        "total_trades": portfolio.data["stats"]["total_trades"],
        "pending_orders": mtm["pending_orders"],
        "last_decision_id": decision_id,
        **risk_metrics,
    }
    atomic_write_text(
        METRICS_FILE,
        json.dumps(metrics, ensure_ascii=False, indent=2),
    )


def print_report() -> None:
    if not METRICS_FILE.exists():
        log("报告", "尚无模拟数据，请先运行 sim_trader.py")
        return
    metrics = json.loads(METRICS_FILE.read_text(encoding="utf-8"))
    print("\n===== 模拟交易绩效 =====")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    if SNAPSHOTS_FILE.exists():
        lines = SNAPSHOTS_FILE.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            first = json.loads(lines[0])
            last = json.loads(lines[-1])
            nav_change = last["total_nav"] - first["total_nav"]
            print(
                f"\n净值：{first['total_nav']:.2f} -> {last['total_nav']:.2f} "
                f"（{nav_change:+.2f}） 快照数={len(lines)}"
            )
            sharpe = metrics.get("sharpe_ratio")
            max_dd = metrics.get("max_drawdown_pct")
            if sharpe is not None or max_dd is not None:
                parts = []
                if sharpe is not None:
                    parts.append(f"Sharpe={sharpe}")
                if max_dd is not None:
                    parts.append(f"最大回撤={max_dd}%")
                print("风险指标：" + "，".join(parts))
    print()
