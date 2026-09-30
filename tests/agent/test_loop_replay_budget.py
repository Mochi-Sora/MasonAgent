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
from nanobot.providers.base import LLMResponse


def _make_loop(tmp_path: Path, *, window: int = 128_000, replay_max: int = 0) -> AgentLoop:
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
    )


def _seed(session, turns: int) -> None:
    for index in range(turns):
        session.add_message("user", f"question {index}")
        session.add_message("assistant", f"answer {index}")


async def test_replay_budget_drops_old_turns_from_the_prompt(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, replay_max=40)
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
    loop = _make_loop(tmp_path, replay_max=0)
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
