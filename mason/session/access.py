"""Read and validate persisted conversations for the session tools."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypedDict, cast

from mason.runtime_context import public_history_message
from mason.session.history_visibility import (
    HIDDEN_HISTORY_CONTINUATION,
    hidden_history_kind,
    is_hidden_history_message,
)
from mason.session.manager import SessionInfo, SessionManager
from mason.utils.file_edit_events import (
    display_file_edit_path,
    is_file_edit_tool,
    resolve_file_edit_paths,
)

_VISIBLE_ROLES = {"user", "assistant"}
#: Framework tool errors reach the model as "Error: ..." payloads.
_ERROR_PREFIX = "error"


class HistoryTool(TypedDict):
    name: str
    ok: bool
    summary: str | None


class HistoryFileEdit(TypedDict):
    path: str
    ok: bool


class SessionMessage(TypedDict):
    message_index: int
    role: str
    timestamp: str | int | None
    content: str
    reasoning: list[str]
    tools: list[HistoryTool]
    file_edits: list[HistoryFileEdit]
    #: True when this assistant message continues the previous answer and was
    #: separated from it only by an internal recovery prompt (hidden history).
    continued: bool


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


def _tool_results_by_call(raw_messages: list[object]) -> dict[str, str]:
    """Index tool results by call id so assistant messages can pair with them."""
    results: dict[str, str] = {}
    for raw_message in raw_messages:
        if not isinstance(raw_message, dict):
            continue
        message = cast(dict[str, Any], raw_message)
        call_id = message.get("tool_call_id")
        content = message.get("content")
        if (
            message.get("role") == "tool"
            and isinstance(call_id, str)
            and isinstance(content, str)
        ):
            results[call_id] = content
    return results


def _reasoning_segments(message: Mapping[str, Any]) -> list[str]:
    reasoning = message.get("reasoning_content")
    if isinstance(reasoning, str) and reasoning.strip():
        return [reasoning.strip()]
    blocks = message.get("thinking_blocks")
    if not isinstance(blocks, list):
        return []
    segments: list[str] = []
    for raw_block in cast(list[object], blocks):
        if not isinstance(raw_block, dict):
            continue
        block = cast(dict[str, Any], raw_block)
        text = block.get("thinking") or block.get("text") or block.get("content")
        if isinstance(text, str) and text.strip():
            segments.append(text.strip())
    return segments


def _tool_call_arguments(raw_arguments: object) -> dict[str, Any] | None:
    if isinstance(raw_arguments, dict):
        return cast(dict[str, Any], raw_arguments)
    if not isinstance(raw_arguments, str) or not raw_arguments.strip():
        return None
    try:
        parsed: object = json.loads(raw_arguments)
    except (TypeError, ValueError):
        return None
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else None


def _tool_activity(
    message: Mapping[str, Any],
    tool_results: Mapping[str, str],
    workspace: Path | None,
) -> tuple[list[HistoryTool], list[HistoryFileEdit]]:
    """Pair an assistant message's tool calls with their persisted results."""
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return [], []
    tools: list[HistoryTool] = []
    file_edits: list[HistoryFileEdit] = []
    for raw_call in cast(list[object], raw_calls):
        if not isinstance(raw_call, dict):
            continue
        call = cast(dict[str, Any], raw_call)
        function = call.get("function")
        if not isinstance(function, dict):
            continue
        function_data = cast(dict[str, Any], function)
        name = function_data.get("name")
        if not isinstance(name, str) or not name:
            continue
        call_id = call.get("id")
        result = tool_results.get(call_id) if isinstance(call_id, str) else None
        ok = result is None or not result.lstrip().lower().startswith(_ERROR_PREFIX)
        summary: str | None = None
        if not ok and result:
            summary = result.replace("\n", " ").strip()[:120]
        else:
            parameters = _tool_call_arguments(function_data.get("arguments"))
            if is_file_edit_tool(name) and parameters is not None:
                for path in resolve_file_edit_paths(name, None, workspace, parameters):
                    file_edits.append({
                        "path": display_file_edit_path(path, workspace),
                        "ok": True,
                    })
        tools.append({"name": name, "ok": ok, "summary": summary})
    return tools, file_edits


def _visible_messages(
    raw_messages: object,
    *,
    workspace: Path | None = None,
) -> list[SessionMessage]:
    if not isinstance(raw_messages, list):
        return []
    typed_messages = cast(list[object], raw_messages)
    tool_results = _tool_results_by_call(typed_messages)
    visible: list[SessionMessage] = []
    continues_previous = False
    for index, raw_message in enumerate(typed_messages):
        if not isinstance(raw_message, dict):
            continues_previous = False
            continue
        message = cast(dict[str, Any], raw_message)
        role = message.get("role")
        if role not in _VISIBLE_ROLES or message.get("_command") or is_hidden_history_message(message):
            continues_previous = hidden_history_kind(message) == HIDDEN_HISTORY_CONTINUATION
            continue
        public = public_history_message(message)
        text = _message_text(public)
        reasoning = _reasoning_segments(public)
        tools, file_edits = _tool_activity(public, tool_results, workspace)
        if not text and not tools and not file_edits:
            continues_previous = False
            continue
        continued = continues_previous and role == "assistant"
        continues_previous = False
        timestamp = public.get("createdAt", public.get("timestamp"))
        visible.append({
            "message_index": index,
            "role": cast(str, role),
            "timestamp": timestamp if isinstance(timestamp, (str, int)) else None,
            "content": text,
            "reasoning": reasoning,
            "tools": tools,
            "file_edits": file_edits,
            "continued": continued,
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
        self._workspace = cast("Path | None", getattr(sessions, "workspace", None))

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
        visible = _visible_messages(raw_messages, workspace=self._workspace)
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
        rows: list[SessionInfo] = []
        for row in self._sessions.list_sessions():
            if row["key"] != exclude_session_key:
                rows.append(row)
        ranked: list[tuple[int, SessionMatch]] = []
        remaining: list[SessionInfo] = []
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
            ranked.append((rank, {
                "session_key": row["key"],
                "title": title,
                "updated_at": row["updated_at"],
                "messages": [],
            }))

        ranked.sort(key=lambda item: item[0])
        needed = max(0, limit - len(ranked))
        for row in remaining:
            if needed <= 0:
                break
            key = row["key"]
            matches = self._messages(key, needle=needle, limit=2)
            if not matches:
                continue
            ranked.append((3, {
                "session_key": key,
                "title": _row_title(row),
                "updated_at": row["updated_at"],
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
