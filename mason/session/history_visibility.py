"""Visibility helpers for persisted session history messages."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, cast

from mason.session.automation_turns import is_automation_history_message

HIDDEN_HISTORY_META = "_hidden_history"

#: Hidden marker kind for internal "continue the cut-off answer" prompts: the
#: next assistant message continues the previous answer and history marks it
#: ``continued`` so clients can fold the segments into one turn.
HIDDEN_HISTORY_CONTINUATION = "continuation"

#: Hidden marker kind for internal retry prompts (final-answer recovery,
#: exhausted budget); the following assistant message stands on its own.
HIDDEN_HISTORY_RETRY = "retry"

_VISIBLE_HISTORY_ROLES = frozenset({"user", "assistant"})


def hidden_history_marker(message: Mapping[str, Any] | None) -> Any:
    """Return the raw hidden marker (``True`` or a structured mapping)."""
    if not message:
        return None
    return message.get(HIDDEN_HISTORY_META)


def hidden_history_kind(message: Mapping[str, Any] | None) -> str | None:
    """Return the structured reason for a hidden marker, when recorded."""
    marker = hidden_history_marker(message)
    if not isinstance(marker, Mapping):
        return None
    marker_data = cast(Mapping[str, Any], marker)
    kind = marker_data.get("kind")
    return kind if isinstance(kind, str) else None


def _has_hidden_history_marker(message: Mapping[str, Any] | None) -> bool:
    marker = hidden_history_marker(message)
    return marker is True or isinstance(marker, Mapping)


def is_hidden_history_message(message: Mapping[str, Any] | None) -> bool:
    """True for persisted messages that should not be shown as chat turns."""
    return _has_hidden_history_marker(message) or is_automation_history_message(message)


def visible_history_message_count(messages: Iterable[Mapping[str, Any]]) -> int:
    """Count the transcript entries a client shows as chat turns.

    Matches the projection used by ``sessions.history``: user and assistant
    entries only, with internal markers (hidden history, automation turns) and
    command echoes excluded, so a session's advertised count never includes
    prompts the client cannot see.
    """
    count = 0
    for message in messages:
        if message.get("role") not in _VISIBLE_HISTORY_ROLES:
            continue
        if message.get("_command") or is_hidden_history_message(message):
            continue
        count += 1
    return count
