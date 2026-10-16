"""Structured attachment records persisted with a user turn.

Channels that resolve uploads into local media paths can attach a structured
record to the inbound metadata (``attachments``); the loop persists it beside
the message so ``sessions.history`` can return thumbnails/players/chips after
a reload without exposing filesystem paths in user-visible text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, TypedDict, cast

ATTACHMENTS_METADATA_KEY = "attachments"

#: Server-only key holding the stored file path on a persisted record. It must
#: never reach a frame or a provider payload; every public projection drops it.
ATTACHMENT_PATH_KEY = "_path"

_ATTACHMENT_KINDS = frozenset({"image", "audio", "video", "file"})


class AttachmentRecord(TypedDict):
    """One persisted attachment, as surfaced to clients."""

    attachment_id: str
    name: str
    mime: str
    kind: str
    size: int
    duration_ms: int | None


class StoredAttachmentRecord(AttachmentRecord, total=False):
    """A record as persisted on the session message, with the private path."""

    _path: str


def normalize_attachment(value: object) -> StoredAttachmentRecord | None:
    """Project one raw attachment entry onto the persisted record shape.

    Unknown or malformed entries return ``None`` so a bad record degrades to
    "no attachment" instead of breaking history reads.  The server-only
    ``_path`` is preserved when present; public projections drop it.
    """
    if not isinstance(value, Mapping):
        return None
    entry = cast(Mapping[str, Any], value)
    attachment_id = entry.get("attachment_id")
    name = entry.get("name")
    mime = entry.get("mime")
    kind = entry.get("kind")
    size = entry.get("size")
    if not isinstance(attachment_id, str) or not attachment_id:
        return None
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(mime, str) or not mime:
        return None
    if kind not in _ATTACHMENT_KINDS:
        return None
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        return None
    duration_ms = entry.get("duration_ms")
    if not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 0:
        duration_ms = None
    record: StoredAttachmentRecord = {
        "attachment_id": attachment_id,
        "name": name,
        "mime": mime,
        "kind": cast(str, kind),
        "size": size,
        "duration_ms": duration_ms,
    }
    stored_path = entry.get(ATTACHMENT_PATH_KEY)
    if isinstance(stored_path, str) and stored_path:
        record[ATTACHMENT_PATH_KEY] = stored_path
    return record


def normalize_attachments(value: object) -> list[StoredAttachmentRecord]:
    """Normalize a raw attachments list, dropping malformed entries."""
    if not isinstance(value, list):
        return []
    records: list[StoredAttachmentRecord] = []
    for entry in cast(list[object], value):
        record = normalize_attachment(entry)
        if record is not None:
            records.append(record)
    return records


def public_attachment(record: Mapping[str, Any]) -> AttachmentRecord:
    """Drop server-only (underscore-prefixed) keys from one record."""
    return cast(AttachmentRecord, {
        key: value for key, value in record.items() if not key.startswith("_")
    })


def public_attachments(records: Iterable[Mapping[str, Any]]) -> list[AttachmentRecord]:
    """Project records for client frames: no paths, no internal keys."""
    return [public_attachment(record) for record in records]


def session_extra(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return persisted session kwargs for turn attachments."""
    if not isinstance(metadata, Mapping):
        return {}
    attachments = normalize_attachments(metadata.get(ATTACHMENTS_METADATA_KEY))
    return {ATTACHMENTS_METADATA_KEY: attachments} if attachments else {}
