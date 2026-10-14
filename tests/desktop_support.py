"""Shared helpers for the desktop channel tests."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from websockets.asyncio.client import ClientConnection, connect

from mason.bus.queue import MessageBus
from mason.channels.desktop.runtime import DesktopChannel


class FakeSessionManager:
    """Duck-typed stand-in for the read-only SessionManager surface."""

    def __init__(
        self,
        sessions: dict[str, dict[str, Any]] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.sessions = sessions or {}
        self.rows = rows or []

    def list_sessions(self) -> list[dict[str, Any]]:
        return list(self.rows)

    def read_session_file(self, key: str) -> dict[str, Any] | None:
        return self.sessions.get(key)

    def read_session_metadata(self, key: str) -> dict[str, Any] | None:
        payload = self.sessions.get(key)
        if payload is None:
            return None
        metadata = payload.get("metadata", {})
        record = {"key": key, "metadata": metadata if isinstance(metadata, dict) else {}}
        for field in ("created_at", "updated_at", "message_count"):
            if field in payload:
                record[field] = payload[field]
        return record


def make_cron_service(jobs: list[Any]) -> Any:
    return SimpleNamespace(list_jobs=lambda include_disabled=True: list(jobs))


class DesktopHarness:
    """A running desktop channel plus its message bus."""

    def __init__(self, channel: DesktopChannel, bus: MessageBus, task: asyncio.Task[None]) -> None:
        self.channel = channel
        self.bus = bus
        self._task = task

    @property
    def url(self) -> str:
        port = self.channel.bound_port
        assert port is not None
        return f"ws://127.0.0.1:{port}"

    async def aclose(self) -> None:
        await self.channel.stop()
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(self._task, 5)


@asynccontextmanager
async def desktop_channel(tmp_path: Path, **overrides: Any) -> AsyncIterator[DesktopHarness]:
    """Start a desktop channel on an ephemeral port and stop it afterwards."""
    config: dict[str, Any] = {
        "enabled": True,
        "host": "127.0.0.1",
        "port": 0,
        "streaming": True,
        "trustLocalhost": False,
        "allowFrom": [],
    }
    config.update(overrides)
    bus = MessageBus()
    channel = DesktopChannel(config, bus, device_ledger_path=tmp_path / "desktop-devices.json")
    task = asyncio.create_task(channel.start())
    for _ in range(200):
        if channel.bound_port is not None:
            break
        await asyncio.sleep(0.01)
    assert channel.bound_port is not None, "desktop channel did not start"
    harness = DesktopHarness(channel, bus, task)
    try:
        yield harness
    finally:
        await harness.aclose()


async def connect_client(url: str) -> ClientConnection:
    return await connect(url, max_size=None)


async def next_frame(ws: ClientConnection, timeout: float = 5) -> dict[str, Any]:
    raw = await asyncio.wait_for(ws.recv(), timeout)
    assert isinstance(raw, str)
    return cast(dict[str, Any], json.loads(raw))


async def wait_response(ws: ClientConnection, frame_id: str, timeout: float = 5) -> dict[str, Any]:
    while True:
        frame = await next_frame(ws, timeout)
        if frame.get("id") == frame_id:
            return frame


async def request(
    ws: ClientConnection, frame_id: str, frame_type: str, **params: Any
) -> dict[str, Any]:
    await ws.send(json.dumps({"v": 1, "id": frame_id, "type": frame_type, **params}))
    return await wait_response(ws, frame_id)


async def next_event(
    ws: ClientConnection, event_type: str | None = None, timeout: float = 5
) -> dict[str, Any]:
    while True:
        frame = await next_frame(ws, timeout)
        if "id" in frame:
            continue  # a response the test did not explicitly wait for
        if event_type is None or frame.get("type") == event_type:
            return frame


async def hello(
    ws: ClientConnection, token: str | None = None, frame_id: str = "h1"
) -> dict[str, Any]:
    return await request(
        ws,
        frame_id,
        "hello",
        device_token=token,
        client={"name": "pytest", "version": "0"},
    )
