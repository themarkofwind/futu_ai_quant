"""日内做 T 多通道通知测试。"""

from __future__ import annotations

from unittest.mock import patch

from futu_ai_quant.notify import intraday_notify
from futu_ai_quant.strategy.intraday_t import SignalEvent, SignalKind


def test_format_intraday_wecom_markdown() -> None:
    sell_md = intraday_notify.format_intraday_wecom_markdown(
        "做T卖出 HK.01347",
        "标的=HK.01347 | 价格=145.800",
        "[SELL T] 建议卖出 1000 股 @ 145.800 HKD | 锚定价=145.800 | 目标买回 <= 143.200 HKD",
    )
    assert "做T卖出 HK.01347" in sell_md
    assert 'color="info"' in sell_md  # 卖出标题绿
    assert "<font color=\"info\">145.800 HKD</font>" in sell_md
    assert "`143.200 HKD`" in sell_md  # 目标买回为买入侧红

    buy_md = intraday_notify.format_intraday_wecom_markdown(
        "做T买回 HK.01347",
        "标的=HK.01347 | 价格=143.100",
        "[BUY BACK] 建议买回 1000 股 @ 143.100 HKD | 触发：硬性止盈（<= 143.200） | 预估净价差 2.700 HKD",
    )
    assert "`做T买回 HK.01347`" in buy_md  # 买回标题红（反引号）
    assert "`143.100 HKD`" in buy_md
    assert "`2.700 HKD`" in buy_md
    assert 'color="warning"' not in buy_md  # 不再用橙色标题


def test_notify_routes_bark_and_wecom(monkeypatch) -> None:
    monkeypatch.setenv("BARK_ENABLED", "1")
    monkeypatch.setenv("BARK_DEVICE_KEY", "k")
    monkeypatch.setenv("WECOM_ENABLED", "1")
    monkeypatch.setenv("WECOM_WEBHOOK_URL", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=t")
    monkeypatch.delenv("INTRADAY_T_WECOM", raising=False)

    event = SignalEvent(kind=SignalKind.SELL, message="[SELL T] test", price=140.0)
    with (
        patch.object(intraday_notify, "send_bark_async") as bark,
        patch.object(intraday_notify, "send_wecom_async") as wecom,
    ):
        intraday_notify.notify_intraday_signal("HK.01347", event, "header")
        bark.assert_called_once()
        wecom.assert_called_once()


def test_intraday_wecom_can_disable(monkeypatch) -> None:
    monkeypatch.setenv("WECOM_ENABLED", "1")
    monkeypatch.setenv("WECOM_WEBHOOK_KEY", "abc")
    monkeypatch.setenv("INTRADAY_T_WECOM", "0")
    assert intraday_notify.intraday_wecom_enabled() is False


def test_notify_channels_label(monkeypatch) -> None:
    monkeypatch.setenv("BARK_ENABLED", "1")
    monkeypatch.setenv("BARK_DEVICE_KEY", "k")
    monkeypatch.setenv("WECOM_ENABLED", "1")
    monkeypatch.setenv("WECOM_WEBHOOK_KEY", "abc")
    monkeypatch.delenv("INTRADAY_T_WECOM", raising=False)
    assert "Bark" in intraday_notify.notify_channels_label()
    assert "企微" in intraday_notify.notify_channels_label()
