"""Transcript counts persisted in the session metadata record.

Session listings and live ``session.updated`` pushes read the count from the
metadata record instead of loading each transcript.
"""

from __future__ import annotations

import json
from pathlib import Path

from mason.providers.base import ProviderConversationState
from mason.session import SessionManager
from mason.utils.runtime import build_length_recovery_message


def _manager(tmp_path: Path) -> SessionManager:
    return SessionManager(tmp_path / "workspace", sessions_root=tmp_path / "sessions")


def _provider_state() -> ProviderConversationState:
    return ProviderConversationState(
        kind="openai_responses",
        provider="openai:test",
        model="test-model",
        version=1,
        payload={"items": []},
    )


def test_save_persists_message_count_without_counting_provider_state(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = manager.get_or_create("cli:count")
    session.add_message("user", "one")
    session.add_message("assistant", "two")
    session.add_message("user", "three")
    session.provider_state = _provider_state()
    manager.save(session)

    first_line = json.loads(
        manager._get_session_path(session.key).read_text(encoding="utf-8").splitlines()[0]
    )
    assert first_line["message_count"] == 3

    reloaded = _manager(tmp_path)
    metadata = reloaded.read_session_metadata("cli:count")
    assert metadata is not None
    assert metadata["message_count"] == 3
    listing = [row for row in reloaded.list_sessions() if row["key"] == "cli:count"]
    assert [row["message_count"] for row in listing] == [3]


def test_stored_count_wins_over_the_transcript(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = manager.get_or_create("cli:drift")
    session.add_message("user", "one")
    manager.save(session)

    path = manager._get_session_path(session.key)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"role": "assistant", "content": "appended externally"}) + "\n")

    reloaded = _manager(tmp_path)
    metadata = reloaded.read_session_metadata("cli:drift")
    assert metadata is not None
    assert metadata["message_count"] == 1
    assert [row["message_count"] for row in reloaded.list_sessions()] == [1]


def test_message_count_excludes_hidden_history_entries(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    session = manager.get_or_create("cli:hidden")
    session.messages = [
        {"role": "user", "content": "write a very long answer"},
        {"role": "assistant", "content": "First segment."},
        build_length_recovery_message("First segment."),
        {"role": "assistant", "content": "Second segment."},
        {"role": "tool", "tool_call_id": "call_1", "name": "exec", "content": "ok"},
    ]
    manager.save(session)

    metadata = _manager(tmp_path).read_session_metadata("cli:hidden")

    assert metadata is not None
    assert metadata["message_count"] == 3


def test_legacy_records_without_a_count_fall_back_to_counting(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    path = manager._get_session_path("cli:legacy")
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "_type": "metadata",
            "key": "cli:legacy",
            "created_at": "2026-10-14T09:00:00",
            "updated_at": "2026-10-14T09:05:00",
            "metadata": {},
            "last_archived": 0,
            "last_consolidated": 0,
        },
        {"role": "user", "content": "old one"},
        {"role": "assistant", "content": "old two"},
    ]
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    metadata = manager.read_session_metadata("cli:legacy")
    assert metadata is not None
    assert metadata["message_count"] == 2
    assert [row["message_count"] for row in manager.list_sessions()] == [2]
