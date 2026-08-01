"""PushPlus 推送单元测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from futu_ai_quant.notify import pushplus


class TestPushPlus:
    def test_not_configured_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PUSHPLUS_ENABLED", "0")
        monkeypatch.setenv("PUSHPLUS_TOKEN", "abc")
        assert pushplus.pushplus_is_configured() is False
        ok, msg = pushplus.send_pushplus("t", "b")
        assert ok is False
        assert "未启用" in msg

    def test_not_configured_without_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PUSHPLUS_ENABLED", "1")
        monkeypatch.delenv("PUSHPLUS_TOKEN", raising=False)
        assert pushplus.pushplus_is_configured() is False

    @patch("futu_ai_quant.notify.pushplus.urllib.request.urlopen")
    def test_send_success(self, mock_urlopen: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PUSHPLUS_ENABLED", "1")
        monkeypatch.setenv("PUSHPLUS_TOKEN", "testtoken")
        monkeypatch.setenv("PUSHPLUS_TEMPLATE", "txt")
        monkeypatch.setenv("PUSHPLUS_CHANNEL", "wechat")

        resp = MagicMock()
        resp.read.return_value = json.dumps(
            {"code": 200, "msg": "请求成功", "data": "sid"},
            ensure_ascii=False,
        ).encode()
        resp.__enter__.return_value = resp
        mock_urlopen.return_value = resp

        ok, raw = pushplus.send_pushplus("持仓分析 · rules", "摘要正文")
        assert ok is True
        assert json.loads(raw)["code"] == 200

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://www.pushplus.plus/send"
        payload = json.loads(req.data.decode())
        assert payload["token"] == "testtoken"
        assert payload["title"] == "持仓分析 · rules"
        assert payload["content"] == "摘要正文"
        assert payload["template"] == "txt"
        assert payload["channel"] == "wechat"
        assert "topic" not in payload

    @patch("futu_ai_quant.notify.pushplus.urllib.request.urlopen")
    def test_send_includes_topic(self, mock_urlopen: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PUSHPLUS_ENABLED", "1")
        monkeypatch.setenv("PUSHPLUS_TOKEN", "testtoken")
        monkeypatch.setenv("PUSHPLUS_TOPIC", "mygroup")

        resp = MagicMock()
        resp.read.return_value = json.dumps({"code": 200, "msg": "ok"}).encode()
        resp.__enter__.return_value = resp
        mock_urlopen.return_value = resp

        ok, _ = pushplus.send_pushplus("t", "b")
        assert ok is True
        payload = json.loads(mock_urlopen.call_args[0][0].data.decode())
        assert payload["topic"] == "mygroup"

    @patch("futu_ai_quant.notify.pushplus.urllib.request.urlopen")
    def test_send_api_error_code(self, mock_urlopen: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PUSHPLUS_ENABLED", "1")
        monkeypatch.setenv("PUSHPLUS_TOKEN", "testtoken")

        resp = MagicMock()
        resp.read.return_value = json.dumps({"code": 500, "msg": "fail"}).encode()
        resp.__enter__.return_value = resp
        mock_urlopen.return_value = resp

        ok, raw = pushplus.send_pushplus("t", "b")
        assert ok is False
        assert "fail" in raw

    def test_format_and_notify(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PUSHPLUS_ENABLED", "0")
        decision = {
            "portfolio_risk_summary": "整体可控",
            "recommendations": [
                {
                    "code": "HK.09988",
                    "display_name": "阿里巴巴",
                    "action": "HOLD",
                    "confidence": 0.7,
                    "reasoning": "观望",
                    "stock_trade_plan": {"direction": "none"},
                    "option_trade_plan": {"action": "none"},
                }
            ],
        }
        content = pushplus.format_pushplus_decision_content(decision, decision_source="rules")
        assert "来源：rules" in content
        assert "HK.09988" in content
        assert "HOLD" in content

        ok, msg = pushplus.notify_analyze_decision(
            {"decision": decision, "decision_source": "rules"}
        )
        assert ok is False
        assert msg == "skipped"

    def test_content_truncation(self) -> None:
        long_reason = "研判" * 2000
        decision = {
            "recommendations": [
                {
                    "code": "HK.00700",
                    "display_name": "腾讯",
                    "action": "BUY",
                    "reasoning": long_reason,
                    "stock_trade_plan": {"direction": "none"},
                    "option_trade_plan": {"action": "none"},
                }
            ]
        }
        content = pushplus.format_pushplus_decision_content(decision, max_chars=200)
        assert len(content) <= 200
        assert content.endswith("…")
