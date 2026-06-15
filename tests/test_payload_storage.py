from __future__ import annotations

import json
from pathlib import Path

from futu_ai_quant.decision.storage import save_analysis_artifacts, save_portfolio_payload_record


class TestPayloadStorage:
    def test_save_portfolio_payload_record(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("futu_ai_quant.decision.storage.PAYLOADS_DIR", tmp_path)
        payload = {
            "as_of": "2026-06-15 12:00:00",
            "stocks": [{"code": "HK.00700"}],
            "options": [],
            "summary": {"stock_count": 1},
        }
        path = save_portfolio_payload_record(
            payload,
            required_codes=["HK.00700"],
            ts="20260615_120000",
            decision_source="rules",
        )
        assert path.name == "payload_20260615_120000.json"
        record = json.loads(path.read_text(encoding="utf-8"))
        assert record["portfolio_payload"]["stocks"][0]["code"] == "HK.00700"
        assert (tmp_path / "latest_payload.json").exists()

    def test_save_analysis_artifacts_links_paths(self, tmp_path: Path, monkeypatch) -> None:
        payloads_dir = tmp_path / "payloads"
        decisions_dir = tmp_path / "decisions"
        monkeypatch.setattr("futu_ai_quant.decision.storage.PAYLOADS_DIR", payloads_dir)
        monkeypatch.setattr("futu_ai_quant.decision.storage.DECISIONS_DIR", decisions_dir)
        payload = {"summary": {"stock_count": 1}, "stocks": [], "options": []}
        decision = {"portfolio_risk_summary": "ok", "recommendations": []}
        payload_path, decision_path = save_analysis_artifacts(
            payload,
            decision,
            required_codes=["HK.00700"],
            decision_source="deepseek",
        )
        assert payload_path.stem == decision_path.stem.replace("decision_", "payload_")
        decision_record = json.loads(decision_path.read_text(encoding="utf-8"))
        assert decision_record["payload_path"] == str(payload_path)
        assert decision_record["analysis_id"] == payload_path.stem.replace("payload_", "")
