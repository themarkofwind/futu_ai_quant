"""企业微信群机器人 Webhook 推送。

文档：https://developer.work.weixin.qq.com/document/path/91770

配置（二选一）：
- ``WECOM_WEBHOOK_URL``：完整 webhook 地址
- ``WECOM_WEBHOOK_KEY``：仅 key，自动拼官方 URL
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from futu_ai_quant.utils.logging import log

_DEFAULT_WEBHOOK_PREFIX = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="
# 企微 text 约 2048 字节；markdown 约 4096 字节。按字符保守截断。
_MAX_TEXT_CHARS = 1800
_MAX_MARKDOWN_CHARS = 3800

# 企微 <font> 仅支持 info(绿) / warning(橙) / comment(灰)。
# 真正偏红的是行内代码样式（与消息里 `rules` 同色），故买入用反引号。
_ACTION_COLOR = {
    "BUY": "buy",
    "SELL": "sell",
    "HOLD": "hold",
    "WATCH": "hold",
    "买入": "buy",
    "买入/平仓": "buy",
    "卖出": "sell",
    "卖出/做空": "sell",
    "观望": "hold",
    "持有": "hold",
}

_COLOR_SELL = "info"  # 绿
_COLOR_TIME = "warning"  # 橙，提醒时段 / 代码


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() not in ("0", "false", "no")


def resolve_wecom_webhook_url() -> str:
    """解析 webhook URL；优先完整 URL，其次用 KEY 拼接。"""
    url = os.getenv("WECOM_WEBHOOK_URL", "").strip()
    if url:
        return url
    key = os.getenv("WECOM_WEBHOOK_KEY", "").strip()
    if key:
        if key.startswith("http://") or key.startswith("https://"):
            return key
        return f"{_DEFAULT_WEBHOOK_PREFIX}{key}"
    return ""


def wecom_is_configured() -> bool:
    return _env_bool("WECOM_ENABLED") and bool(resolve_wecom_webhook_url())


def _escape_md(text: str) -> str:
    """弱化易破坏企微 markdown 的字符（保留可读性）。"""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _action_font(action_label: str, action: str) -> str:
    label = (action_label or action or "HOLD").strip()
    kind = _ACTION_COLOR.get(label) or _ACTION_COLOR.get((action or "").upper())
    if kind is None:
        if any(k in label for k in ("买", "平仓", "加仓", "BUY")):
            kind = "buy"
        elif any(k in label for k in ("卖", "空", "减仓", "SELL")):
            kind = "sell"
        else:
            kind = "hold"
    if kind == "buy":
        # 行内代码 ≈ 消息中 `rules` 的红色
        return f"`{label}`"
    if kind == "sell":
        return _font(_COLOR_SELL, label)
    return _font("comment", label)


def _buy_price(text: str) -> str:
    """买入价位：行内代码红色（对齐 `rules`）。"""
    return f"`{text}`"


def _sell_price(text: str) -> str:
    """卖出价位：绿色 font。"""
    return _font(_COLOR_SELL, text)


def _font(color: str, text: str) -> str:
    return f'<font color="{color}">{_escape_md(text)}</font>'


def _shorten(text: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _fmt_price(value: Any) -> str | None:
    try:
        if value is None:
            return None
        return f"{float(value):.3f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return None


def _fmt_price_range(low: Any, high: Any) -> str | None:
    a, b = _fmt_price(low), _fmt_price(high)
    if a is None or b is None:
        return None
    return f"{a}-{b}"


def _strip_tip_parens(text: str) -> str:
    return re.sub(r"（[^）]*）|\([^)]*\)", "", text or "")


def _build_price_line(rec: dict[str, Any], tech: str) -> str | None:
    """现价 + 买入(红)/卖出(绿)参考（去掉括号说明）。"""
    parts: list[str] = []
    spot = None
    m = re.search(r"现价=([0-9.]+)", tech or "")
    if m:
        spot = m.group(1)
    if spot:
        parts.append(f"现价 {_escape_md(spot)}")

    plan = rec.get("stock_trade_plan") or {}
    direction = str(plan.get("direction") or "none")
    if direction not in (None, "", "none"):
        band = _fmt_price_range(plan.get("trigger_price_low"), plan.get("trigger_price_high"))
        if band:
            if direction == "buy":
                parts.append(f"买入参考 {_buy_price(band)}")
            else:
                parts.append(f"卖出参考 {_sell_price(band)}")
        else:
            trigger = _strip_tip_parens(str(rec.get("suggested_trigger") or "")).strip(" ；;")
            trigger = re.sub(r"\s+", " ", trigger)
            if trigger:
                colored = re.sub(
                    r"([0-9]+(?:\.[0-9]+)?-[0-9]+(?:\.[0-9]+)?)",
                    lambda m: (
                        _buy_price(m.group(1))
                        if "买" in trigger
                        else _sell_price(m.group(1))
                    ),
                    trigger,
                )
                parts.append(colored)
    else:
        buy_band = sell_band = None
        for w in plan.get("watch_triggers") or []:
            if not isinstance(w, dict):
                continue
            band = _fmt_price_range(w.get("price_low"), w.get("price_high"))
            if not band:
                continue
            if w.get("side") == "buy":
                buy_band = band
            elif w.get("side") == "sell":
                sell_band = band
        if buy_band:
            parts.append(f"买入参考 {_buy_price(buy_band)}")
        if sell_band:
            parts.append(f"卖出参考 {_sell_price(sell_band)}")
        if not buy_band and not sell_band:
            # technical_summary / tip / suggested_trigger 均可带「买入/卖出参考」
            blob = "；".join(
                str(rec.get(k) or "")
                for k in ("tip", "suggested_trigger", "technical_summary")
            )
            blob = _strip_tip_parens(blob)
            m_buy = re.search(r"买入参考[=：:]?\s*([0-9.]+(?:\s*[-~]\s*[0-9.]+)?)", blob)
            m_sell = re.search(r"卖出参考[=：:]?\s*([0-9.]+(?:\s*[-~]\s*[0-9.]+)?)", blob)
            if m_buy:
                buy_band = m_buy.group(1).replace(" ", "").replace("~", "-")
            if m_sell:
                sell_band = m_sell.group(1).replace(" ", "").replace("~", "-")
            if buy_band:
                parts.append(f"买入参考 {_buy_price(buy_band)}")
            if sell_band:
                parts.append(f"卖出参考 {_sell_price(sell_band)}")
            elif not buy_band:
                tip = _strip_tip_parens(str(rec.get("tip") or ""))
                tip = re.sub(r"^(观望|持有)[；;]\s*", "", tip)
                tip = re.sub(r"\s+", " ", tip).strip(" ；;")
                if tip:
                    parts.append(tip)

    return "；".join(parts) if parts else None


def _parse_k_lines(tech: str) -> list[str]:
    """从 technical_summary 拆出完整的日K/周K/盘中行（不截断）。"""
    lines: list[str] = []
    for label in ("日K", "周K", "盘中"):
        m = re.search(rf"{label}\s+([^；;]+)", tech or "")
        if m:
            body = re.sub(r"\s+", " ", m.group(1)).strip()
            if body:
                lines.append(f"{label} {body}")
    return lines


def _compact_portfolio_summary(summary: str, *, max_len: int = 100) -> str:
    text = re.sub(r"^【[^】]*】\s*", "", (summary or "").strip())
    return _shorten(text, max_len)


def format_wecom_decision_markdown(
    decision: dict[str, Any],
    *,
    title: str,
    decision_source: str | None = None,
    when_text: str | None = None,
    max_chars: int = _MAX_MARKDOWN_CHARS,
) -> str:
    """将决策格式化为企微群可读 markdown（列表 + 动作/价位/分周期指标）。"""
    is_watchlist = decision.get("analysis_mode") == "watchlist"
    lines: list[str] = [f"## {_escape_md(title)}"]

    if when_text:
        lines.append(_font(_COLOR_TIME, when_text))

    source_bit = f"`{_escape_md(decision_source)}`" if decision_source else ""
    recs = decision.get("recommendations") or []
    n = len(recs) if isinstance(recs, list) else 0
    if source_bit or n:
        bits = [b for b in (source_bit, f"{n} 只" if n else "") if b]
        lines.append(" · ".join(bits))

    summary = str(decision.get("portfolio_risk_summary") or "").strip()
    if summary:
        lines.append(f"> {_escape_md(_compact_portfolio_summary(summary))}")

    if not isinstance(recs, list) or not recs:
        lines.append("")
        lines.append("暂无标的建议")
    else:
        lines.append("")  # 与组合观点隔开
        for i, rec in enumerate(recs):
            if not isinstance(rec, dict):
                continue
            code = str(rec.get("code") or "")
            name = str(rec.get("display_name") or rec.get("name") or code)
            action = str(rec.get("action") or "HOLD")
            action_label = str(rec.get("action_label") or action)
            confidence = rec.get("confidence")
            conf = (
                f"  ·  置信度 {confidence:.0%}"
                if isinstance(confidence, (int, float))
                else ""
            )
            tech = str(rec.get("technical_summary") or "").strip()

            if i > 0:
                # 企微会折叠纯 \\n，且不解析 HTML 实体；用真实 NBSP 行拉开间距
                lines.append("")
                lines.append("\u00a0")
                lines.append("")

            lines.append(
                f"**{i + 1}. {_escape_md(name)}**  {_font(_COLOR_TIME, code)}"
            )
            lines.append(f"动作 {_action_font(action_label, action)}{conf}")

            price_line = _build_price_line(rec, tech)
            if price_line:
                lines.append(price_line)

            for k_line in _parse_k_lines(tech):
                lines.append(_font("comment", k_line))

            if not is_watchlist:
                stock_plan = rec.get("stock_trade_plan") or {}
                if stock_plan.get("direction") not in (None, "none") and not price_line:
                    lines.append(
                        f"{_escape_md(str(stock_plan.get('direction')))} "
                        f"{stock_plan.get('suggested_lots', 0)}手"
                    )
                opt_plan = rec.get("option_trade_plan") or {}
                if opt_plan.get("action") not in (None, "none"):
                    lines.append(
                        f"期权 {_escape_md(str(opt_plan.get('action')))} "
                        f"{_font(_COLOR_TIME, str(opt_plan.get('contract_code') or ''))}"
                    )

    text = "\n".join(lines).rstrip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def send_wecom(
    title: str,
    content: str,
    *,
    msg_type: str | None = None,
    timeout_sec: float | None = None,
    content_is_full_markdown: bool = False,
) -> tuple[bool, str]:
    """同步发送企微群消息；返回 (success, response_or_error)。

    ``content_is_full_markdown=True`` 时，``content`` 已含标题，不再包一层。
    """
    if not wecom_is_configured():
        return False, "企微未启用或未配置 WECOM_WEBHOOK_URL / WECOM_WEBHOOK_KEY"

    webhook = resolve_wecom_webhook_url()
    default_type = os.getenv("WECOM_MSG_TYPE", "markdown").strip().lower() or "markdown"
    use_type = (msg_type or default_type).lower()
    if use_type not in ("text", "markdown"):
        use_type = "markdown"
    timeout = timeout_sec if timeout_sec is not None else float(os.getenv("WECOM_TIMEOUT_SEC", "10"))

    title_clean = title.replace("\n", " ").strip() or "通知"
    body = (content or "").strip()
    max_chars = _MAX_MARKDOWN_CHARS if use_type == "markdown" else _MAX_TEXT_CHARS
    if use_type == "markdown":
        if content_is_full_markdown and body:
            text = body
        else:
            text = f"**{title_clean}**\n{body}" if body else f"**{title_clean}**"
    else:
        text = f"【{title_clean}】\n{body}" if body else f"【{title_clean}】"
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"

    if use_type == "markdown":
        payload: dict[str, Any] = {"msgtype": "markdown", "markdown": {"content": text}}
    else:
        payload = {"msgtype": "text", "text": {"content": text}}

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook,
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

    errcode = parsed.get("errcode")
    if errcode == 0:
        return True, raw
    return False, raw


def log_wecom_send(ok: bool, title: str, msg: str) -> None:
    if ok:
        log("企微", f"推送成功: {title}")
    else:
        log("企微", f"推送失败: {msg}")
