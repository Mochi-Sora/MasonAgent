"""Loop-to-backup integration: every persisted turn must reach the capture.

The user message is persisted before the run (``_persist_user_message_early``)
and skipped by the save boundary, so the backup tier must include it explicitly;
otherwise ``recall_backup`` only ever sees assistant replies.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import LLMResponse


def _make_loop(tmp_path: Path, *replies: str) -> AgentLoop:
    provider = MagicMock()
    provider.provider_name = "test"
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(max_tokens=4096)
    provider.chat_stream_with_retry = AsyncMock(
        side_effect=[LLMResponse(content=reply) for reply in replies],
    )
    return AgentLoop(bus=MessageBus(), provider=provider, workspace=tmp_path, model="test-model")


async def test_turn_captures_user_and_assistant_messages(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, "Noted.")

    await loop.process_direct(
        "remember my secret phrase is BANANA42",
        session_key="telegram:1",
        channel="cli",
        chat_id="1",
    )

    db = loop.context.memory_db
    assert [(hit.role, hit.content) for hit in db.recent_episodes(limit=10)] == [
        ("assistant", "Noted."),
        ("user", "remember my secret phrase is BANANA42"),
    ]
    assert [hit.role for hit in db.search_episodes("BANANA42")] == ["user"]


async def test_each_turn_is_captured_exactly_once(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path, "First reply.", "Second reply.")

    for content in ("first turn", "second turn"):
        await loop.process_direct(
            content,
            session_key="telegram:1",
            channel="cli",
            chat_id="1",
        )

    contents = [hit.content for hit in loop.context.memory_db.recent_episodes(limit=10)]
    assert sorted(contents) == ["First reply.", "Second reply.", "first turn", "second turn"]


async def test_capture_survives_a_long_session(tmp_path: Path) -> None:
    """Later turns keep capturing the early-persisted user message, not just turn one."""
    loop = _make_loop(tmp_path, "one", "two", "three")

    for content in ("alpha", "beta", "gamma"):
        await loop.process_direct(
            content,
            session_key="telegram:1",
            channel="cli",
            chat_id="1",
        )

    db = loop.context.memory_db
    for content in ("alpha", "beta", "gamma"):
        assert [hit.role for hit in db.search_episodes(content)] == ["user"]
