"""Tests for the framework-triggered session-state writer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mason.memory.state import MemoryState, SessionState
from mason.memory.state_writer import StateWriter
from mason.providers.base import GenerationSettings, LLMResponse
from mason.utils.llm_runtime import LLMRuntime


@pytest.fixture
def state(tmp_path: Path) -> MemoryState:
    return MemoryState(tmp_path, session_key="cli:1")


@pytest.fixture
def runtime() -> LLMRuntime:
    provider = MagicMock()
    provider.chat = AsyncMock()
    provider.generation = GenerationSettings(max_tokens=500)
    return LLMRuntime.capture(provider, "test-model", context_window_tokens=8_000)


async def test_writer_applies_all_four_sections(
    state: MemoryState, runtime: LLMRuntime
) -> None:
    runtime.provider.chat.return_value = LLMResponse(  # type: ignore[attr-defined]
        content=json.dumps({
            "current_goal": "finish the state rewrite",
            "done": "added session-scoped state",
            "next_steps": "add writer tests",
            "session_summary": "working on state",
        })
    )

    result = await StateWriter().update(
        state,
        runtime,
        session_key="cli:1",
        user_message="please rework state",
        assistant_response="I added the store.",
        tool_lines=["- write_file: ok — state.py"],
    )

    assert result.ok is True
    assert result.updated is True
    assert state.read_sections() == SessionState(
        current_goal="finish the state rewrite",
        done="added session-scoped state",
        next_steps="add writer tests",
        session_summary="working on state",
    )


async def test_writer_preserves_previous_sections_on_partial_output(
    state: MemoryState, runtime: LLMRuntime
) -> None:
    state.write_sections(
        SessionState(current_goal="old goal", done="old done", next_steps="old next")
    )
    runtime.provider.chat.return_value = LLMResponse(  # type: ignore[attr-defined]
        content=json.dumps({"next_steps": "new next"})
    )

    result = await StateWriter().update(
        state,
        runtime,
        session_key="cli:1",
        user_message="next",
        assistant_response="ok",
    )

    assert result.ok is True
    sections = state.read_sections()
    assert sections.current_goal == "old goal"
    assert sections.done == "old done"
    assert sections.next_steps == "new next"


async def test_writer_keeps_state_when_model_fails(
    state: MemoryState, runtime: LLMRuntime
) -> None:
    state.write_sections(SessionState(current_goal="keep me"))
    runtime.provider.chat.side_effect = RuntimeError("provider down")  # type: ignore[attr-defined]

    result = await StateWriter().update(
        state,
        runtime,
        session_key="cli:1",
        user_message="hello",
        assistant_response="hi",
    )

    assert result.ok is False
    assert state.read_sections().current_goal == "keep me"


async def test_writer_keeps_state_when_json_is_unusable(
    state: MemoryState, runtime: LLMRuntime
) -> None:
    state.write_sections(SessionState(current_goal="keep me"))
    runtime.provider.chat.return_value = LLMResponse(  # type: ignore[attr-defined]
        content="I could not summarize that."
    )

    result = await StateWriter().update(
        state,
        runtime,
        session_key="cli:1",
        user_message="hello",
        assistant_response="hi",
    )

    assert result.ok is False
    assert state.read_sections().current_goal == "keep me"


async def test_writer_does_not_restore_state_after_clear(
    state: MemoryState, runtime: LLMRuntime
) -> None:
    state.write_sections(SessionState(current_goal="old"))
    runtime.provider.chat.side_effect = None  # type: ignore[attr-defined]
    runtime.provider.chat.return_value = LLMResponse(  # type: ignore[attr-defined]
        content=json.dumps({"current_goal": "late writer"})
    )

    # Simulate a clear while the writer is running by clearing before applying.
    from mason.memory import state_writer as writer_module

    original = writer_module._parse_plan
    writer_module._parse_plan = lambda text: (  # type: ignore[assignment]
        state.clear() or original(text)
    )
    try:
        result = await StateWriter().update(
            state,
            runtime,
            session_key="cli:1",
            user_message="hello",
            assistant_response="hi",
        )
    finally:
        writer_module._parse_plan = original

    assert result.ok is True
    assert result.updated is False
    assert state.read() == ""
