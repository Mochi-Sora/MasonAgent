"""The loop adopts a caller-supplied turn id from inbound metadata.

Surfaces that must hand out a correlation id before the turn starts (the
desktop protocol's ``chat.send`` acknowledgement) put it on the inbound
metadata; every event for the turn then carries that same id.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.session_helpers import run_session
from mason.agent.loop import AgentLoop
from mason.bus.events import InboundMessage
from mason.bus.queue import MessageBus
from mason.session.turn_metadata import TURN_ID_METADATA_KEY


def _make_loop(tmp_path: Path) -> AgentLoop:
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    with patch("mason.agent.loop.ContextBuilder"), \
         patch("mason.agent.loop.SessionManager"), \
         patch("mason.agent.loop.SubagentManager") as mock_subagent_manager:
        mock_subagent_manager.return_value.cancel_by_session = AsyncMock(return_value=0)
        return AgentLoop(bus=bus, provider=provider, workspace=tmp_path)


def _capture_turn_ids(loop: AgentLoop) -> list[str]:
    seen: list[str] = []

    async def fake_stage(ctx, name, handler):
        seen.append(ctx.turn_id)
        return False if name == "command" else None

    loop._run_turn_stage = fake_stage  # type: ignore[method-assign]
    loop._log_turn_completion = MagicMock()  # type: ignore[method-assign]
    return seen


@pytest.mark.asyncio
async def test_dispatch_adopts_a_supplied_turn_id(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    seen = _capture_turn_ids(loop)

    msg = InboundMessage(
        channel="desktop",
        sender_id="device",
        chat_id="desktop:1",
        content="hi",
        metadata={TURN_ID_METADATA_KEY: "turn-supplied"},
    )
    await run_session(loop, msg)

    assert seen
    assert set(seen) == {"turn-supplied"}


@pytest.mark.asyncio
async def test_dispatch_generates_a_turn_id_when_none_is_supplied(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    seen = _capture_turn_ids(loop)

    msg = InboundMessage(channel="desktop", sender_id="device", chat_id="desktop:1", content="hi")
    await run_session(loop, msg)

    prefix = f"{loop._effective_session_key(msg)}:"  # type: ignore[reportPrivateUsage]
    assert seen
    assert all(turn_id.startswith(prefix) for turn_id in seen)
