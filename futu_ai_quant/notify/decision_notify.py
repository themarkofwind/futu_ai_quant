"""按 ``NOTIFY_CHANNEL`` 路由决策摘要通知。"""

from __future__ import annotations

import os
from typing import Any

from futu_ai_quant.config.watchlist import format_watchlist_notify_when
from futu_ai_quant.notify.pushplus import (
    format_pushplus_decision_content,
    pushplus_is_configured,
    send_pushplus,
)
from futu_ai_quant.notify.wecom import (
    format_wecom_decision_markdown,
    log_wecom_send,
    send_wecom,
    wecom_is_configured,
)

_WECOM_ALIASES = {"wecom", "wework", "wechat_work", "wechatwork", "企微"}


def notify_channel() -> str:
    raw = os.getenv("NOTIFY_CHANNEL", "pushplus").strip().lower()
    return "wecom" if raw in _WECOM_ALIASES else "pushplus"


def notify_channel_label() -> str:
    return "企微群机器人" if notify_channel() == "wecom" else "PushPlus"


def notify_is_configured() -> bool:
    return wecom_is_configured() if notify_channel() == "wecom" else pushplus_is_configured()


def _resolve_when_text(result: dict[str, Any], *, watchlist: bool) -> str:
    if not watchlist:
        return str(result.get("analyzed_at") or "").strip()
    return format_watchlist_notify_when(
        slot_key=result.get("slot_key"),
        slot_label_text=result.get("slot_label"),
        analyzed_at=result.get("analyzed_at"),
    )


def _watchlist_title(result: dict[str, Any], source: str) -> str:
    slot = str(result.get("slot_label") or "").strip()
    return f"自选分析 · {slot} · {source}" if slot else f"自选分析 · {source}"


def send_decision_message(
    result: dict[str, Any],
    *,
    watchlist: bool = False,
) -> tuple[bool, str]:
    if not notify_is_configured():
        return False, "skipped"
    decision = result.get("decision")
    if not isinstance(decision, dict):
        return False, "无 decision 字段"
    source = str(result.get("decision_source") or "").strip() or "unknown"
    when = _resolve_when_text(result, watchlist=watchlist)
    title = _watchlist_title(result, source) if watchlist else f"持仓分析 · {source}"
    if notify_channel() == "wecom":
        content = format_wecom_decision_markdown(
            decision, title=title, when_text=when or None, decision_source=source
        )
        ok, message = send_wecom(title, content, content_is_full_markdown=True)
        log_wecom_send(ok, title, message)
        return ok, message
    content = format_pushplus_decision_content(decision, decision_source=source)
    return send_pushplus(title, content, topic=None if watchlist else "")


def notify_analyze_decision(result: dict[str, Any]) -> tuple[bool, str]:
    """通知持仓决策；PushPlus 情形强制一对一。"""
    return send_decision_message(result, watchlist=False)


def notify_watchlist_decision(result: dict[str, Any]) -> tuple[bool, str]:
    """通知自选决策；PushPlus 情形沿用默认群组。"""
    return send_decision_message(result, watchlist=True)
