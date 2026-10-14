"""Outbound frames and query frames of the desktop channel."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from desktop_support import (
    FakeSessionManager,
    connect_client,
    desktop_channel,
    hello,
    make_cron_service,
    next_event,
    request,
)

from mason.bus.events import OutboundMessage
from mason.bus.outbound_events import ProgressEvent


async def _paired_session(harness: Any) -> tuple[Any, str, str]:
    ws = await connect_client(harness.url)
    await hello(ws)
    first = await request(ws, "c1", "chat.send", session_id=None, text="hello")
    session_id = first["result"]["session_id"]
    turn_id = first["result"]["turn_id"]
    await next_event(ws, "message")
    return ws, session_id, turn_id


async def test_streaming_and_reasoning_frames(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws, session_id, _turn_id = await _paired_session(harness)
        try:
            await harness.channel.send_delta(session_id, "Hel", {}, stream_id="s:0")
            await harness.channel.send_reasoning_delta(session_id, "thinking", {})
            await harness.channel.send_reasoning_end(session_id, {})
            await harness.channel.send_delta(session_id, "lo", {}, stream_id="s:0", stream_end=True)

            first = await next_event(ws, "delta")
            reasoning = await next_event(ws, "reasoning_delta")
            reasoning_end = await next_event(ws, "reasoning_end")
            second = await next_event(ws, "delta")
            final = await next_event(ws, "message")

            assert first["event"]["text_delta"] == "Hel"
            assert second["event"]["text_delta"] == "lo"
            message_id = first["event"]["message_id"]
            assert reasoning["event"]["message_id"] == message_id
            assert reasoning_end["event"]["message_id"] == message_id
            assert final["event"]["message_id"] == message_id
            assert final["event"]["text"] == "Hello"
            assert final["event"]["role"] == "assistant"
            assert final["event"]["session_id"] == session_id
        finally:
            await ws.close()


async def test_resuming_and_merge_next_stream_semantics(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws, session_id, _turn_id = await _paired_session(harness)
        try:
            await harness.channel.send_delta(
                session_id, "discarded", {}, stream_end=True, resuming=True
            )
            await harness.channel.send_delta(
                session_id, "keep", {}, stream_end=True, resuming=True, merge_next=True
            )
            await harness.channel.send_delta(session_id, " more", {}, stream_end=True)

            await next_event(ws, "delta")
            await next_event(ws, "delta")
            await next_event(ws, "delta")
            final = await next_event(ws, "message")
            assert final["event"]["text"] == "keep more"
        finally:
            await ws.close()


async def test_tool_progress_and_file_edit_frames(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws, session_id, _turn_id = await _paired_session(harness)
        try:
            event = ProgressEvent(
                content="shell.exec('ls')",
                tool_hint=True,
                tool_events=[
                    {
                        "version": 1,
                        "phase": "start",
                        "call_id": "tc-1",
                        "name": "shell.exec",
                        "arguments": {"command": "ls"},
                        "result": None,
                        "error": None,
                        "files": [],
                    }
                ],
            )
            await harness.channel.send(
                OutboundMessage(
                    channel="desktop", chat_id=session_id, content=event.content, event=event
                )
            )
            tool = await next_event(ws, "tool")
            assert tool["event"]["tool_call_id"] == "tc-1"
            assert tool["event"]["phase"] == "start"
            assert tool["event"]["name"] == "shell.exec"
            assert "ls" in (tool["event"]["args_preview"] or "")

            progress = await next_event(ws, "progress")
            assert progress["event"]["kind"] == "tool_hint"
            assert progress["event"]["text"] == "shell.exec('ls')"

            await harness.channel.send_file_edit_events(
                session_id,
                [
                    {"path": "a.py", "phase": "start", "status": "editing"},
                    {
                        "path": "a.py",
                        "phase": "end",
                        "status": "done",
                        "diff": {"format": "unified", "text": "@@ -1 +1 @@\n-a\n+b"},
                    },
                ],
            )
            start_edit = await next_event(ws, "file_edit")
            end_edit = await next_event(ws, "file_edit")
            assert start_edit["event"]["path"] == "a.py"
            assert start_edit["event"]["ok"] is None
            assert end_edit["event"]["ok"] is True
            assert ("@@" in (end_edit["event"]["diff_preview"] or "")) is True

            await harness.channel.send(
                OutboundMessage(channel="desktop", chat_id=session_id, content="plain reply")
            )
            message = await next_event(ws, "message")
            assert message["event"]["text"] == "plain reply"
            assert message["event"]["role"] == "assistant"
        finally:
            await ws.close()


async def test_event_turn_ids_are_forwarded_to_frames(tmp_path: Path) -> None:
    """Ids carried by events win over channel-side bookkeeping."""
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws, session_id, turn_id = await _paired_session(harness)
        try:
            await harness.channel.send_delta(
                session_id, "Hel", {}, stream_id="s:0", turn_id="turn-event"
            )
            delta = await next_event(ws, "delta")
            assert delta["event"]["turn_id"] == "turn-event"

            await harness.channel.send_reasoning_delta(
                session_id, "hmm", {}, turn_id="turn-reason"
            )
            reasoning = await next_event(ws, "reasoning_delta")
            assert reasoning["event"]["turn_id"] == "turn-reason"
            await harness.channel.send_reasoning_end(session_id, {}, turn_id="turn-reason")
            reasoning_end = await next_event(ws, "reasoning_end")
            assert reasoning_end["event"]["turn_id"] == "turn-reason"

            event = ProgressEvent(content="working…", tool_hint=True, turn_id="turn-event-2")
            await harness.channel.send(
                OutboundMessage(
                    channel="desktop", chat_id=session_id, content=event.content, event=event
                )
            )
            progress = await next_event(ws, "progress")
            assert progress["event"]["turn_id"] == "turn-event-2"

            await harness.channel.send_file_edit_events(
                session_id,
                [{"path": "a.py", "phase": "start"}],
                {},
                turn_id="turn-event-3",
            )
            edit = await next_event(ws, "file_edit")
            assert edit["event"]["turn_id"] == "turn-event-3"

            # Without an event id, frames fall back to the ack id of the active turn.
            await harness.channel.send_delta(
                session_id, "lo", {}, stream_id="s:0", stream_end=True
            )
            second = await next_event(ws, "delta")
            final = await next_event(ws, "message")
            assert second["event"]["turn_id"] == turn_id
            assert final["event"]["turn_id"] == turn_id
        finally:
            await ws.close()


def _session_payload() -> dict[str, Any]:
    return {
        "key": "desktop:abc",
        "created_at": "2026-10-13T10:00:00",
        "updated_at": "2026-10-13T11:00:00",
        "metadata": {"title": "Hello there"},
        "messages": [
            {"role": "user", "content": "Hi", "timestamp": "2026-10-13T10:00:01"},
            {
                "role": "assistant",
                "content": "ignored command",
                "_command": True,
                "timestamp": "2026-10-13T10:00:02",
            },
            {
                "role": "assistant",
                "content": "Hello!",
                "timestamp": "2026-10-13T10:00:05",
                "reasoning_content": "Say hello back.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": json.dumps(
                                {"path": "greeting.txt", "content": "Hello!"}
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "write_file",
                "content": "Wrote greeting.txt",
                "timestamp": "2026-10-13T10:00:06",
            },
        ],
    }


def _session_rows() -> list[dict[str, Any]]:
    return [
        {
            "key": "desktop:abc",
            "created_at": "2026-10-13T10:00:00",
            "updated_at": "2026-10-13T11:00:00",
            "title": "Hello there",
            "preview": "Hi",
            "path": "/tmp/session.jsonl",
            "message_count": 3,
        },
        {
            "key": "heartbeat",
            "created_at": "2026-10-13T10:00:00",
            "updated_at": "2026-10-13T11:00:00",
            "title": "heartbeat",
            "preview": "",
            "path": "/tmp/heartbeat.jsonl",
            "message_count": 0,
        },
    ]


async def test_query_frames_with_services(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws = await connect_client(harness.url)
        try:
            await hello(ws)
            from types import SimpleNamespace

            job = SimpleNamespace(
                id="c-1",
                name="nightly",
                enabled=True,
                schedule=SimpleNamespace(kind="cron", expr="0 3 * * *", tz=None),
                payload=SimpleNamespace(message="consolidate"),
                state=SimpleNamespace(
                    next_run_at_ms=1760000000000,
                    last_run_at_ms=None,
                    last_status="ok",
                ),
            )
            harness.channel.attach_services(
                session_manager=FakeSessionManager(
                    sessions={"desktop:abc": _session_payload()},
                    rows=_session_rows(),
                    workspace=tmp_path,
                ),
                memory_stats=lambda: {"enabled": True, "memories": 3},
                cron_service=make_cron_service([job]),
            )

            listed = await request(ws, "q1", "sessions.list", limit=10)
            sessions = listed["result"]["sessions"]
            assert [entry["id"] for entry in sessions] == ["desktop:abc"]
            meta = sessions[0]
            assert meta["title"] == "Hello there"
            assert meta["message_count"] == 3
            assert meta["source"] == "desktop"
            expected_ms = int(datetime(2026, 10, 13, 11, 0).timestamp() * 1000)
            assert meta["updated_at"] == expected_ms

            history = await request(ws, "q2", "sessions.history", session_id="desktop:abc")
            messages = history["result"]["messages"]
            assert [message["text"] for message in messages] == ["Hi", "Hello!"]
            assert messages[0]["message_id"] == "desktop:abc:0"
            assert messages[1]["message_id"] == "desktop:abc:2"
            assert messages[1]["role"] == "assistant"
            assert messages[1]["reasoning"] == [{"text": "Say hello back."}]
            assert messages[1]["tools"] == [
                {"name": "write_file", "ok": True},
            ]
            assert messages[1]["file_edits"] == [{"path": "greeting.txt", "ok": True}]
            assert "reasoning" not in messages[0]

            memory = await request(ws, "q3", "memory.stats")
            assert memory["result"] == {"enabled": True, "memories": 3}

            cron = await request(ws, "q4", "cron.list")
            job_payload = cron["result"]["jobs"][0]
            assert job_payload["id"] == "c-1"
            assert job_payload["schedule"] == "0 3 * * *"
            assert job_payload["enabled"] is True
            assert job_payload["next_run_at"] == 1760000000000
            assert job_payload["last_run_at"] is None
            assert job_payload["last_status"] == "ok"
            assert job_payload["payload_preview"] == "consolidate"

            missing = await request(ws, "q5", "sessions.history", session_id="desktop:nope")
            assert missing["error"]["code"] == "no_session"

            approval = await request(ws, "q6", "approve", approval_id="a-1")
            assert approval["error"]["code"] == "no_approval"
            denial = await request(ws, "q7", "deny", approval_id="a-1", reason="nope")
            assert denial["error"]["code"] == "no_approval"
        finally:
            await ws.close()


async def test_query_frames_without_services(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws = await connect_client(harness.url)
        try:
            await hello(ws)
            for frame_id, frame_type in (
                ("e1", "sessions.list"),
                ("e2", "memory.stats"),
                ("e3", "cron.list"),
            ):
                result = await request(ws, frame_id, frame_type)
                assert result["error"]["code"] == "internal"
        finally:
            await ws.close()
