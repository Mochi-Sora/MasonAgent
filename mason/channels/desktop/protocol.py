"""Pure wire helpers for the desktop channel (protocol version 1).

The frame contract lives in the MasonDesk repository (docs/protocol.md); this
module implements the dependency-free parts: frame construction, request
parsing, and value formatting.  Keeping them free of I/O makes them directly
unit-testable.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

PROTOCOL_VERSION = 1

FRAME_MAX_BYTES = 1024 * 1024
PREVIEW_MAX_CHARS = 4096
ARGS_PREVIEW_MAX_CHARS = 512

ERROR_PROTOCOL_MISMATCH = "protocol_mismatch"
ERROR_UNAUTHORIZED = "unauthorized"
ERROR_NOT_PAIRED = "not_paired"
ERROR_BAD_REQUEST = "bad_request"
ERROR_UNSUPPORTED = "unsupported"
ERROR_BUSY = "busy"
ERROR_NO_SESSION = "no_session"
ERROR_NO_TURN = "no_turn"
ERROR_NO_APPROVAL = "no_approval"
ERROR_NO_ATTACHMENT = "no_attachment"
ERROR_MEDIA_GONE = "media_gone"
ERROR_TOO_LARGE = "too_large"
ERROR_RATE_LIMITED = "rate_limited"
ERROR_INTERNAL = "internal"

CLOSE_PROTOCOL_ERROR = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_HELLO_TIMEOUT = 4408

TRUSTED_LOOPBACK_DEVICE = "loopback"


@dataclass(frozen=True)
class RequestFrame:
    """One parsed client request; ``body`` is the raw frame object."""

    id: str
    type: str
    version: object
    body: dict[str, Any]


def parse_request(text: str) -> RequestFrame | None:
    """Parse a text frame; ``None`` when it is not a usable request."""
    try:
        raw: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    body = cast(dict[str, Any], raw)
    frame_id = body.get("id")
    frame_type = body.get("type")
    if isinstance(frame_id, bool) or not isinstance(frame_id, (str, int)):
        return None
    if not isinstance(frame_type, str) or not frame_type:
        return None
    return RequestFrame(id=str(frame_id), type=frame_type, version=body.get("v"), body=body)


def ok_response(request_id: str, result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build a success response frame."""
    return {
        "v": PROTOCOL_VERSION,
        "id": request_id,
        "ok": True,
        "result": dict(result) if result is not None else {},
    }


def error_response(
    request_id: str,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an error response frame."""
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = dict(details)
    return {"v": PROTOCOL_VERSION, "id": request_id, "ok": False, "error": error}


def event_frame(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Build a server push event frame."""
    return {"v": PROTOCOL_VERSION, "type": event_type, "event": dict(payload)}


def epoch_ms(value: object, fallback: int = 0) -> int:
    """Best-effort conversion of a framework timestamp to epoch milliseconds.

    Session timestamps are naive local ISO strings; numeric values are treated
    as seconds when they look small and as milliseconds otherwise.
    """
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return fallback
        return int(number * 1000) if abs(number) < 1e12 else int(number)
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip())
        except ValueError:
            return fallback
        return int(parsed.timestamp() * 1000)
    return fallback


def optional_ms(value: object) -> int | None:
    """Return an epoch-millisecond integer for cron-style ``*_ms`` fields."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return int(number)


def optional_text(value: object) -> str | None:
    """Return a stripped string, or ``None`` for blank/non-string values."""
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def truncate_text(text: str, limit: int) -> str:
    """Truncate to ``limit`` characters with a trailing ellipsis."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def preview_text(value: object, limit: int = ARGS_PREVIEW_MAX_CHARS) -> str | None:
    """A compact preview of arbitrary JSON-able data."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            text = str(value)
    if not text:
        return None
    return truncate_text(text, limit)


def duration_text(milliseconds: int | None) -> str:
    """Render a duration in milliseconds as a short human string."""
    if not milliseconds or milliseconds <= 0:
        return "?"
    seconds = milliseconds // 1000
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m" if seconds == 0 else f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h" if minutes == 0 else f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d" if hours == 0 else f"{days}d {hours}h"


def schedule_text(schedule: object) -> str:
    """Render a framework ``CronSchedule`` as display text."""
    kind = getattr(schedule, "kind", None)
    if kind == "at":
        at_ms = optional_ms(getattr(schedule, "at_ms", None))
        return f"at {_local_time_text(at_ms)}" if at_ms is not None else "at"
    if kind == "every":
        every_ms = optional_ms(getattr(schedule, "every_ms", None))
        return f"every {duration_text(every_ms)}" if every_ms else "every"
    if kind == "cron":
        text = optional_text(getattr(schedule, "expr", None)) or "cron"
        tz = optional_text(getattr(schedule, "tz", None))
        return f"{text} ({tz})" if tz else text
    return optional_text(kind) or "unknown"


def _local_time_text(milliseconds: int) -> str:
    return datetime.fromtimestamp(milliseconds / 1000).strftime("%Y-%m-%d %H:%M")


def tool_summary(payload: Mapping[str, Any]) -> str | None:
    """A one-line summary for a tool progress-event payload."""
    error = optional_text(payload.get("error"))
    if error:
        return truncate_text(error, 240)
    files = payload.get("files")
    if isinstance(files, list) and files:
        return f"{len(cast(list[object], files))} file(s)"
    return None


def diff_preview(edit: Mapping[str, Any]) -> str | None:
    """Extract a truncated unified diff from a file-edit event payload."""
    diff = edit.get("diff")
    if not isinstance(diff, dict):
        return None
    diff_map = cast(dict[str, Any], diff)
    text = diff_map.get("text")
    if isinstance(text, str) and text.strip():
        return truncate_text(text, PREVIEW_MAX_CHARS)
    lines = diff_map.get("lines")
    if isinstance(lines, list) and lines:
        joined = "\n".join(str(line) for line in cast(list[object], lines))
        if joined.strip():
            return truncate_text(joined, PREVIEW_MAX_CHARS)
    return None
