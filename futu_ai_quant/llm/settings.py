"""LLM 多提供商配置（运行时读取环境变量，兼容 load_dotenv 晚于 import 的场景）。"""

from __future__ import annotations

import os

_DEFAULT_MODELS = {
    "deepseek": "deepseek-v4-flash",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-20241022",
    "custom": "gpt-4o-mini",
}


def llm_provider() -> str:
    return os.getenv("LLM_PROVIDER", "deepseek").strip().lower()


def llm_model() -> str:
    return os.getenv("LLM_MODEL", "").strip()


def llm_temperature() -> float:
    return float(os.getenv("LLM_TEMPERATURE", "0.2"))


def llm_max_tokens() -> int:
    return int(os.getenv("LLM_MAX_TOKENS", "8192"))


def llm_api_key() -> str:
    return os.getenv("LLM_API_KEY", "").strip()


def deepseek_api_key() -> str:
    return os.getenv("DEEPSEEK_API_KEY", "").strip()


def deepseek_base_url() -> str:
    return os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()


def openai_api_key() -> str:
    return os.getenv("OPENAI_API_KEY", "").strip()


def openai_base_url() -> str:
    return os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()


def anthropic_api_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "").strip()


def resolve_llm_model(provider: str | None = None) -> str:
    explicit = llm_model()
    if explicit:
        return explicit
    key = (provider or llm_provider()).lower()
    return _DEFAULT_MODELS.get(key, "deepseek-v4-flash")
