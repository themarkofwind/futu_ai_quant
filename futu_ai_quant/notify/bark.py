"""Bark iOS 推送（https://github.com/Finb/Bark）。"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any

from futu_ai_quant.utils.logging import log


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() not in ("0", "false", "no")


def bark_is_configured() -> bool:
    return _env_bool("BARK_ENABLED") and bool(os.getenv("BARK_DEVICE_KEY", "").strip())


def bark_notify_warning() -> bool:
    return _env_bool("BARK_NOTIFY_WARNING")


def send_bark(
    title: str,
    body: str,
    *,
    group: str | None = None,
    level: str | None = None,
    timeout_sec: float | None = None,
) -> tuple[bool, str]:
    """同步发送 Bark 推送；返回 (success, response_or_error)。"""
    if not bark_is_configured():
        return False, "Bark 未启用或未配置 BARK_DEVICE_KEY"

    device_key = os.getenv("BARK_DEVICE_KEY", "").strip()
    server = os.getenv("BARK_SERVER", "https://api.day.app").strip().rstrip("/")
    default_group = os.getenv("BARK_GROUP", "日内做T").strip()
    default_level = os.getenv("BARK_LEVEL", "timeSensitive").strip()
    timeout = timeout_sec if timeout_sec is not None else float(os.getenv("BARK_TIMEOUT_SEC", "10"))

    payload: dict[str, Any] = {
        "device_key": device_key,
        "title": title,
        "body": body,
        "group": group or default_group,
        "level": level or default_level,
    }
    url = f"{server}/push"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return True, raw
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return False, f"HTTP {exc.code}: {detail}"
    except Exception as exc:
        return False, str(exc)


def send_bark_async(
    title: str,
    body: str,
    *,
    group: str | None = None,
    level: str | None = None,
) -> None:
    """后台线程发送，避免阻塞行情回调。"""

    def _worker() -> None:
        ok, msg = send_bark(title, body, group=group, level=level)
        if ok:
            log("Bark", f"推送成功: {title}")
        else:
            log("Bark", f"推送失败: {msg}")

    threading.Thread(target=_worker, daemon=True, name="bark-push").start()


def bark_title_for_signal(kind: str, code: str) -> str:
    if kind == "SELL":
        return f"做T卖出 {code}"
    if kind == "BUY_T":
        return f"做T买入 {code}"
    if kind == "BUY_BACK":
        return f"做T买回 {code}"
    if kind == "SELL_OFF":
        return f"做T卖出平仓 {code}"
    if kind == "WARNING":
        return f"做T预警 {code}"
    return f"做T通知 {code}"
