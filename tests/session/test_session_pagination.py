"""Cursor pagination for user-visible session listings."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from mason.session import SessionManager


def _manager(tmp_path: Path) -> SessionManager:
    return SessionManager(tmp_path / "workspace", sessions_root=tmp_path / "sessions")


def _save(manager: SessionManager, key: str, updated_at: str) -> None:
    session = manager.get_or_create(key)
    session.add_message("user", key)
    # add_message stamps "now"; page order is driven by the stored timestamp.
    session.updated_at = datetime.fromisoformat(updated_at)
    manager.save(session)


def test_pages_are_newest_first_and_exclude_maintenance(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _save(manager, "telegram:one", "2026-10-14T09:00:00")
    _save(manager, "desktop:two", "2026-10-14T10:00:00")
    _save(manager, "discord:three", "2026-10-14T11:00:00")
    _save(manager, "heartbeat", "2026-10-14T12:00:00")

    reloaded = _manager(tmp_path)
    first = reloaded.list_session_page(limit=2)
    assert [row["key"] for row in first.sessions] == ["discord:three", "desktop:two"]
    assert first.next_cursor == "desktop:two"

    second = reloaded.list_session_page(limit=2, cursor=first.next_cursor)
    assert [row["key"] for row in second.sessions] == ["telegram:one"]
    assert second.next_cursor is None


def test_channel_filter_deleted_cursor_and_unlimited(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _save(manager, "telegram:one", "2026-10-14T09:00:00")
    _save(manager, "desktop:two", "2026-10-14T10:00:00")

    reloaded = _manager(tmp_path)
    filtered = reloaded.list_session_page(channel="telegram")
    assert [row["key"] for row in filtered.sessions] == ["telegram:one"]

    assert reloaded.list_session_page(cursor="telegram:gone").sessions == []

    unlimited = reloaded.list_session_page()
    assert [row["key"] for row in unlimited.sessions] == ["desktop:two", "telegram:one"]
    assert unlimited.next_cursor is None


def test_list_sessions_returns_typed_rows_including_maintenance(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    _save(manager, "telegram:one", "2026-10-14T09:00:00")
    _save(manager, "heartbeat", "2026-10-14T10:00:00")

    rows = _manager(tmp_path).list_sessions()

    assert {row["key"] for row in rows} == {"telegram:one", "heartbeat"}
    assert all(isinstance(row["message_count"], int) for row in rows)
