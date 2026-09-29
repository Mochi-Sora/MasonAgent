"""Read and validate persisted conversations for the session tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypedDict, cast

from nanobot.runtime_context import public_history_message
from nanobot.session.history_visibility import is_hidden_history_message
from nanobot.session.manager import SessionManager

_VISIBLE_ROLES = {"user", "assistant"}


class SessionMessage(TypedDict):
    message_index: int
    role: str
    timestamp: str | int | None
    content: str


class SessionMatch(TypedDict):
    session_key: str
    title: str
    updated_at: str | None
    messages: list[SessionMessage]


def _message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for raw_block in cast(list[object], content):
        if not isinstance(raw_block, dict):
            continue
        block = cast(dict[object, object], raw_block)
        text = block.get("text")
        if block.get("type") == "text" and isinstance(text, str):
            parts.append(text)
    return "\n".join(parts).strip()


def _visible_messages(raw_messages: object) -> list[SessionMessage]:
    if not isinstance(raw_messages, list):
        return []
    visible: list[SessionMessage] = []
    for index, raw_message in enumerate(cast(list[object], raw_messages)):
        if not isinstance(raw_message, dict):
            continue
        message = cast(dict[str, Any], raw_message)
        role = message.get("role")
        if role not in _VISIBLE_ROLES or message.get("_command") or is_hidden_history_message(message):
            continue
        public = public_history_message(message)
        text = _message_text(public)
        if not text:
            continue
        timestamp = public.get("createdAt", public.get("timestamp"))
        visible.append({
            "message_index": index,
            "role": cast(str, role),
            "timestamp": timestamp if isinstance(timestamp, (str, int)) else None,
            "content": text,
        })
    return visible


def _text(value: object) -> str:
    return value.strip()[:160] if isinstance(value, str) else ""


def _session_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = cast(object, payload.get("metadata"))
    return cast(dict[str, Any], raw) if isinstance(raw, dict) else {}


def _row_title(row: Mapping[str, Any]) -> str:
    return _text(row.get("title")) or _text(row.get("preview"))


class SessionAccess:
    """Own listing, validation, and history reads for session references."""

    def __init__(self, sessions: SessionManager) -> None:
        self._sessions = sessions

    def _metadata(
        self,
        session_key: str,
        *,
        exclude_session_key: str | None,
    ) -> dict[str, Any] | None:
        if session_key == exclude_session_key:
            return None
        return self._sessions.read_session_metadata(session_key)

    def _messages(self, session_key: str, *, needle: str, limit: int) -> list[SessionMessage]:
        """Return the newest visible messages, oldest first, with absolute indexes."""
        payload = self._sessions.read_session_file(session_key)
        raw_messages = payload.get("messages") if payload is not None else None
        visible = _visible_messages(raw_messages)
        if needle:
            visible = [
                message for message in visible if needle in message["content"].casefold()
            ]
        return visible[-limit:] if limit > 0 else visible

    def search(
        self,
        query: str,
        limit: int,
        *,
        exclude_session_key: str | None = None,
    ) -> list[SessionMatch]:
        needle = query.casefold()
        rows: list[dict[str, Any]] = []
        for row in self._sessions.list_sessions():
            key = row.get("key")
            if isinstance(key, str) and key != exclude_session_key:
                rows.append(row)
        ranked: list[tuple[int, SessionMatch]] = []
        remaining: list[dict[str, Any]] = []
        for row in rows:
            title = _row_title(row)
            folded = title.casefold()
            rank = (
                0 if folded == needle
                else 1 if folded.startswith(needle)
                else 2 if needle in folded
                else None
            )
            if rank is None:
                remaining.append(row)
                continue
            updated = row.get("updated_at")
            ranked.append((rank, {
                "session_key": cast(str, row["key"]),
                "title": title,
                "updated_at": updated if isinstance(updated, str) else None,
                "messages": [],
            }))

        ranked.sort(key=lambda item: item[0])
        needed = max(0, limit - len(ranked))
        for row in remaining:
            if needed <= 0:
                break
            key = cast(str, row["key"])
            matches = self._messages(key, needle=needle, limit=2)
            if not matches:
                continue
            updated = row.get("updated_at")
            ranked.append((3, {
                "session_key": key,
                "title": _row_title(row),
                "updated_at": updated if isinstance(updated, str) else None,
                "messages": matches,
            }))
            needed -= 1
        return [item[1] for item in ranked[:limit]]

    def read(
        self,
        session_key: str,
        *,
        query: str,
        limit: int,
        exclude_session_key: str | None = None,
    ) -> SessionMatch | None:
        payload = self._metadata(session_key, exclude_session_key=exclude_session_key)
        if payload is None:
            return None
        messages = self._messages(session_key, needle=query.casefold(), limit=limit)
        updated = payload.get("updated_at")
        return {
            "session_key": session_key,
            "title": _text(_session_metadata(payload).get("title")),
            "updated_at": updated if isinstance(updated, str) else None,
            "messages": messages,
        }
