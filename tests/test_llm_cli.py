"""LLM CLI 与 Gemini 提供商配置测试。"""

from __future__ import annotations

import argparse
import os

import pytest

from futu_ai_quant.llm.cli import add_llm_cli_arguments, apply_llm_cli_overrides
from futu_ai_quant.llm.settings import gemini_base_url, resolve_llm_model


def test_apply_llm_cli_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    parser = argparse.ArgumentParser()
    add_llm_cli_arguments(parser)
    args = parser.parse_args(["--llm-provider", "gemini", "--llm-model", "gemini-2.5-flash"])
    apply_llm_cli_overrides(args)
    assert os.environ["LLM_PROVIDER"] == "gemini"
    assert os.environ["LLM_MODEL"] == "gemini-2.5-flash"
    assert resolve_llm_model("gemini") == "gemini-2.5-flash"


def test_gemini_default_model_and_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert resolve_llm_model("gemini") == "gemini-3.5-flash"
    assert "generativelanguage.googleapis.com" in gemini_base_url()
