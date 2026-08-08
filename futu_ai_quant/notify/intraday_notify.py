"""日内做 T 信号通知：Bark（手机）+ 企微群（可选）。"""

from __future__ import annotations

import os
import re

from futu_ai_quant.notify.bark import (
    bark_is_configured,
    bark_notify_warning,
    bark_title_for_signal,
    send_bark,
    send_bark_async,
)
from futu_ai_quant.notify.wecom import (
    _buy_price,
    _sell_price,
    log_wecom_send,
    send_wecom,
    send_wecom_async,
    wecom_is_configured,
)
from futu_ai_quant.strategy.intraday_t import SignalEvent, SignalKind
from futu_ai_quant.utils.logging import log


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() not in ("0", "false", "no")


def intraday_wecom_enabled() -> bool:
    """做 T 是否推企微：默认跟随 WECOM_*；可用 INTRADAY_T_WECOM=0 单独关闭。"""
    raw = os.getenv("INTRADAY_T_WECOM", "").strip().lower()
    if raw in ("0", "false", "no"):
        return False
    if raw in ("1", "true", "yes"):
        return wecom_is_configured()
    return wecom_is_configured()


def intraday_notify_kinds() -> set[SignalKind]:
    kinds = {
        SignalKind.SELL,
        SignalKind.BUY_T,
        SignalKind.BUY_BACK,
        SignalKind.SELL_OFF,
    }
    if bark_notify_warning() or _env_bool("INTRADAY_T_WECOM_WARNING"):
        kinds.add(SignalKind.WARNING)
    return kinds


def _is_buy_side_title(title: str) -> bool:
    upper = title.upper()
    return any(k in upper for k in ("买入", "买回", "BUY"))


def _is_sell_side_title(title: str) -> bool:
    upper = title.upper()
    return any(k in upper for k in ("卖出", "SELL")) and not _is_buy_side_title(title)


def _color_intraday_prices(msg: str, *, buy_side: bool) -> str:
    """把建议成交价 / 目标价着色：买入红（反引号）、卖出绿（info）。"""
    colorize = _buy_price if buy_side else _sell_price

    # @ 145.800 HKD
    msg = re.sub(
        r"@\s*([0-9]+(?:\.[0-9]+)?)\s*(HKD|USD|CNY)?",
        lambda m: f"@ {colorize((m.group(1) + (' ' + m.group(2) if m.group(2) else '')).strip())}",
        msg,
    )
    # 目标买回 <= 143.200 HKD / 目标卖出 >= 136.500 HKD
    msg = re.sub(
        r"(目标买回\s*<=?\s*)([0-9]+(?:\.[0-9]+)?(?:\s*HKD)?)",
        lambda m: f"{m.group(1)}{_buy_price(m.group(2).strip())}",
        msg,
    )
    msg = re.sub(
        r"(目标卖出\s*>=?\s*)([0-9]+(?:\.[0-9]+)?(?:\s*HKD)?)",
        lambda m: f"{m.group(1)}{_sell_price(m.group(2).strip())}",
        msg,
    )
    # 预估净价差 2.700 HKD —— 随本笔方向着色
    msg = re.sub(
        r"(预估净价差\s*)([0-9]+(?:\.[0-9]+)?(?:\s*HKD)?)",
        lambda m: f"{m.group(1)}{colorize(m.group(2).strip())}",
        msg,
    )
    return msg


def format_intraday_wecom_markdown(title: str, header: str, message: str) -> str:
    """组装企微 markdown。

    企微 font 仅 info(绿)/warning(橙)/comment(灰)；买入红色用行内代码 `` ` ``。
    """
    msg = re.sub(r"[🚨✅⚠️]", "", (message or "").strip()).strip()
    header = (header or "").strip()
    buy_side = _is_buy_side_title(title)
    sell_side = _is_sell_side_title(title)

    if buy_side:
        # 标题用反引号 → 红色（与自选「买入」一致）
        title_line = f"## {_buy_price(title)}"
    elif sell_side:
        title_line = f'## <font color="info">{title}</font>'
    elif "预警" in title or "WARNING" in title.upper():
        title_line = f'## <font color="warning">{title}</font>'
    else:
        title_line = f'## <font color="comment">{title}</font>'

    if msg and (buy_side or sell_side):
        msg = _color_intraday_prices(msg, buy_side=buy_side)

    lines = [title_line]
    if header:
        lines.append(f"> {header}")
    if msg:
        lines.append(msg)
    return "\n".join(lines)


def notify_intraday_signal(
    code: str,
    event: SignalEvent,
    header: str,
    *,
    sync: bool = False,
) -> None:
    """推送做 T 信号到已配置的通道。

    ``sync=True`` 用于回放等进程即将退出的场景（避免 daemon 线程被掐断）。
    """
    if event.kind not in intraday_notify_kinds():
        return

    title = bark_title_for_signal(event.kind.value, code)
    body = f"{header}\n{event.message}".strip()

    if bark_is_configured():
        if sync:
            ok, msg = send_bark(title, body)
            log("Bark", f"推送{'成功' if ok else '失败'}: {msg if not ok else title}")
        else:
            send_bark_async(title, body)

    if intraday_wecom_enabled():
        md = format_intraday_wecom_markdown(title, header, event.message)
        if sync:
            ok, msg = send_wecom(title, md, content_is_full_markdown=True)
            log_wecom_send(ok, title, msg)
        else:
            send_wecom_async(title, md, content_is_full_markdown=True)


def notify_channels_label() -> str:
    parts: list[str] = []
    if bark_is_configured():
        parts.append("Bark")
    if intraday_wecom_enabled():
        parts.append("企微")
    return "+".join(parts) if parts else "关闭"
