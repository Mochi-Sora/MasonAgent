"""Tests for AgentLoop wiring of lazy capability discovery."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import LazyCapabilitiesConfig, ToolsConfig
from nanobot.providers.base import LLMResponse


def _make_loop(tmp_path: Path, tools_config: ToolsConfig | None = None) -> AgentLoop:
    provider = MagicMock()
    provider.provider_name = "test"
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_stream_with_retry = MagicMock(return_value=LLMResponse(content="ok"))
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        tools_config=tools_config,
    )


def test_loop_registers_find_capabilities(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    assert loop.tools.has("find_capabilities")
    assert loop.tools.get("find_capabilities")._skills is loop.context.skills


def test_loop_gate_hides_tools_and_keeps_memory_tools_visible(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    gate = loop._build_capability_gate(loop.tools)

    assert gate is not None
    visible = set(gate.visible_names())
    assert "find_capabilities" in visible
    assert {"update_state", "recall_memory", "recall_backup"} <= visible
    assert "read_file" not in visible
    assert "`read_file`" in gate.catalog()


def test_loop_gate_includes_configured_always_visible_tools(tmp_path: Path) -> None:
    loop = _make_loop(
        tmp_path,
        ToolsConfig(lazy_capabilities=LazyCapabilitiesConfig(always_visible=["read_file"])),
    )

    gate = loop._build_capability_gate(loop.tools)

    assert gate is not None
    assert "read_file" in gate.visible_names()


def test_loop_skips_gate_when_disabled(tmp_path: Path) -> None:
    loop = _make_loop(
        tmp_path,
        ToolsConfig(lazy_capabilities=LazyCapabilitiesConfig(enabled=False)),
    )

    assert loop._build_capability_gate(loop.tools) is None
    assert not loop.tools.has("find_capabilities")
    assert "find_capabilities" not in loop.context.build_system_prompt()


def test_loop_system_prompt_advertises_discovery(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    prompt = loop.context.build_system_prompt()

    assert "find_capabilities" in prompt
    assert "Before you conclude that something is impossible" in prompt
