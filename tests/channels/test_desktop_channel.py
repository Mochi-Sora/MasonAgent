"""Connection, pairing, chat, and interrupt behavior of the desktop channel."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from desktop_support import (
    FakeSessionManager,
    connect_client,
    desktop_channel,
    hello,
    next_event,
    next_frame,
    request,
)
from websockets.exceptions import ConnectionClosed

from mason.bus.runtime_events import RuntimeEventContext, TurnCompleted, TurnRunStatusChanged
from mason.channels.desktop import runtime as desktop_runtime
from mason.pairing import approve_code, revoke


def _context(session_id: str) -> RuntimeEventContext:
    return RuntimeEventContext(channel="desktop", chat_id=session_id, session_key=session_id)


def _close_code(error: ConnectionClosed) -> int | None:
    received = error.rcvd
    return received.code if received is not None else None


async def _send_chat(ws: Any, frame_id: str, text: str, session_id: str | None = None) -> dict[str, Any]:
    return await request(ws, frame_id, "chat.send", session_id=session_id, text=text)


async def test_hello_requires_pairing_and_lists_code(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=False) as harness:
        ws = await connect_client(harness.url)
        try:
            result = await hello(ws)
            assert result["ok"] is True
            payload = result["result"]
            assert payload["paired"] is False
            assert payload["reason"] == "missing"
            assert payload["pairing"]["code"]
            assert payload["pairing"]["expires_at"] > 0

            gated = await request(ws, "r1", "sessions.list")
            assert gated["error"]["code"] == "not_paired"
        finally:
            await ws.close()


async def test_ping_is_allowed_before_hello(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path) as harness:
        ws = await connect_client(harness.url)
        try:
            result = await request(ws, "p1", "ping", ts=42)
            assert result["result"]["ts"] == 42
            assert isinstance(result["result"]["server_time"], int)
        finally:
            await ws.close()


async def test_pairing_approval_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop_runtime, "PAIRING_POLL_INTERVAL_S", 0.02)
    async with desktop_channel(tmp_path, trustLocalhost=False) as harness:
        ws = await connect_client(harness.url)
        try:
            result = await hello(ws)
            code = result["result"]["pairing"]["code"]
            approved = approve_code(code)
            assert approved is not None
            channel_name, sender_id = approved
            assert channel_name == "desktop"
            assert sender_id.startswith("device-")

            event = await next_event(ws, "pairing.approved", timeout=5)
            assert event["event"]["device_token"] == sender_id

            second = await hello(ws, token=sender_id, frame_id="h2")
            assert second["result"]["paired"] is True
            assert second["result"]["device_token"] == sender_id
            assert second["result"]["gateway"]["version"]
        finally:
            await ws.close()


async def test_missing_token_with_unknown_reason(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=False) as harness:
        ws = await connect_client(harness.url)
        try:
            result = await hello(ws, token="device-never-seen")
            payload = result["result"]
            assert payload["paired"] is False
            assert payload["reason"] == "unknown"
        finally:
            await ws.close()


async def test_trust_localhost_autopairs_and_revocation_is_reported(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws = await connect_client(harness.url)
        try:
            result = await hello(ws)
            assert result["result"]["paired"] is True
            assert result["result"]["device_token"] == "loopback"
        finally:
            await ws.close()

        reuse = await connect_client(harness.url)
        try:
            result = await hello(reuse, token="loopback")
            assert result["result"]["paired"] is True
        finally:
            await reuse.close()

        assert revoke("desktop", "loopback") is True
        revoked = await connect_client(harness.url)
        try:
            result = await hello(revoked, token="loopback")
            assert result["result"]["paired"] is False
            assert result["result"]["reason"] == "revoked"
        finally:
            await revoked.close()


async def test_chat_send_inbound_busy_and_interrupt(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws = await connect_client(harness.url)
        try:
            await hello(ws)
            first = await _send_chat(ws, "c1", "hello there")
            assert first["ok"] is True
            session_id = first["result"]["session_id"]
            turn_id = first["result"]["turn_id"]
            assert session_id.startswith("desktop:")
            assert turn_id.startswith("turn-")

            echo = await next_event(ws, "message")
            assert echo["event"]["role"] == "user"
            assert echo["event"]["text"] == "hello there"

            inbound = await asyncio.wait_for(harness.bus.consume_inbound(), 2)
            assert inbound.channel == "desktop"
            assert inbound.sender_id == "loopback"
            assert inbound.chat_id == session_id
            assert inbound.session_key == session_id
            assert inbound.content == "hello there"

            busy = await _send_chat(ws, "c2", "again", session_id=session_id)
            assert busy["error"]["code"] == "busy"

            interrupt = await request(
                ws, "i1", "turn.interrupt", session_id=session_id, turn_id=turn_id
            )
            assert interrupt["result"]["interrupted"] is True
            stop = await asyncio.wait_for(harness.bus.consume_inbound(), 2)
            assert stop.content == "/stop"
            assert stop.session_key == session_id

            await harness.bus.publish(
                TurnRunStatusChanged(context=_context(session_id), status="idle")
            )
            end = await next_event(ws, "turn.end")
            assert end["event"]["status"] == "interrupted"
            assert end["event"]["turn_id"] == turn_id

            again = await _send_chat(ws, "c3", "fresh turn", session_id=session_id)
            assert again["ok"] is True
            await asyncio.wait_for(harness.bus.consume_inbound(), 2)

            await harness.bus.publish(
                TurnCompleted(
                    context=_context(session_id),
                    outcome="failed",
                    failure_kind="internal",
                )
            )
            failure = await next_event(ws, "turn.end")
            assert failure["event"]["status"] == "error"
            assert failure["event"]["error"] == "internal"
        finally:
            await ws.close()


async def test_interrupt_without_turn_is_rejected(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws = await connect_client(harness.url)
        try:
            await hello(ws)
            result = await request(ws, "i1", "turn.interrupt", session_id="desktop:x", turn_id="turn-y")
            assert result["error"]["code"] == "no_turn"
        finally:
            await ws.close()


async def test_unknown_frames_and_sessions(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws = await connect_client(harness.url)
        try:
            await hello(ws)
            harness.channel.attach_services(session_manager=FakeSessionManager())

            unsupported = await request(ws, "u1", "nonsense.frame")
            assert unsupported["error"]["code"] == "unsupported"

            missing = await _send_chat(ws, "u2", "hi", session_id="desktop:missing")
            assert missing["error"]["code"] == "no_session"

            empty = await _send_chat(ws, "u3", "   ")
            assert empty["error"]["code"] == "bad_request"
        finally:
            await ws.close()


async def test_hello_timeout_closes_4408(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop_runtime, "HELLO_TIMEOUT_S", 0.05)
    async with desktop_channel(tmp_path) as harness:
        ws = await connect_client(harness.url)
        with pytest.raises(ConnectionClosed) as info:
            await asyncio.wait_for(ws.recv(), 5)
        assert _close_code(info.value) == 4408


async def test_protocol_mismatch_and_malformed_frames_close_4400(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path) as harness:
        ws = await connect_client(harness.url)
        try:
            await ws.send(json.dumps({"v": 99, "id": "v1", "type": "hello"}))
            frame = await next_frame(ws)
            assert frame["error"]["code"] == "protocol_mismatch"
            with pytest.raises(ConnectionClosed) as info:
                await asyncio.wait_for(ws.recv(), 5)
            assert _close_code(info.value) == 4400
        finally:
            await ws.close()

        second = await connect_client(harness.url)
        try:
            await second.send("this is not json")
            with pytest.raises(ConnectionClosed) as info:
                await asyncio.wait_for(second.recv(), 5)
            assert _close_code(info.value) == 4400
        finally:
            await second.close()
