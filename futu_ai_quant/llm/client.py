"""OpenAI 兼容 LLM 客户端工厂。"""

from __future__ import annotations

from openai import OpenAI

from futu_ai_quant.llm.settings import (
    anthropic_api_key,
    deepseek_api_key,
    deepseek_base_url,
    llm_api_key,
    llm_provider,
    openai_api_key,
    openai_base_url,
    resolve_llm_model,
)


class LLMConfigError(RuntimeError):
    pass


def create_llm_client(provider: str | None = None) -> OpenAI:
    """
    按 ``LLM_PROVIDER`` 创建 OpenAI 兼容客户端。

    支持 deepseek / openai / anthropic（经 OpenAI 兼容代理）/ custom。
    """
    name = (provider or llm_provider()).lower()

    if name == "deepseek":
        api_key = deepseek_api_key() or llm_api_key()
        if not api_key:
            raise LLMConfigError("请配置 DEEPSEEK_API_KEY 或 LLM_API_KEY")
        return OpenAI(api_key=api_key, base_url=deepseek_base_url())

    if name == "openai":
        api_key = openai_api_key() or llm_api_key()
        if not api_key:
            raise LLMConfigError("请配置 OPENAI_API_KEY 或 LLM_API_KEY")
        return OpenAI(api_key=api_key, base_url=openai_base_url())

    if name == "anthropic":
        api_key = anthropic_api_key() or llm_api_key()
        if not api_key:
            raise LLMConfigError("请配置 ANTHROPIC_API_KEY 或 LLM_API_KEY")
        base_url = openai_base_url()
        return OpenAI(api_key=api_key, base_url=base_url)

    if name == "custom":
        api_key = llm_api_key() or openai_api_key() or deepseek_api_key()
        if not api_key:
            raise LLMConfigError("custom 提供商需配置 LLM_API_KEY")
        return OpenAI(api_key=api_key, base_url=openai_base_url())

    raise LLMConfigError(f"未知 LLM_PROVIDER: {name}")


def get_llm_model(provider: str | None = None) -> str:
    return resolve_llm_model(provider)
