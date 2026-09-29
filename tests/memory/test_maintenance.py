"""Tests for the daily memory rollover."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from nanobot.memory.maintenance import maybe_rollover
from nanobot.memory.state import MemoryState
from nanobot.memory.store import MemoryDB


def test_rollover_clears_state_and_keeps_unconsolidated_backup(tmp_path: Path) -> None:
    db = MemoryDB(tmp_path / "memory" / "memory.db")
    state = MemoryState(tmp_path)
    state.write("active goal")
    db.capture_messages(
        "cli:1",
        [{"role": "user", "content": "yesterday", "timestamp": "2026-01-01T10:00:00"}],
    )
    assert db.insert_memory("durable fact") is not None

    assert maybe_rollover(db, state, now=datetime(2026, 1, 2, 12, 0)) is True
    assert state.read() == ""
    # Nothing has been consolidated yet, so the purge keeps the old backup.
    assert db.counts() == {"episodes": 1, "memories": 1, "edges": 0}
    # Same day: no-op.
    assert maybe_rollover(db, state, now=datetime(2026, 1, 2, 23, 59)) is False
    # Once consolidation has considered the episode, the next day's purge drops it.
    db.set_consolidation_cursor(1)
    assert maybe_rollover(db, state, now=datetime(2026, 1, 3, 0, 1)) is True
    assert db.counts() == {"episodes": 0, "memories": 1, "edges": 0}
    db.close()


def test_rollover_is_strictly_daily_without_consolidation(tmp_path: Path) -> None:
    db = MemoryDB(tmp_path / "memory" / "memory.db")
    state = MemoryState(tmp_path)
    db.capture_messages(
        "cli:1",
        [{"role": "user", "content": "old", "timestamp": "2026-01-01T10:00:00"}],
    )
    assert (
        maybe_rollover(db, state, consolidation_enabled=False, now=datetime(2026, 1, 2))
        is True
    )
    assert db.counts()["episodes"] == 0
    db.close()


def test_disabled_store_never_rolls_over(tmp_path: Path) -> None:
    db = MemoryDB(tmp_path / "memory" / "memory.db", enabled=False)
    state = MemoryState(tmp_path)
    state.write("keep me")
    assert maybe_rollover(db, state, now=datetime(2026, 1, 2, 12, 0)) is False
    assert state.read() == "keep me"
    db.close()
