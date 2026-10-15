"""Attachment upload and chat.send attachment resolution for the desktop channel."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import pytest
from desktop_support import (
    connect_client,
    desktop_channel,
    hello,
    next_event,
    next_frame,
    request,
)

from mason.channels.desktop import runtime as desktop_runtime


@pytest.fixture(autouse=True)
def _desktop_media_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep uploaded attachments out of the real media directory."""
    media_dir = tmp_path / "desktop-media"
    media_dir.mkdir(parents=True, exist_ok=True)

    def fake_get_media_dir(channel: str | None = None) -> Path:
        media_dir.mkdir(parents=True, exist_ok=True)
        return media_dir

    monkeypatch.setattr(desktop_runtime, "get_media_dir", fake_get_media_dir)
    return media_dir


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


async def _upload(ws: Any, frame_id: str, data: bytes, **fields: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "name": "photo.jpg",
        "mime": "image/jpeg",
        "kind": "image",
        "data": _b64(data),
    }
    params.update(fields)
    return await request(ws, frame_id, "media.upload", **params)


def _is_uuid_stem(path: Path) -> bool:
    return len(path.stem) == 32 and all(char in "0123456789abcdef" for char in path.stem)


async def test_upload_then_chat_send_publishes_media(
    tmp_path: Path, _desktop_media_dir: Path
) -> None:
    raw = b"\xff\xd8\xff\xe0 fake jpeg body"
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws = await connect_client(harness.url)
        try:
            await hello(ws)
            upload = await _upload(ws, "u1", raw, duration_ms=4200)
            assert upload["ok"] is True
            result = upload["result"]
            assert result["attachment_id"].startswith("att-")
            assert result == {
                "attachment_id": result["attachment_id"],
                "name": "photo.jpg",
                "mime": "image/jpeg",
                "kind": "image",
                "size": len(raw),
            }

            sent = await request(
                ws,
                "c1",
                "chat.send",
                session_id=None,
                text="what is this?",
                attachments=[result["attachment_id"]],
            )
            assert sent["ok"] is True
            session_id = sent["result"]["session_id"]
            assert session_id.startswith("desktop:")

            echo = await next_event(ws, "message")
            assert echo["event"]["role"] == "user"
            assert echo["event"]["session_id"] == session_id
            assert echo["event"]["text"] == "what is this?"
            assert echo["event"]["attachments"] == [result]

            inbound = await asyncio.wait_for(harness.bus.consume_inbound(), 2)
            assert inbound.content == "what is this?"
            assert len(inbound.media) == 1
            stored = Path(inbound.media[0])
            assert stored.is_file()
            assert stored.read_bytes() == raw
            assert stored.parent == _desktop_media_dir
            assert stored.suffix == ".jpg"
            assert _is_uuid_stem(stored)
        finally:
            await ws.close()


