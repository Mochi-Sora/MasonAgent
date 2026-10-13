"""A revoked device is disconnected while idle, not on its next request.

Protocol v1: a token revoked while connected produces an ``unauthorized`` error
and a 4401 close. Without a periodic re-check an idle client would stay
connected until it happened to send a frame.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from desktop_support import connect_client, desktop_channel, hello, next_frame
from websockets.exceptions import ConnectionClosed

from mason.channels.desktop import runtime as desktop_runtime
from mason.pairing.store import approve_code, generate_code, revoke

_TOKEN = "device-idle-revocation"


async def test_revoked_device_is_closed_while_idle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(desktop_runtime, "REVALIDATE_INTERVAL_S", 0.05)
    approve_code(generate_code("desktop", _TOKEN))

    async with desktop_channel(tmp_path) as harness:
        ws = await connect_client(harness.url)
        try:
            result = await hello(ws, token=_TOKEN)
            assert result["result"]["paired"] is True

            assert revoke("desktop", _TOKEN) is True

            # No further client input: the server must notice by itself.
            frame = await next_frame(ws, timeout=5)
            assert frame.get("ok") is False
            assert frame["error"]["code"] == "unauthorized"

            with pytest.raises(ConnectionClosed) as closed:
                await asyncio.wait_for(ws.recv(), 5)
            assert closed.value.rcvd is not None
            assert closed.value.rcvd.code == 4401
        finally:
            await ws.close()


async def test_approved_device_stays_connected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-check must not disconnect a device that is still approved."""
    monkeypatch.setattr(desktop_runtime, "REVALIDATE_INTERVAL_S", 0.05)
    approve_code(generate_code("desktop", _TOKEN))

    async with desktop_channel(tmp_path) as harness:
        ws = await connect_client(harness.url)
        try:
            result = await hello(ws, token=_TOKEN)
            assert result["result"]["paired"] is True

            # Several re-check intervals pass without a close.
            await asyncio.sleep(0.3)
            assert ws.state.name == "OPEN"
        finally:
            await ws.close()
