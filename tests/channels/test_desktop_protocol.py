"""Pure helpers of the desktop channel: frames, formatting, and the ledger."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from mason.channels.desktop.devices import DeviceLedger
from mason.channels.desktop.protocol import (
    PROTOCOL_VERSION,
    diff_preview,
    duration_text,
    epoch_ms,
    error_response,
    event_frame,
    ok_response,
    optional_ms,
    optional_text,
    parse_request,
    preview_text,
    schedule_text,
    tool_summary,
    truncate_text,
)


def test_parse_request_accepts_valid_frames() -> None:
    frame = parse_request('{"v": 1, "id": "abc", "type": "hello", "device_token": null}')
    assert frame is not None
    assert frame.id == "abc"
    assert frame.type == "hello"
    assert frame.version == 1
    assert frame.body["device_token"] is None


def test_parse_request_accepts_numeric_ids() -> None:
    frame = parse_request('{"id": 7, "type": "ping"}')
    assert frame is not None
    assert frame.id == "7"


def test_parse_request_rejects_bad_input() -> None:
    assert parse_request("not json") is None
    assert parse_request("[1, 2]") is None
    assert parse_request('{"type": "hello"}') is None
    assert parse_request('{"id": "x"}') is None
    assert parse_request('{"id": "x", "type": ""}') is None
    assert parse_request('{"id": true, "type": "ping"}') is None


def test_response_and_event_frames_carry_the_version() -> None:
    ok = ok_response("1", {"a": 1})
    assert ok == {"v": PROTOCOL_VERSION, "id": "1", "ok": True, "result": {"a": 1}}
    empty = ok_response("1")
    assert empty["result"] == {}
    error = error_response("2", "unsupported", "nope")
    assert error["ok"] is False
    assert error["error"]["code"] == "unsupported"
    event = event_frame("delta", {"text_delta": "x"})
    assert event["type"] == "delta"
    assert event["event"]["text_delta"] == "x"


def test_epoch_ms_handles_seconds_milliseconds_and_iso() -> None:
    assert epoch_ms(1760000000.5) == 1760000000500
    assert epoch_ms(1760000000500) == 1760000000500
    local = datetime(2026, 10, 13, 11, 0)
    assert epoch_ms(local.isoformat()) == int(local.timestamp() * 1000)
    aware = datetime(2026, 10, 13, 11, 0, tzinfo=timezone.utc)
    assert epoch_ms(aware.isoformat()) == int(aware.timestamp() * 1000)
    assert epoch_ms(None, 7) == 7
    assert epoch_ms("nonsense", 7) == 7
    assert epoch_ms(True, 7) == 7
    assert epoch_ms(float("nan"), 7) == 7


def test_optional_helpers() -> None:
    assert optional_ms(1760000000000) == 1760000000000
    assert optional_ms("x") is None
    assert optional_ms(True) is None
    assert optional_text(" x ") == "x"
    assert optional_text("   ") is None
    assert optional_text(3) is None
    assert truncate_text("abcdef", 4) == "abc…"
    assert truncate_text("abc", 4) == "abc"


def test_duration_and_schedule_text() -> None:
    assert duration_text(45_000) == "45s"
    assert duration_text(90_000) == "1m 30s"
    assert duration_text(3_600_000) == "1h"
    assert duration_text(90_061_000) == "1d 1h"
    assert duration_text(0) == "?"
    assert schedule_text(SimpleNamespace(kind="cron", expr="0 3 * * *", tz=None)) == "0 3 * * *"
    assert (
        schedule_text(SimpleNamespace(kind="cron", expr="0 3 * * *", tz="UTC")) == "0 3 * * * (UTC)"
    )
    assert schedule_text(SimpleNamespace(kind="every", every_ms=1_800_000)) == "every 30m"
    assert schedule_text(SimpleNamespace(kind="at", at_ms=1760000000000)).startswith("at ")
    assert schedule_text(SimpleNamespace(kind="unknown")) == "unknown"


def test_previews() -> None:
    assert preview_text({"b": 1, "a": 2}) == '{"a": 2, "b": 1}'
    assert preview_text("  ") is None
    assert preview_text(None) is None
    assert len(preview_text("x" * 100, 10) or "") == 10
    assert tool_summary({"error": "boom"}) == "boom"
    assert tool_summary({"files": ["a", "b"]}) == "2 file(s)"
    assert tool_summary({}) is None
    preview = diff_preview({"diff": {"format": "unified", "text": "@@ -1 +1 @@\n-a\n+b"}})
    assert preview is not None and preview.startswith("@@")
    assert diff_preview({}) is None
    assert diff_preview({"diff": None}) is None


def test_device_ledger_roundtrip(tmp_path: Path) -> None:
    ledger = DeviceLedger(tmp_path / "devices.json")
    assert ledger.is_known("t1") is False
    ledger.record("t1")
    ledger.record("t1")
    assert DeviceLedger(tmp_path / "devices.json").is_known("t1") is True
    (tmp_path / "devices.json").write_text("{broken", encoding="utf-8")
    assert DeviceLedger(tmp_path / "devices.json").is_known("t1") is False
