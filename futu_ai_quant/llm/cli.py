"""LLM 命令行参数与环境变量覆盖。"""

from __future__ import annotations

import argparse
import os
from typing import Any

from futu_ai_quant.llm.settings import llm_model, llm_provider, resolve_llm_model
from futu_ai_quant.utils.logging import log

LLM_PROVIDER_CHOICES = ("deepseek", "openai", "anthropic", "gemini", "custom")


def add_llm_cli_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--llm-provider",
        choices=LLM_PROVIDER_CHOICES,
        help="覆盖 LLM_PROVIDER（deepseek / openai / anthropic / gemini / custom）",
    )
    parser.add_argument(
        "--llm-model",
        help="覆盖 LLM_MODEL（留空则用提供商默认模型）",
    )


def apply_llm_cli_overrides(args: Any) -> None:
    """将 CLI 参数写入环境变量，须在 load_dotenv 之后、create_llm_client 之前调用。"""
    provider = getattr(args, "llm_provider", None)
    model = getattr(args, "llm_model", None)
    if provider:
        os.environ["LLM_PROVIDER"] = str(provider).strip().lower()
    if model:
        os.environ["LLM_MODEL"] = str(model).strip()


def log_llm_runtime_config() -> None:
    provider = llm_provider()
    model = resolve_llm_model(provider)
    explicit = llm_model()
    if explicit:
        log("模型", f"LLM 配置：provider={provider} model={model}")
    else:
        log("模型", f"LLM 配置：provider={provider} model={model}（提供商默认）")
