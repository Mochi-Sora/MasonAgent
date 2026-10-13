"""Tests for lazy provider exports from mason.providers."""

from __future__ import annotations

import importlib
import sys


def test_importing_providers_package_is_lazy(monkeypatch) -> None:
    original_package = sys.modules["mason.providers"]
    monkeypatch.delitem(sys.modules, "mason.providers", raising=False)
    monkeypatch.delitem(sys.modules, "mason.providers.anthropic_provider", raising=False)
    monkeypatch.delitem(sys.modules, "mason.providers.openai_compat_provider", raising=False)
    monkeypatch.delitem(sys.modules, "mason.providers.openai_codex_provider", raising=False)
    monkeypatch.delitem(sys.modules, "mason.providers.xai_oauth", raising=False)
    monkeypatch.delitem(sys.modules, "mason.providers.xai_grok_provider", raising=False)
    monkeypatch.delitem(sys.modules, "mason.providers.github_copilot_provider", raising=False)
    monkeypatch.delitem(sys.modules, "mason.providers.azure_openai_provider", raising=False)
    monkeypatch.delitem(sys.modules, "mason.providers.bedrock_provider", raising=False)

    try:
        providers = importlib.import_module("mason.providers")

        assert "mason.providers.anthropic_provider" not in sys.modules
        assert "mason.providers.openai_compat_provider" not in sys.modules
        assert "mason.providers.openai_codex_provider" not in sys.modules
        assert "mason.providers.xai_oauth" not in sys.modules
        assert "mason.providers.xai_grok_provider" not in sys.modules
        assert "mason.providers.github_copilot_provider" not in sys.modules
        assert "mason.providers.azure_openai_provider" not in sys.modules
        assert "mason.providers.bedrock_provider" not in sys.modules
        assert providers.__all__ == [
            "LLMProvider",
            "LLMResponse",
            "LLMUsage",
            "AnthropicProvider",
            "OpenAICompatProvider",
            "OpenAICodexProvider",
            "XAIGrokProvider",
            "GitHubCopilotProvider",
            "AzureOpenAIProvider",
            "BedrockProvider",
        ]
    finally:
        # Importing a replacement subpackage also replaces mason.providers on the
        # parent package. Restore both views so this isolation test cannot pollute
        # later tests that resolve a module through a dotted monkeypatch target.
        monkeypatch.undo()
        setattr(sys.modules["mason"], "providers", original_package)


def test_explicit_provider_import_still_works(monkeypatch) -> None:
    original_package = sys.modules["mason.providers"]
    monkeypatch.delitem(sys.modules, "mason.providers", raising=False)
    monkeypatch.delitem(sys.modules, "mason.providers.anthropic_provider", raising=False)

    try:
        namespace: dict[str, object] = {}
        exec("from mason.providers import AnthropicProvider", namespace)

        assert namespace["AnthropicProvider"].__name__ == "AnthropicProvider"
        assert "mason.providers.anthropic_provider" in sys.modules
    finally:
        monkeypatch.undo()
        setattr(sys.modules["mason"], "providers", original_package)
