"""
LLM 决策生成（多提供商，OpenAI 兼容 API）。

外部 API：``client.chat.completions.create``，JSON mode，最多 2 次补全缺失标的。
自选观察另走 ``call_watchlist_llm_decision``（更短 prompt + JSON 截断重试）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from openai import OpenAI

from futu_ai_quant.analysis.portfolio import collect_required_codes
from futu_ai_quant.analysis.slim import slim_portfolio_for_ai, slim_watchlist_for_ai
from futu_ai_quant.config.prompts import SYSTEM_PROMPT, WATCHLIST_SYSTEM_PROMPT
from futu_ai_quant.decision.validation import find_missing_recommendation_codes
from futu_ai_quant.llm.settings import (
    llm_max_tokens,
    llm_provider,
    llm_temperature,
    resolve_llm_model,
)
from futu_ai_quant.utils.logging import log


def _strip_code_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_llm_json_content(content: str) -> dict[str, Any]:
    """解析模型 JSON；失败时抛出带上下文的 ValueError。"""
    text = _strip_code_fence(content)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"LLM JSON 解析失败: {exc.msg} (line {exc.lineno} col {exc.colno}, "
            f"pos {exc.pos}, content_len={len(text)})"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError(f"LLM JSON 根节点须为对象，实际为 {type(data).__name__}")
    return data


def _completion_meta(response: Any) -> tuple[str | None, int | None, int | None]:
    choice = response.choices[0]
    finish = getattr(choice, "finish_reason", None)
    usage = getattr(response, "usage", None)
    completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
    total_tokens = getattr(usage, "total_tokens", None) if usage else None
    return finish, completion_tokens, total_tokens


def call_llm_decision(client: OpenAI, portfolio_payload: dict[str, Any]) -> dict[str, Any]:
    """
    将 ``build_portfolio_payload`` 结果发给 LLM，返回 JSON 决策。

    使用 ``config.prompts.SYSTEM_PROMPT`` 作为 system 消息；
    user 消息包含精简 portfolio JSON、虚拟分析师信号与必须覆盖的标的列表。
    股票名称不发给模型，展示层由本地名称缓存负责。
    """
    required_codes = collect_required_codes(portfolio_payload)
    required_count = len(required_codes)
    code_list_text = "、".join(required_codes)
    slim_payload = slim_portfolio_for_ai(portfolio_payload)
    model = resolve_llm_model()

    analyst_hint = ""
    virtual = portfolio_payload.get("virtual_analysts") or {}
    if virtual.get("per_stock"):
        lines = [
            f"{item['code']}: {item['consensus']} (score={item.get('consensus_score')})"
            for item in virtual["per_stock"]
        ]
        analyst_hint = "虚拟分析师共识摘要：\n" + "\n".join(lines) + "\n"

    user_prompt = (
        f"请分析以下港股账户持仓数据，并输出符合 schema 的 JSON 交易建议。\n"
        f"本次共有 {required_count} 个持仓标的，recommendations 必须逐一生成 {required_count} 条建议，"
        f"与 required_positions 一一对应，不得遗漏。\n"
        f"必须覆盖的全部代码：{code_list_text}\n"
        f"{analyst_hint}"
        "策略框架：周K定方向、日K找时机；综合 RSI/布林带/MACD/成交量/ATR 与 technical_ensemble 研判。\n"
        "须参考 macro_risk：宏观风险已预计算进 stock_trade_plan。\n"
        "各正股 analyst_signals 为规则化预分析信号，请与 stock_trade_plan 一并参考，但数量须以预计算 plan 为准。\n"
        "价格字段：pnl.market_price 是未复权现价；daily/weekly.technical_close 是复权技术价，禁止混用。\n"
        "每个正股已预计算 stock_trade_plan（整手股数 lot_size、具体手数/股数）与 option_trade_plan，"
        "输出时必须原样填入 recommendations 对应字段；suggested_qty 必须是 lot_size 整数倍。\n"
        "正股 existing_option_positions 是已有期权，option_trade_plan（plan_source=suggested）是建议新开，二者不可混淆。\n"
        "正股 trade_history 含当年 ytd_summary 与 recent_swing_window（最近若干笔成交，默认 5 笔），"
        "波段建议须避免与近期已执行买卖冲突。\n"
        "务必严格区分 position_direction（如「卖出Call」「买入Put」），"
        "卖出期权与买入期权的 Theta/到期逻辑完全相反。\n"
        f"{json.dumps(slim_payload, ensure_ascii=False, separators=(',', ':'))}"
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    last_missing: list[str] = required_codes
    for attempt in range(1, 3):
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=messages,
            temperature=llm_temperature(),
            max_tokens=llm_max_tokens(),
        )

        content = response.choices[0].message.content
        if not content:
            raise ValueError(f"{llm_provider()} LLM 返回空内容")

        finish, completion_tokens, total_tokens = _completion_meta(response)
        log(
            "模型",
            f"持仓LLM第{attempt}次: finish={finish} "
            f"content_len={len(content)} completion_tokens={completion_tokens} "
            f"total_tokens={total_tokens}",
        )
        if finish == "length":
            log("模型", "输出可能被 max_tokens 截断，若 JSON 解析失败将重试")

        try:
            decision = parse_llm_json_content(content)
        except ValueError:
            if attempt >= 2:
                raise
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "上一次输出不是完整合法 JSON（可能被截断）。"
                        "请重新输出完整 JSON 对象，reasoning 请缩短，确保所有字符串闭合。"
                    ),
                }
            )
            continue

        last_missing = find_missing_recommendation_codes(decision, required_codes)
        if not last_missing:
            return decision

        log(
            "模型",
            f"第 {attempt} 次返回缺少 {len(last_missing)} 个标的建议: {last_missing}",
        )
        messages.append({"role": "assistant", "content": content})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"上一次 recommendations 不完整，缺少以下 {len(last_missing)} 个标的，"
                    f"请补全并重新输出完整 JSON（仍需包含全部 {required_count} 个标的建议）：\n"
                    + "\n".join(f"- {code}" for code in last_missing)
                ),
            }
        )

    raise ValueError(f"模型未返回全部持仓建议，仍缺少: {last_missing}")


def call_watchlist_llm_decision(
    client: OpenAI,
    portfolio_payload: dict[str, Any],
    *,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """自选观察 LLM 决策：短 prompt + 精简 payload + JSON 截断重试。"""
    required_codes = collect_required_codes(portfolio_payload)
    if not required_codes:
        required_codes = [
            str(s.get("code"))
            for s in (portfolio_payload.get("stocks") or [])
            if isinstance(s, dict) and s.get("code")
        ]
    required_count = len(required_codes)
    slim_payload = slim_watchlist_for_ai(portfolio_payload)
    model = resolve_llm_model()
    # 自选输出应更短；默认仍可读 LLM_MAX_TOKENS，但至少给到 4096
    max_tokens = max(4096, min(llm_max_tokens(), 8192))

    user_prompt = (
        f"自选观察共 {required_count} 只，请输出紧凑 JSON。"
        f"必须覆盖：{'、'.join(required_codes)}。\n"
        "每条 reasoning≤80字；不要写持仓/仓位/降本；option 一律 none。\n"
        f"{json.dumps(slim_payload, ensure_ascii=False, separators=(',', ':'))}"
    )
    messages: list[dict[str, str]] = [
        {"role": "system", "content": WATCHLIST_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        temperature = 0.1 if attempt > 1 else min(llm_temperature(), 0.2)
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content
        if not content:
            last_error = ValueError(f"{llm_provider()} LLM 返回空内容")
            log("模型", f"自选LLM第{attempt}次空内容")
            continue

        finish, completion_tokens, total_tokens = _completion_meta(response)
        log(
            "模型",
            f"自选LLM第{attempt}次: finish={finish} content_len={len(content)} "
            f"completion_tokens={completion_tokens} total_tokens={total_tokens} "
            f"temp={temperature}",
        )

        try:
            decision = parse_llm_json_content(content)
        except ValueError as exc:
            last_error = exc
            log("模型", f"自选LLM JSON失败: {exc}")
            if attempt >= max_attempts:
                break
            messages.append({"role": "assistant", "content": content[:2000]})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "输出被截断或不是合法 JSON。"
                        "请重新输出完整紧凑 JSON（单行也可），"
                        "reasoning 每条≤40字，确保所有引号闭合，覆盖全部代码。"
                    ),
                }
            )
            continue

        missing = find_missing_recommendation_codes(decision, required_codes)
        if missing:
            last_error = ValueError(f"缺少标的建议: {missing}")
            log("模型", f"自选LLM缺标的: {missing}")
            if attempt >= max_attempts:
                break
            messages.append({"role": "assistant", "content": content})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"缺少 {len(missing)} 个标的，请输出覆盖全部 "
                        f"{required_count} 只的完整 JSON：\n"
                        + "\n".join(f"- {code}" for code in missing)
                    ),
                }
            )
            continue

        return decision

    raise ValueError(f"自选 LLM 决策失败: {last_error}")


def call_deepseek(client: OpenAI, portfolio_payload: dict[str, Any]) -> dict[str, Any]:
    """向后兼容别名。"""
    return call_llm_decision(client, portfolio_payload)
