"""Bark 推送单元测试。"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from futu_ai_quant.notify import bark


class TestBark:
    def test_not_configured_when_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BARK_ENABLED", "0")
        monkeypatch.setenv("BARK_DEVICE_KEY", "abc")
        assert bark.bark_is_configured() is False
        ok, msg = bark.send_bark("t", "b")
        assert ok is False
        assert "未启用" in msg

    def test_not_configured_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BARK_ENABLED", "1")
        monkeypatch.delenv("BARK_DEVICE_KEY", raising=False)
        assert bark.bark_is_configured() is False

    @patch("futu_ai_quant.notify.bark.urllib.request.urlopen")
    def test_send_success(self, mock_urlopen: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("BARK_ENABLED", "1")
        monkeypatch.setenv("BARK_DEVICE_KEY", "testkey")
        monkeypatch.setenv("BARK_SERVER", "https://api.day.app")
        monkeypatch.setenv("BARK_LEVEL", "timeSensitive")

        resp = MagicMock()
        resp.read.return_value = json.dumps({"code": 200, "message": "success"}).encode()
        resp.__enter__.return_value = resp
        mock_urlopen.return_value = resp

        ok, raw = bark.send_bark("做T卖出 HK.09988", "价格=100")
        assert ok is True
        assert "success" in raw

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://api.day.app/push"
        payload = json.loads(req.data.decode())
        assert payload["device_key"] == "testkey"
        assert payload["title"] == "做T卖出 HK.09988"
        assert payload["level"] == "timeSensitive"

    def test_title_for_signal(self) -> None:
        assert bark.bark_title_for_signal("SELL", "HK.09988") == "做T卖出 HK.09988"
        assert bark.bark_title_for_signal("BUY_BACK", "HK.09988") == "做T买回 HK.09988"
        assert bark.bark_title_for_signal("BUY_T", "HK.09988") == "做T买入 HK.09988"
        assert bark.bark_title_for_signal("SELL_OFF", "HK.09988") == "做T卖出平仓 HK.09988"
