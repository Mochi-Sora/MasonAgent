"""Session replay budget: a long conversation must not grow the prompt without bound.

The full session stays on disk and in the backup tier; only the most recent
turns are replayed, and the rest is recallable with ``recall_backup``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse, ProviderConversationState, ToolCallRequest


def _make_loop(
    tmp_path: Path,
    *,
    window: int = 128_000,
    replay_max: int = 0,
    replay_mode: str | None = None,
) -> AgentLoop:
    provider = MagicMock()
    provider.provider_name = "test"
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_stream_with_retry = AsyncMock(return_value=LLMResponse(content="Noted."))
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=window,
        session_replay_max_tokens=replay_max,
        session_replay=replay_mode,
    )


def _seed(session, turns: int) -> None:
    for index in range(turns):
        session.add_message("user", f"question {index}")
        session.add_message("assistant", f"answer {index}")


async def test_replay_budget_drops_old_turns_from_the_prompt(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, replay_max=40, replay_mode="tail")
    session = loop.sessions.get_or_create("cli:test")
    _seed(session, 20)
    loop.sessions.save(session)

    await loop.process_direct("latest question", session_key="cli:test")

    sent = loop.provider.chat_stream_with_retry.call_args.kwargs["messages"]
    replayed = [message for message in sent if message["role"] != "system"]
    # The prompt keeps a recent tail, not the whole session.
    assert len(replayed) < len(session.messages) + 1
    assert replayed[0]["role"] == "user"
    assert replayed[-1]["content"] == "latest question"
    assert all("question 0" != message.get("content") for message in replayed)

    # Nothing is deleted: the session and the backup both still have it all.
    reloaded = loop.sessions.get_or_create("cli:test")
    assert reloaded.messages[0]["content"] == "question 0"
    assert [hit.role for hit in loop.context.memory_db.search_episodes("question 0")] == ["user"]


async def test_zero_budget_replays_everything(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, replay_max=0, replay_mode="tail")
    session = loop.sessions.get_or_create("cli:test")
    _seed(session, 20)
    loop.sessions.save(session)

    await loop.process_direct("latest question", session_key="cli:test")

    sent = loop.provider.chat_stream_with_retry.call_args.kwargs["messages"]
    contents = [message.get("content") for message in sent]
    assert "question 0" in contents


async def test_budget_is_clamped_to_a_quarter_of_a_small_window(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, window=40_000, replay_max=32_000)
    runtime = loop.llm_runtime()

    # min(configured, max(2048, window // 4))
    assert loop._replay_budget(runtime) == 10_000

    floor = _make_loop(tmp_path / "floor", window=4_000, replay_max=32_000)
    assert floor._replay_budget(floor.llm_runtime()) == 2_048


async def test_budget_keeps_configured_cap_on_a_large_window(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, window=1_000_000, replay_max=32_000)
    runtime = loop.llm_runtime()

    assert loop._replay_budget(runtime) == 32_000
    # A configured cap larger than the window share is clamped too.
    loop._session_replay_max_tokens = 400_000
    assert loop._replay_budget(runtime) == 250_000


class TestReplayBudgetConfig:
    def test_default_is_32k(self) -> None:
        from nanobot.config.schema import AgentDefaults

        assert AgentDefaults().session_replay_max_tokens == 32_000

    def test_alias_and_serialization(self) -> None:
        from nanobot.config.schema import AgentDefaults

        defaults = AgentDefaults.model_validate({"sessionReplayMaxTokens": 5_000})
        assert defaults.session_replay_max_tokens == 5_000
        data = defaults.model_dump(mode="json", by_alias=True)
        assert data["sessionReplayMaxTokens"] == 5_000

    def test_zero_disables_the_budget(self) -> None:
        from nanobot.config.schema import AgentDefaults

        assert AgentDefaults(session_replay_max_tokens=0).session_replay_max_tokens == 0


async def test_none_mode_sends_no_history(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, replay_mode="none")
    session = loop.sessions.get_or_create("cli:test")
    _seed(session, 5)
    loop.sessions.save(session)

    await loop.process_direct("fresh question", session_key="cli:test")

    sent = loop.provider.chat_stream_with_retry.call_args.kwargs["messages"]
    assert [message["role"] for message in sent] == ["system", "user"]
    assert sent[1]["content"] == "fresh question"

    # Storage and memory capture are unaffected by the replay mode.
    reloaded = loop.sessions.get_or_create("cli:test")
    assert len(reloaded.messages) == 12
    assert reloaded.messages[0]["content"] == "question 0"
    assert [hit.role for hit in loop.context.memory_db.search_episodes("question 0")] == ["user"]
    assert [hit.role for hit in loop.context.memory_db.search_episodes("fresh question")] == ["user"]


async def test_none_mode_does_not_stage_provider_state(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, replay_mode="none")
    loop.provider.can_resume_conversation_state.return_value = True
    session = loop.sessions.get_or_create("cli:test")
    session.provider_state = ProviderConversationState(
        kind="openai_responses",
        provider="openai:test",
        model="test-model",
        version=1,
        payload={"items": []},
    )
    loop.sessions.save(session)

    await loop.process_direct("fresh question", session_key="cli:test")

    sent = loop.provider.chat_stream_with_retry.call_args.kwargs
    assert sent["provider_context"].conversation_state is None
    assert loop.sessions.get_or_create("cli:test").provider_state is None


async def test_full_mode_replays_everything(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, replay_max=1, replay_mode="full")
    session = loop.sessions.get_or_create("cli:test")
    _seed(session, 20)
    loop.sessions.save(session)

    await loop.process_direct("latest question", session_key="cli:test")

    sent = loop.provider.chat_stream_with_retry.call_args.kwargs["messages"]
    contents = [message.get("content") for message in sent]
    assert "question 0" in contents


async def test_state_check_note_follows_a_turn_without_update_state(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, replay_mode="none")

    await loop.process_direct("hello", session_key="cli:test")

    session = loop.sessions.get_or_create("cli:test")
    assert session.metadata["_state_stale_turns"] == 1

    await loop.process_direct("second message", session_key="cli:test")

    sent = loop.provider.chat_stream_with_retry.call_args.kwargs["messages"]
    assert "State check" in str(sent[-1]["content"])
    assert sent[-1]["content"].startswith("second message")


async def test_state_check_note_is_absent_after_update_state(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, replay_mode="none")
    loop.provider.chat_stream_with_retry = AsyncMock(side_effect=[
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call-1",
                    name="update_state",
                    arguments={"content": "goal: ship the memory rework"},
                )
            ],
        ),
        LLMResponse(content="Noted."),
    ])

    await loop.process_direct("remember the goal", session_key="cli:test")

    session = loop.sessions.get_or_create("cli:test")
    assert session.metadata["_state_stale_turns"] == 0
    assert loop.context.memory_state.read() == "goal: ship the memory rework"

    loop.provider.chat_stream_with_retry = AsyncMock(return_value=LLMResponse(content="Sure."))
    await loop.process_direct("next", session_key="cli:test")

    sent = loop.provider.chat_stream_with_retry.call_args.kwargs["messages"]
    assert "State check" not in str(sent[-1]["content"])


class TestReplayModeConfig:
    def test_default_mode_is_none(self) -> None:
        from nanobot.config.schema import AgentDefaults

        # The framework remembers state and memory, not conversation history.
        assert AgentDefaults().session_replay == "none"

    def test_mode_aliases_and_serialization(self) -> None:
        from nanobot.config.schema import AgentDefaults

        defaults = AgentDefaults.model_validate({"sessionReplay": "none"})
        assert defaults.session_replay == "none"
        data = defaults.model_dump(mode="json", by_alias=True)
        assert data["sessionReplay"] == "none"

    def test_invalid_mode_is_rejected(self) -> None:
        import pytest

        from nanobot.config.schema import AgentDefaults

        with pytest.raises(Exception):
            AgentDefaults.model_validate({"sessionReplay": "sometimes"})
