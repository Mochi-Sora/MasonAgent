"""Acceptance: the desktop channel inside a live gateway stack.

These tests wire the real components the gateway wires -- a ChannelManager
owning the desktop channel, the outbound dispatcher, and a running AgentLoop
on the same bus -- and drive them with a WebSocket client.  They are the
end-to-end counterpart to ``dev/smoke.mjs``, which only exercises the mock.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from desktop_support import (
    connect_client,
    hello,
    next_event,
    next_frame,
    private_payload_keys,
    request,
)

from mason.agent.loop import AgentLoop
from mason.bus.queue import MessageBus
from mason.channels.desktop import runtime as desktop_runtime
from mason.channels.manager import ChannelManager
from mason.config.loader import set_config_path
from mason.config.schema import Config, ToolApprovalConfig, ToolsConfig
from mason.providers.base import LLMResponse, ToolCallRequest
from mason.session.manager import SessionManager


class _GatewayStack:
    """A manager + agent loop running on one bus, like ``mason gateway``."""

    def __init__(self, bus: MessageBus, loop: AgentLoop, manager: ChannelManager) -> None:
        self.bus = bus
        self.loop = loop
        self.manager = manager
        self._loop_task: asyncio.Task[None] | None = None
        self._manager_task: asyncio.Task[None] | None = None

    @property
    def url(self) -> str:
        port = self.manager.channels["desktop"].bound_port
        assert port is not None
        return f"ws://127.0.0.1:{port}"

    async def start(self) -> None:
        self._manager_task = asyncio.create_task(self.manager.start_all())
        for _ in range(200):
            if self.manager.channels["desktop"].bound_port is not None:
                break
            await asyncio.sleep(0.01)
        assert self.manager.channels["desktop"].bound_port is not None
        self._loop_task = asyncio.create_task(self.loop.run())

    async def aclose(self) -> None:
        await self.manager.stop_all()
        if self._manager_task is not None:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._manager_task, 5)
        if self._loop_task is not None:
            self._loop_task.cancel()
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._loop_task, 5)


@contextlib.asynccontextmanager
async def gateway_stack(tmp_path: Path, provider: MagicMock):
    set_config_path(tmp_path / "config.json")
    config = Config.model_validate({
        "channels": {
            "desktop": {
                "enabled": True,
                "host": "127.0.0.1",
                "port": 0,
                "trustLocalhost": True,
                "streaming": True,
            },
        },
    })
    bus = MessageBus()
    sessions = SessionManager(tmp_path / "workspace", sessions_root=tmp_path / "sessions")
    manager = ChannelManager(
        config,
        bus,
        session_manager=sessions,
        config_path=tmp_path / "config.json",
    )
    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path / "workspace",
        model="test-model",
        session_manager=sessions,
        tools_config=ToolsConfig(
            approval=ToolApprovalConfig(enabled=True, require=["write_file"]),
        ),
    )
    stack = _GatewayStack(bus, loop, manager)
    await stack.start()
    try:
        yield stack
    finally:
        await stack.aclose()


def _provider(*responses: LLMResponse) -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    provider.estimate_prompt_tokens.return_value = (10_000, "test")
    provider.chat_stream_with_retry = AsyncMock(side_effect=list(responses))
    return provider


async def _expect_frames_in_order(
    ws: Any, types: list[str], timeout: float = 5
) -> dict[str, dict[str, Any]]:
    """Consume frames until each expected type arrives, in order.

    Unrelated frames (progress, tool, deltas) are skipped, but any of the
    expected types arriving too early fails the sequence.
    """
    from desktop_support import next_frame

    frames: dict[str, dict[str, Any]] = {}
    for expected in types:
        while True:
            frame = await next_frame(ws, timeout)
            if "id" in frame:
                continue
            kind = frame.get("type")
            if kind == expected:
                frames[expected] = frame
                break
            if kind in types:
                raise AssertionError(f"{kind} arrived before {expected}")
    return frames


@pytest.mark.asyncio
async def test_gateway_round_trip_with_approval(tmp_path: Path) -> None:
    provider = _provider(
        LLMResponse(
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="write_file",
                    arguments={"path": "note.txt", "content": "hello"},
                )
            ],
        ),
        LLMResponse(content="Saved the note.", finish_reason="stop"),
    )

    async with gateway_stack(tmp_path, provider) as stack:
        ws = await connect_client(stack.url)
        try:
            paired = await hello(ws)
            assert paired["result"]["paired"] is True
            pong = await request(ws, "p1", "ping", ts=1)
            assert pong["result"]["ts"] == 1

            sent = await request(ws, "c1", "chat.send", text="save a note")
            assert sent["ok"] is True
            session_id = sent["result"]["session_id"]
            assert session_id.startswith("desktop:")

            echo = await next_event(ws, "message")
            assert echo["event"]["role"] == "user"
            assert echo["event"]["text"] == "save a note"

            approval = await next_event(ws, "approval")
            assert approval["event"]["tool"] == "write_file"
            assert approval["event"]["session_id"] == session_id
            approved = await request(
                ws, "a1", "approve", approval_id=approval["event"]["approval_id"]
            )
            assert approved["result"]["approved"] is True

            # Protocol order: the persisted session update, the final message
            # (carrying the client-known turn id), then the terminal signal.
            ordered = await _expect_frames_in_order(
                ws, ["session.updated", "message", "turn.end"]
            )
            assert ordered["session.updated"]["event"]["session_id"] == session_id
            final = ordered["message"]["event"]
            assert final["role"] == "assistant"
            assert final["text"] == "Saved the note."
            assert final["turn_id"] == sent["result"]["turn_id"]
            end = ordered["turn.end"]["event"]
            assert end["status"] == "completed"
            assert end["session_id"] == session_id

            assert (tmp_path / "workspace" / "note.txt").read_text(encoding="utf-8") == "hello"

            listed = await request(ws, "s1", "sessions.list")
            entries = listed["result"]["sessions"]
            assert [entry["id"] for entry in entries] == [session_id]
            assert entries[0]["message_count"] > 0
        finally:
            await ws.close()


@pytest.mark.asyncio
async def test_gateway_turn_interrupt_publishes_interrupted(tmp_path: Path) -> None:
    started = asyncio.Event()

    async def hang(**_kwargs: Any) -> LLMResponse:
        started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    provider = _provider()
    provider.chat_stream_with_retry = AsyncMock(side_effect=hang)

    async with gateway_stack(tmp_path, provider) as stack:
        ws = await connect_client(stack.url)
        try:
            await hello(ws)
            sent = await request(ws, "c1", "chat.send", text="start a long task")
            turn_id = sent["result"]["turn_id"]
            session_id = sent["result"]["session_id"]
            await next_event(ws, "message")

            await asyncio.wait_for(started.wait(), 5)
            interrupted = await request(
                ws, "i1", "turn.interrupt", session_id=session_id, turn_id=turn_id
            )
            assert interrupted["result"]["interrupted"] is True

            end = await next_event(ws, "turn.end")
            assert end["event"]["status"] == "interrupted"
            assert end["event"]["turn_id"] == turn_id
        finally:
            await ws.close()


async def _frames_until_turn_end(ws: Any) -> list[dict[str, Any]]:
    """Collect event frames (skipping responses) through the terminal signal."""
    frames: list[dict[str, Any]] = []
    while True:
        frame = await next_frame(ws, timeout=5)
        if "id" in frame:
            continue
        frames.append(frame)
        if frame.get("type") == "turn.end":
            return frames


@pytest.mark.asyncio
async def test_gateway_length_recovery_leaves_history_clean(tmp_path: Path) -> None:
    """A truncated answer replays as folded segments, never the internal prompt."""
    responses = iter([
        LLMResponse(content="First segment of a very long answer.", finish_reason="length"),
        LLMResponse(content=" Second segment continues the answer.", finish_reason="stop"),
    ])

    async def stream(*, on_content_delta: Any, **_kwargs: Any) -> LLMResponse:
        response = next(responses)
        await on_content_delta(response.content or "")
        return response

    provider = _provider()
    provider.chat_stream_with_retry = AsyncMock(side_effect=stream)

    async with gateway_stack(tmp_path, provider) as stack:
        ws = await connect_client(stack.url)
        try:
            await hello(ws)
            sent = await request(ws, "c1", "chat.send", text="write a very long answer")
            session_id = sent["result"]["session_id"]

            frames = await _frames_until_turn_end(ws)
            assert frames[-1]["event"]["status"] == "completed"
            assert frames[-1]["event"]["session_id"] == session_id
            events = [(frame["type"], frame["event"]) for frame in frames]
            assistant_messages = [
                event for kind, event in events
                if kind == "message" and event.get("role") == "assistant"
            ]
            assert len(assistant_messages) == 1
            final_text = assistant_messages[0]["text"]
            assert final_text.startswith("First segment of a very long answer.")
            assert final_text.endswith("Second segment continues the answer.")
            assert not any(event.get("replaced") for _kind, event in events)

            history = await request(ws, "h1", "sessions.history", session_id=session_id)
            messages = history["result"]["messages"]
            assert [message["role"] for message in messages] == [
                "user",
                "assistant",
                "assistant",
            ]
            assert [message["text"] for message in messages] == [
                "write a very long answer",
                "First segment of a very long answer.",
                "Second segment continues the answer.",
            ]
            assert messages[1].get("continued") is None
            assert messages[2]["continued"] is True
            assert not any("cut off" in message["text"] for message in messages)

            listed = await request(ws, "s1", "sessions.list")
            entry = listed["result"]["sessions"][0]
            assert entry["id"] == session_id
            assert entry["message_count"] == 3
        finally:
            await ws.close()


@pytest.mark.asyncio
async def test_gateway_attachment_records_reach_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An uploaded image survives a reload as a record and refetchable bytes."""
    media_dir = tmp_path / "desktop-media"
    media_dir.mkdir(parents=True, exist_ok=True)

    def fake_get_media_dir(channel: str | None = None) -> Path:
        media_dir.mkdir(parents=True, exist_ok=True)
        return media_dir

    monkeypatch.setattr(desktop_runtime, "get_media_dir", fake_get_media_dir)

    provider = _provider(LLMResponse(content="Got the photo.", finish_reason="stop"))

    async with gateway_stack(tmp_path, provider) as stack:
        ws = await connect_client(stack.url)
        try:
            paired = await hello(ws)
            assert paired["result"]["limits"]["max_fetch_bytes"] == 25 * 1024 * 1024
            raw = b"\x89PNG fake image body"
            upload = await request(
                ws,
                "u1",
                "media.upload",
                name="photo.png",
                mime="image/png",
                kind="image",
                data=base64.b64encode(raw).decode("ascii"),
            )
            assert upload["ok"] is True
            attachment = upload["result"]

            sent = await request(
                ws,
                "c1",
                "chat.send",
                text="look at this",
                attachments=[attachment["attachment_id"]],
            )
            session_id = sent["result"]["session_id"]
            frames = await _frames_until_turn_end(ws)
            events = [frame["event"] for frame in frames]
            echo = next(event for event in events if event.get("role") == "user")
            assert echo["attachments"] == [attachment]
            assert frames[-1]["event"]["status"] == "completed"

            history = await request(ws, "h1", "sessions.history", session_id=session_id)
            messages = history["result"]["messages"]
            user_message = messages[0]
            assert user_message["role"] == "user"
            assert user_message["text"] == "look at this"
            assert user_message["attachments"] == [attachment]
            for message in messages:
                assert str(media_dir) not in message["text"]

            # Reload path: the transcript record resolves the stored bytes.
            fetched = await request(
                ws,
                "m1",
                "media.fetch",
                session_id=session_id,
                attachment_id=attachment["attachment_id"],
            )
            assert fetched["ok"] is True
            assert fetched["result"] == {
                **attachment,
                "data": base64.b64encode(raw).decode("ascii"),
            }

            # The stored transcript keeps the structured record and its private
            # path, but no frame may carry an underscore-prefixed key.
            stored = next((tmp_path / "sessions").rglob("*.jsonl"))
            raw_transcript = stored.read_text(encoding="utf-8")
            assert attachment["attachment_id"] in raw_transcript
            assert "photo.png" in raw_transcript
            assert "_path" in raw_transcript
            assert "[image:" not in user_message["text"]
            for frame in [*frames, history, fetched]:
                assert private_payload_keys(frame) == []
        finally:
            await ws.close()
