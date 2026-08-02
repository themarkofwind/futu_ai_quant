"""企微通知与统一通知路由测试。"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from futu_ai_quant.config.watchlist import format_watchlist_notify_when
from futu_ai_quant.notify import decision_notify, wecom


@pytest.fixture
def decision() -> dict:
    return {
        "analysis_mode": "watchlist",
        "portfolio_risk_summary": "【自选·尾盘提醒】整体风险可控",
        "recommendations": [
            {
                "code": "HK.09988",
                "display_name": "阿里巴巴",
                "action": "HOLD",
                "action_label": "观望",
                "confidence": 0.6,
                "technical_summary": (
                    "模式=自选观察；有效信号=HOLD；现价=117；"
                    "日K HOLD RSI=57.58 MACD=neutral 布林=around_mid 量比=0.68；"
                    "周K HOLD RSI=47.46 MACD=golden_cross 布林=around_mid 量比=0.99；"
                    "盘中 HOLD RSI=73.64 MACD=bullish 布林=above_upper 量比=3.02 量能确认"
                ),
                "stock_trade_plan": {
                    "direction": "none",
                    "watch_triggers": [
                        {"side": "buy", "price_low": 115.688, "price_high": 116.563},
                        {"side": "sell", "price_low": 117.437, "price_high": 118.312},
                    ],
                },
            },
            {
                "code": "HK.01347",
                "display_name": "华虹宏力",
                "action": "SELL",
                "action_label": "卖出/做空",
                "confidence": 0.75,
                "technical_summary": "现价=128.3；日K HOLD RSI=61.90；周K HOLD RSI=50.72；盘中 SELL_SWING RSI=71.46",
                "stock_trade_plan": {
                    "direction": "sell",
                    "trigger_price_low": 129.446,
                    "trigger_price_high": 131.737,
                },
            },
        ],
    }


def test_webhook_key_and_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WECOM_ENABLED", "1")
    monkeypatch.setenv("WECOM_WEBHOOK_KEY", "test-key")
    monkeypatch.delenv("WECOM_WEBHOOK_URL", raising=False)
    assert wecom.resolve_wecom_webhook_url().endswith("key=test-key")


@patch("futu_ai_quant.notify.wecom.urllib.request.urlopen")
def test_send_wecom(mock_urlopen: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WECOM_ENABLED", "1")
    monkeypatch.setenv("WECOM_WEBHOOK_URL", "https://example.test/webhook")
    response = MagicMock()
    response.read.return_value = b'{"errcode": 0, "errmsg": "ok"}'
    response.__enter__.return_value = response
    mock_urlopen.return_value = response
    ok, _ = wecom.send_wecom("标题", "## 正文", content_is_full_markdown=True)
    assert ok
    payload = json.loads(mock_urlopen.call_args[0][0].data.decode())
    assert payload == {"msgtype": "markdown", "markdown": {"content": "## 正文"}}


def test_final_markdown_format(decision: dict) -> None:
    content = wecom.format_wecom_decision_markdown(
        decision,
        title="自选分析 · 尾盘提醒 · rules",
        when_text="2026-08-02（周日）15:45 · 尾盘提醒",
        decision_source="rules",
    )
    assert content.startswith("## 自选分析 · 尾盘提醒 · rules")
    assert '<font color="warning">2026-08-02（周日）15:45 · 尾盘提醒</font>' in content
    assert "`rules` · 2 只" in content
    assert "整体风险可控" in content and "【自选" not in content
    assert '**1. 阿里巴巴**  <font color="warning">HK.09988</font>' in content
    assert "动作" in content and "置信度 60%" in content
    assert '<font color="comment">观望</font>' in content
    assert "买入参考 `115.688-116.563`" in content
    assert '卖出参考 <font color="info">117.437-118.312</font>' in content
    assert "日K HOLD RSI=57.58" in content
    assert "\n\u00a0\n" in content
    assert "动作" in content and "卖出/做空" in content
    assert '卖出参考 <font color="info">129.446-131.737</font>' in content
    assert "研判" not in content


def test_format_watchlist_notify_when() -> None:
    text = format_watchlist_notify_when(
        slot_key="preclose",
        slot_label_text="尾盘提醒",
        analyzed_at=datetime(2026, 8, 2, 15, 45, 10),
    )
    assert text == "2026-08-02（周日）15:45 · 尾盘提醒"


def test_notify_routes_to_wecom(monkeypatch: pytest.MonkeyPatch, decision: dict) -> None:
    monkeypatch.setenv("NOTIFY_CHANNEL", "wework")
    monkeypatch.setenv("WECOM_ENABLED", "1")
    monkeypatch.setenv("WECOM_WEBHOOK_URL", "https://example.test/webhook")
    with patch.object(decision_notify, "send_wecom", return_value=(True, "ok")) as send:
        ok, _ = decision_notify.notify_watchlist_decision(
            {
                "decision": decision,
                "decision_source": "rules",
                "slot_key": "preclose",
                "slot_label": "尾盘提醒",
            }
        )
    assert ok and send.called
    assert send.call_args.kwargs.get("content_is_full_markdown") is True
    assert send.call_args.args[0] == "自选分析 · 尾盘提醒 · rules"


def test_notify_routes_to_pushplus(monkeypatch: pytest.MonkeyPatch, decision: dict) -> None:
    monkeypatch.setenv("NOTIFY_CHANNEL", "pushplus")
    monkeypatch.setenv("PUSHPLUS_ENABLED", "1")
    monkeypatch.setenv("PUSHPLUS_TOKEN", "token")
    with patch.object(decision_notify, "send_pushplus", return_value=(True, "ok")) as send:
        ok, _ = decision_notify.notify_analyze_decision(
            {"decision": decision, "decision_source": "rules"}
        )
    assert ok
    assert send.call_args.kwargs["topic"] == ""
    assert send.call_args.args[0] == "持仓分析 · rules"
