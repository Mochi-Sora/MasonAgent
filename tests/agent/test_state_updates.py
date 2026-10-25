"""Integration coverage for the framework-owned state update."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from mason.agent.loop import AgentLoop
from mason.bus.queue import MessageBus
from mason.providers.base import LLMResponse


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.provider_name = "test"
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_stream_with_retry = AsyncMock(return_value=LLMResponse(content="Done."))
    provider.chat = AsyncMock(return_value=LLMResponse(content=json.dumps({
        "current_goal": "ship the state rewrite",
        "done": "answered the user",
        "next_steps": "verify the tests",
        "session_summary": "state rewrite work",
    })))
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")


async def test_framework_updates_state_after_a_turn(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    await loop.process_direct("ship the state rewrite", session_key="cli:test")
    await loop.join_state_update("cli:test")

    state = loop.context.memory_state_store.for_session("cli:test").read_sections()
    assert state.current_goal == "ship the state rewrite"
    assert state.done == "answered the user"
    assert state.next_steps == "verify the tests"
    assert state.session_summary == "state rewrite work"
    loop.provider.chat.assert_awaited_once()  # type: ignore[attr-defined]


async def test_state_is_injected_into_the_next_prompt(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    await loop.process_direct("first", session_key="cli:test")
    await loop.join_state_update("cli:test")

    await loop.process_direct("second", session_key="cli:test")

    sent = loop.provider.chat_stream_with_retry.call_args.kwargs["messages"]
    system = sent[0]["content"]
    assert "# Session State" in system
    assert "ship the state rewrite" in system
    await loop.join_state_update("cli:test")