async def test_empty_text_with_attachment_builds_placeholder(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws = await connect_client(harness.url)
        try:
            await hello(ws)
            missing = await request(ws, "c0", "chat.send", session_id=None, text="")
            assert missing["ok"] is False
            assert missing["error"]["code"] == "bad_request"

            upload = await _upload(ws, "u1", b"pixels")
            attachment_id = upload["result"]["attachment_id"]
            sent = await request(
                ws,
                "c1",
                "chat.send",
                session_id=None,
                text="",
                attachments=[attachment_id],
            )
            assert sent["ok"] is True

            echo = await next_event(ws, "message")
            assert echo["event"]["text"] == ""
            assert echo["event"]["attachments"][0]["attachment_id"] == attachment_id

            inbound = await asyncio.wait_for(harness.bus.consume_inbound(), 2)
            assert inbound.content == "[image: photo.jpg]"
            assert len(inbound.media) == 1
            assert Path(inbound.media[0]).is_file()
        finally:
            await ws.close()


async def test_unknown_attachment_is_rejected_without_turn(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws = await connect_client(harness.url)
        try:
            await hello(ws)
            denied = await request(
                ws,
                "c1",
                "chat.send",
                session_id=None,
                text="hi",
                attachments=["att-unknown"],
            )
            assert denied["ok"] is False
            assert denied["error"]["code"] == "no_attachment"
            assert harness.bus.inbound_size == 0

            # No user-turn echo either: the rejected send starts nothing.
            with pytest.raises(asyncio.TimeoutError):
                await next_frame(ws, timeout=0.2)
        finally:
            await ws.close()


async def test_upload_is_owned_by_its_connection(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        owner = await connect_client(harness.url)
        visitor = await connect_client(harness.url)
        try:
            await hello(owner, frame_id="h1")
            await hello(visitor, frame_id="h2")
            upload = await _upload(owner, "u1", b"photo bytes")
            attachment_id = upload["result"]["attachment_id"]

            denied = await request(
                visitor,
                "c1",
                "chat.send",
                session_id=None,
                text="steal?",
                attachments=[attachment_id],
            )
            assert denied["error"]["code"] == "no_attachment"
            assert harness.bus.inbound_size == 0

            # The owner can still consume the id: the foreign lookup did not.
            sent = await request(
                owner,
                "c2",
                "chat.send",
                session_id=None,
                text="mine",
                attachments=[attachment_id],
            )
            assert sent["ok"] is True
            await next_event(owner, "message")
            inbound = await asyncio.wait_for(harness.bus.consume_inbound(), 2)
            assert Path(inbound.media[0]).is_file()
        finally:
            await owner.close()
            await visitor.close()


async def test_oversized_upload_reports_limit_bytes(
    tmp_path: Path, _desktop_media_dir: Path
) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True, maxUploadMb=1) as harness:
        ws = await connect_client(harness.url)
        try:
            await hello(ws)
            rejected = await _upload(ws, "u1", b"x" * (1024 * 1024 + 1))
            assert rejected["ok"] is False
            assert rejected["error"]["code"] == "too_large"
            assert rejected["error"]["details"] == {"limit_bytes": 1024 * 1024}
            assert list(_desktop_media_dir.iterdir()) == []

            accepted = await _upload(ws, "u2", b"x" * 1024)
            assert accepted["ok"] is True
            assert accepted["result"]["size"] == 1024
        finally:
            await ws.close()


async def test_audio_upload_transcribes_into_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fake_transcribe(path: Any) -> str:
        calls.append(str(path))
        return "hello from audio"

    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        monkeypatch.setattr(harness.channel, "transcribe_audio", fake_transcribe)
        ws = await connect_client(harness.url)
        try:
            await hello(ws)
            upload = await _upload(
                ws,
                "u1",
                b"OggS fake voice note",
                name="note.webm",
                mime="audio/webm",
                kind="audio",
            )
            sent = await request(
                ws,
                "c1",
                "chat.send",
                session_id=None,
                text="",
                attachments=[upload["result"]["attachment_id"]],
            )
            assert sent["ok"] is True

            echo = await next_event(ws, "message")
            assert echo["event"]["text"] == ""

            inbound = await asyncio.wait_for(harness.bus.consume_inbound(), 2)
            assert calls == [inbound.media[0]]
            assert Path(inbound.media[0]).suffix == ".webm"
            assert "[audio: note.webm]" in inbound.content
            assert "[transcription: hello from audio]" in inbound.content
        finally:
            await ws.close()


async def test_transcription_failure_does_not_break_send(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def broken_transcribe(path: Any) -> str:
        raise RuntimeError("no transcription provider")

    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        monkeypatch.setattr(harness.channel, "transcribe_audio", broken_transcribe)
        ws = await connect_client(harness.url)
        try:
            await hello(ws)
            upload = await _upload(
                ws, "u1", b"voice", name="note.ogg", mime="audio/ogg", kind="audio"
            )
            sent = await request(
                ws,
                "c1",
                "chat.send",
                session_id=None,
                text="",
                attachments=[upload["result"]["attachment_id"]],
            )
            assert sent["ok"] is True
            await next_event(ws, "message")

            inbound = await asyncio.wait_for(harness.bus.consume_inbound(), 2)
            assert inbound.content == "[audio: note.ogg]"
        finally:
            await ws.close()


async def test_consumed_attachment_cannot_be_reused(tmp_path: Path) -> None:
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws = await connect_client(harness.url)
        try:
            await hello(ws)
            upload = await _upload(ws, "u1", b"once")
            attachment_id = upload["result"]["attachment_id"]
            first = await request(
                ws,
                "c1",
                "chat.send",
                session_id=None,
                text="first",
                attachments=[attachment_id],
            )
            assert first["ok"] is True
            await next_event(ws, "message")
            await asyncio.wait_for(harness.bus.consume_inbound(), 2)

            second = await request(
                ws,
                "c2",
                "chat.send",
                session_id=None,
                text="again",
                attachments=[attachment_id],
            )
            assert second["ok"] is False
            assert second["error"]["code"] == "no_attachment"
            assert harness.bus.inbound_size == 0
            with pytest.raises(asyncio.TimeoutError):
                await next_frame(ws, timeout=0.2)
        finally:
            await ws.close()


async def test_expired_upload_is_pruned_with_its_file(
    tmp_path: Path, _desktop_media_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop_runtime, "UPLOAD_TTL_S", 0.0)
    async with desktop_channel(tmp_path, trustLocalhost=True) as harness:
        ws = await connect_client(harness.url)
        try:
            await hello(ws)
            upload = await _upload(ws, "u1", b"ttl")
            stored = next(iter(_desktop_media_dir.iterdir()))
            assert stored.is_file()

            denied = await request(
                ws,
                "c1",
                "chat.send",
                session_id=None,
                text="late",
                attachments=[upload["result"]["attachment_id"]],
            )
            assert denied["error"]["code"] == "no_attachment"
            assert not stored.exists()
        finally:
            await ws.close()
