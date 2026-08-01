"""PushPlus 微信推送（https://www.pushplus.plus/）。

- ``futu-analyze``：持仓摘要，强制一对一（``topic=""``）
- ``futu-watchlist``：自选摘要，默认走 ``PUSHPLUS_TOPIC`` 群组
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from futu_ai_quant.decision.display import format_decision_summary
from futu_ai_quant.utils.logging import log

_DEFAULT_API = "https://www.pushplus.plus/send"
_MAX_CONTENT_CHARS = 3500


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() not in ("0", "false", "no")


def pushplus_is_configured() -> bool:
    return _env_bool("PUSHPLUS_ENABLED") and bool(os.getenv("PUSHPLUS_TOKEN", "").strip())


def send_pushplus(
    title: str,
    content: str,
    *,
    template: str | None = None,
    channel: str | None = None,
    topic: str | None = None,
    timeout_sec: float | None = None,
) -> tuple[bool, str]:
    """同步发送 PushPlus；返回 (success, response_or_error)。

    接口成功仅表示服务端受理请求（``code == 200``），最终送达需在 PushPlus 控制台或微信侧确认。
    """
    if not pushplus_is_configured():
        return False, "PushPlus 未启用或未配置 PUSHPLUS_TOKEN"

    token = os.getenv("PUSHPLUS_TOKEN", "").strip()
    api = os.getenv("PUSHPLUS_API", _DEFAULT_API).strip() or _DEFAULT_API
    default_template = os.getenv("PUSHPLUS_TEMPLATE", "txt").strip() or "txt"
    default_channel = os.getenv("PUSHPLUS_CHANNEL", "wechat").strip() or "wechat"
    default_topic = os.getenv("PUSHPLUS_TOPIC", "").strip()
    timeout = timeout_sec if timeout_sec is not None else float(os.getenv("PUSHPLUS_TIMEOUT_SEC", "10"))

    payload: dict[str, Any] = {
        "token": token,
        "title": title.replace("\n", " ").strip() or "持仓分析",
        "content": content,
        "template": template or default_template,
        "channel": channel or default_channel,
    }
    topic_val = topic if topic is not None else default_topic
    if topic_val:
        payload["topic"] = topic_val

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return False, f"HTTP {exc.code}: {detail}"
    except Exception as exc:
        return False, str(exc)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return False, f"非 JSON 响应: {raw[:200]}"

    code = parsed.get("code")
    if code == 200:
        return True, raw
    return False, raw


def format_pushplus_decision_content(
    decision: dict[str, Any],
    *,
    decision_source: str | None = None,
    max_chars: int = _MAX_CONTENT_CHARS,
) -> str:
    """将决策压成适合微信推送的文本（过长截断）。"""
    header_parts: list[str] = []
    if decision_source:
        header_parts.append(f"来源：{decision_source}")
    recs = decision.get("recommendations") or []
    if isinstance(recs, list):
        header_parts.append(f"标的数：{len(recs)}")
    header = " | ".join(header_parts)

    body = format_decision_summary(decision)
    text = f"{header}\n\n{body}" if header else body
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def notify_analyze_decision(result: dict[str, Any]) -> tuple[bool, str]:
    """持仓分析成功后推送摘要（强制一对一，不使用群组 topic）。"""
    if not pushplus_is_configured():
        return False, "skipped"

    decision = result.get("decision")
    if not isinstance(decision, dict):
        return False, "无 decision 字段"

    source = str(result.get("decision_source") or "").strip() or "unknown"
    title = f"持仓分析 · {source}"
    content = format_pushplus_decision_content(decision, decision_source=source)
    # topic="" 覆盖 PUSHPLUS_TOPIC，避免个人持仓进群
    ok, msg = send_pushplus(title, content, topic="")
    if ok:
        log("PushPlus", f"推送成功(一对一): {title}")
    else:
        log("PushPlus", f"推送失败: {msg}")
    return ok, msg


def notify_watchlist_decision(result: dict[str, Any]) -> tuple[bool, str]:
    """自选分析成功后推送摘要；默认走 PUSHPLUS_TOPIC 一对多群组。"""
    if not pushplus_is_configured():
        return False, "skipped"

    decision = result.get("decision")
    if not isinstance(decision, dict):
        return False, "无 decision 字段"

    source = str(result.get("decision_source") or "").strip() or "unknown"
    slot = str(result.get("slot_label") or "").strip()
    if slot:
        title = f"自选分析 · {slot} · {source}"
    else:
        title = f"自选分析 · {source}"
    content = format_pushplus_decision_content(decision, decision_source=source)
    ok, msg = send_pushplus(title, content)
    if ok:
        log("PushPlus", f"推送成功: {title}")
    else:
        log("PushPlus", f"推送失败: {msg}")
    return ok, msg
