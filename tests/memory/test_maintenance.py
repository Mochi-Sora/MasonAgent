"""Tests for the daily memory maintenance pass."""

from __future__ import annotations

import gzip
import sqlite3
from datetime import datetime
from pathlib import Path

from mason.memory.maintenance import maybe_rollover
from mason.memory.state import MemoryState
from mason.memory.store import MemoryDB


def test_rollover_keeps_the_session_state_and_verbatim_journal(tmp_path: Path) -> None:
    db = MemoryDB(tmp_path / "memory" / "memory.db")
    state = MemoryState(tmp_path, session_key="cli:1")
    state.write("active goal")
    db.capture_messages(
        "cli:1",
        [{"role": "user", "content": "yesterday", "timestamp": "2026-01-01T10:00:00"}],
    )
    assert db.insert_memory("durable fact") is not None

    assert maybe_rollover(db, state, now=datetime(2026, 1, 2, 12, 0)) is True
    assert state.read() == "active goal"
    # The verbatim journal is the source of truth and is never purged.
    assert db.counts() == {"episodes": 1, "memories": 1, "edges": 0}
    assert db.search_episodes("yesterday") != []
    # Same day: no-op.
    assert maybe_rollover(db, state, now=datetime(2026, 1, 2, 23, 59)) is False
    assert maybe_rollover(db, state, now=datetime(2026, 1, 3, 0, 1)) is True
    assert state.read() == "active goal"
    assert db.counts() == {"episodes": 1, "memories": 1, "edges": 0}
    db.close()


def test_disabled_store_never_rolls_over(tmp_path: Path) -> None:
    db = MemoryDB(tmp_path / "memory" / "memory.db", enabled=False)
    state = MemoryState(tmp_path, session_key="cli:1")
    state.write("keep me")
    assert maybe_rollover(db, state, now=datetime(2026, 1, 2, 12, 0)) is False
    assert state.read() == "keep me"
    db.close()


def test_rollover_writes_a_compressed_verbatim_backup(tmp_path: Path) -> None:
    db = MemoryDB(tmp_path / "memory" / "memory.db")
    state = MemoryState(tmp_path, session_key="cli:1")
    db.capture_messages(
        "cli:1",
        [{"role": "user", "content": "outgoing day", "timestamp": "2026-01-01T10:00:00"}],
    )

    assert maybe_rollover(db, state, now=datetime(2026, 1, 2, 0, 1)) is True

    backup = tmp_path / "backups" / "memory" / "memory-2026-01-02.db.gz"
    assert backup.exists()
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(backup.read_bytes()))
    copy = sqlite3.connect(restored)
    try:
        rows = copy.execute("SELECT content FROM episodes").fetchall()
    finally:
        copy.close()
    assert rows == [("outgoing day",)]
    db.close()


def test_rollover_can_skip_the_backup(tmp_path: Path) -> None:
    db = MemoryDB(tmp_path / "memory" / "memory.db")
    state = MemoryState(tmp_path, session_key="cli:1")
    db.capture_messages(
        "cli:1", [{"role": "user", "content": "today", "timestamp": "2026-01-02T09:00:00"}],
    )
    assert maybe_rollover(
        db, state, snapshot_enabled=False, now=datetime(2026, 1, 2, 12, 0),
    ) is True
    assert not (tmp_path / "backups").exists()
    db.close()


def test_explicit_admin_purge_is_not_part_of_rollover(tmp_path: Path) -> None:
    db = MemoryDB(tmp_path / "memory" / "memory.db")
    state = MemoryState(tmp_path, session_key="cli:1")
    db.capture_messages(
        "cli:1",
        [{"role": "user", "content": "ancient turn", "timestamp": "2026-01-01T09:00:00"}],
    )

    assert maybe_rollover(db, state, now=datetime(2026, 1, 3, 0, 1)) is True
    assert db.counts()["episodes"] == 1

    # The admin escape hatch still works when a user explicitly wants to forget.
    assert db.rollover("2026-01-03", retain_days=1) == 1
    assert db.counts()["episodes"] == 0
    db.close()
