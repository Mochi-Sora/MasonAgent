"""Tests for the SQLite memory store (backup tier + long-term tier)."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pytest

from nanobot.memory.store import MemoryDB, retention_cutoff
from nanobot.runtime_context import (
    RUNTIME_CONTEXT_HISTORY_META,
    RuntimeContextBlock,
    append_runtime_context,
)

_NOW = datetime(2026, 1, 2, 12, 0, 0)


def _msg(role: str, content: str, ts: str = "2026-01-02T10:00:00") -> dict[str, object]:
    return {"role": role, "content": content, "timestamp": ts}


@pytest.fixture
def db(tmp_path: Path):
    store = MemoryDB(tmp_path / "memory.db")
    yield store
    store.close()


def test_capture_is_idempotent(db: MemoryDB) -> None:
    messages = [_msg("user", "hello memory"), _msg("assistant", "noted")]
    assert db.capture_messages("cli:1", messages, now=_NOW) == 2
    assert db.capture_messages("cli:1", messages, now=_NOW) == 0
    assert db.counts()["episodes"] == 2


def test_capture_skips_tool_hidden_and_command_messages(db: MemoryDB) -> None:
    messages = [
        _msg("tool", "raw payload"),
        {"role": "user", "content": "echo", "_command": "/status", "timestamp": "t"},
        {"role": "user", "content": "hidden", "_hidden_history": True, "timestamp": "t"},
        _msg("user", "   "),
        _msg("user", "kept"),
    ]
    assert db.capture_messages("cli:1", messages, now=_NOW) == 1
    hits = db.recent_episodes()
    assert [hit.content for hit in hits] == ["kept"]


def test_capture_truncates_oversized_content(db: MemoryDB) -> None:
    assert db.capture_messages("cli:1", [_msg("user", "x" * 20_000)], now=_NOW) == 1
    assert len(db.recent_episodes()[0].content) <= 8_000


def test_search_matches_and_filters(db: MemoryDB) -> None:
    db.capture_messages("cli:1", [_msg("user", "banana bread recipe")], now=_NOW)
    db.capture_messages("cli:2", [_msg("user", "banana smoothie")], now=_NOW)
    assert len(db.search_episodes("banana")) == 2
    by_session = db.search_episodes("banana", session_key="cli:2")
    assert [hit.session_key for hit in by_session] == ["cli:2"]
    by_day = db.search_episodes("banana", day="2026-01-02")
    assert len(by_day) == 2
    assert db.search_episodes("banana", day="2025-12-31") == []
    assert db.search_episodes("banana", limit=1, offset=1)


def test_search_tolerates_hostile_query_syntax(db: MemoryDB) -> None:
    db.capture_messages("cli:1", [_msg("user", "quotes and stars")], now=_NOW)
    for query in ['"', "*", "AND", "NEAR(", 'a" OR "b', "()", "", "   "]:
        db.search_episodes(query)


def test_search_falls_back_to_substring_scan(db: MemoryDB) -> None:
    db.capture_messages("cli:1", [_msg("user", "中文记忆测试")], now=_NOW)
    # unicode61 treats the CJK run as one token; the LIKE fallback still finds it.
    hits = db.search_episodes("中文记忆")
    assert len(hits) == 1
    db._fts_enabled = False
    assert len(db.search_episodes("记忆")) == 1


def test_rollover_drops_previous_days_only(db: MemoryDB) -> None:
    db.capture_messages("cli:1", [_msg("user", "yesterday", "2026-01-01T09:00:00")], now=_NOW)
    db.capture_messages("cli:1", [_msg("user", "today", "2026-01-02T09:00:00")], now=_NOW)
    assert db.insert_memory("durable fact") is not None
    assert db.rollover("2026-01-02") == 1
    assert db.counts() == {"episodes": 1, "memories": 1, "edges": 0}
    assert db.search_episodes("yesterday") == []


def test_rollover_retention_window_keeps_trailing_days(db: MemoryDB) -> None:
    db.capture_messages("cli:1", [_msg("user", "day one", "2026-01-01T09:00:00")], now=_NOW)
    db.capture_messages("cli:1", [_msg("user", "day two", "2026-01-02T09:00:00")], now=_NOW)
    db.capture_messages("cli:1", [_msg("user", "day three", "2026-01-03T09:00:00")], now=_NOW)

    assert db.rollover("2026-01-03", retain_days=2) == 1
    remaining = sorted(hit.content for hit in db.recent_episodes(limit=10))
    assert remaining == ["day three", "day two"]


def test_retention_cutoff_counts_days_including_today() -> None:
    assert retention_cutoff("2026-01-03", 1) == "2026-01-03"
    assert retention_cutoff("2026-01-03", 2) == "2026-01-02"
    assert retention_cutoff("2026-01-03", 3) == "2026-01-01"
    assert retention_cutoff("2026-01-01", 2) == "2025-12-31"
    assert retention_cutoff("not-a-date", 2) == "not-a-date"


def test_memory_tier_search_link_and_dedupe(db: MemoryDB) -> None:
    active_id = db.insert_memory("user prefers dark mode", now=_NOW)
    candidate_id = db.insert_memory("maybe likes tea", status="candidate", now=_NOW)
    assert active_id is not None and candidate_id is not None
    assert db.insert_memory("user prefers dark mode", now=_NOW) is None
    active_hits = db.search_memories("dark mode")
    assert [hit.text for hit in active_hits] == ["user prefers dark mode"]
    assert db.search_memories("tea", include_candidates=False) == []
    assert db.search_memories("tea")[0].status == "candidate"
    db.link(
        src_kind="memory",
        src_id=active_id,
        dst_kind="memory",
        dst_id=candidate_id,
        rel="related",
        now=_NOW,
    )
    db.link(
        src_kind="memory",
        src_id=active_id,
        dst_kind="memory",
        dst_id=candidate_id,
        rel="related",
        weight=2.0,
        now=_NOW,
    )
    assert db.counts()["edges"] == 1


def test_meta_roundtrip(db: MemoryDB) -> None:
    assert db.get_meta("missing") is None
    db.set_meta("key", "value")
    assert db.get_meta("key") == "value"
    db.set_meta("key", "other")
    assert db.get_meta("key") == "other"


def test_disabled_store_is_inert(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = MemoryDB(path, enabled=False)
    assert store.capture_messages("cli:1", [_msg("user", "hello")]) == 0
    assert store.search_episodes("hello") == []
    assert store.insert_memory("memory") is None
    assert store.search_memories("memory") == []
    assert store.rollover("2026-01-02") == 0
    assert store.counts() == {"episodes": 0, "memories": 0, "edges": 0}
    assert not path.exists()
    store.close()


def test_retrieval_stays_fast(db: MemoryDB) -> None:
    """One recall must stay interactive even with a full backup tier and mature graph."""
    messages = [
        _msg("user", f"project alpha note {index} keyword zebra{index % 7}")
        for index in range(8_000)
    ]
    db.capture_messages("bulk", messages, now=_NOW)
    for index in range(400):
        db.insert_memory(f"consolidated fact {index} about topic magpie{index % 11}", now=_NOW)
    assert db.counts()["episodes"] == 8_000

    started = time.perf_counter()
    hits = db.search_episodes("zebra3 project alpha")
    episode_seconds = time.perf_counter() - started
    assert hits
    assert episode_seconds < 0.25, f"episode search took {episode_seconds:.3f}s"

    started = time.perf_counter()
    memory_hits = db.search_memories("magpie4 fact")
    memory_seconds = time.perf_counter() - started
    assert memory_hits
    assert memory_seconds < 0.25, f"memory search took {memory_seconds:.3f}s"

    # Worst case: nothing matches, so the bounded substring scan runs.
    started = time.perf_counter()
    assert db.search_episodes("zzz_no_such_term_zzz") == []
    scan_seconds = time.perf_counter() - started
    assert scan_seconds < 0.5, f"fallback scan took {scan_seconds:.3f}s"


def test_capture_keeps_conversation_text_only(db: MemoryDB) -> None:
    """Trusted runtime context is framework metadata, not a captured turn."""
    content, marker = append_runtime_context(
        "what did I say about the deploy?",
        [RuntimeContextBlock(source="memory", content="State check: update state now.")],
    )
    assert marker is not None

    db.capture_messages(
        "cli:1",
        [
            {
                "role": "user",
                "content": content,
                RUNTIME_CONTEXT_HISTORY_META: marker,
                "timestamp": "2026-01-02T10:00:00",
            }
        ],
        now=_NOW,
    )

    hits = db.recent_episodes()
    assert [hit.content for hit in hits] == ["what did I say about the deploy?"]


# -- durability: snapshots, checkpoints, health -------------------------------


def test_snapshot_captures_uncheckpointed_wal_and_roundtrips(db: MemoryDB) -> None:
    """A hand copy of memory.db would miss the WAL; VACUUM INTO must not."""
    db.capture_messages("cli:1", [_msg("user", "wal-only turn")], now=_NOW)
    assert db._wal_bytes() > 0  # committed, not yet folded into memory.db

    snapshot = db.snapshot(now=_NOW)
    assert snapshot is not None and snapshot.exists()
    copy = sqlite3.connect(snapshot)
    try:
        rows = copy.execute("SELECT role, content FROM episodes").fetchall()
    finally:
        copy.close()
    assert rows == [("user", "wal-only turn")]
    assert db.get_meta("last_snapshot_at") is not None


def test_snapshot_prunes_to_newest(db: MemoryDB) -> None:
    for day in range(1, 6):
        assert db.snapshot(keep=3, now=datetime(2026, 1, day)) is not None
    backups = db.path.parent / "backups"
    assert sorted(path.name for path in backups.glob("memory-*.db")) == [
        "memory-2026-01-03.db",
        "memory-2026-01-04.db",
        "memory-2026-01-05.db",
    ]


def test_checkpoint_truncates_wal(db: MemoryDB) -> None:
    db.capture_messages("cli:1", [_msg("user", "durable")], now=_NOW)
    assert db._wal_bytes() > 0
    assert db.checkpoint("TRUNCATE") is True
    assert db._wal_bytes() == 0
    assert db.get_meta("wal_checkpoint_at") is not None


def test_stats_reports_counts_sizes_and_pending(db: MemoryDB) -> None:
    db.capture_messages("cli:1", [_msg("user", "recent turn")], now=_NOW)
    db.insert_memory("durable fact", now=_NOW)

    stats = db.stats()
    assert stats["enabled"] is True
    assert stats["episodes"] == 1
    assert stats["memories"] == 1
    assert stats["edges"] == 0
    assert stats["pending_episodes"] == 1
    assert stats["consolidation_cursor"] == 0
    assert stats["consolidation_last_ok_at"] is None
    assert isinstance(stats["db_bytes"], int) and stats["db_bytes"] > 0
    assert isinstance(stats["wal_bytes"], int) and stats["wal_bytes"] >= 0

    db.set_consolidation_cursor(1)
    assert db.stats()["pending_episodes"] == 0
    assert db.stats()["consolidation_cursor"] == 1


def test_store_files_are_not_world_readable(db: MemoryDB) -> None:
    db.counts()  # force connect/create
    assert db.path.stat().st_mode & 0o777 == 0o600
    assert db.path.parent.stat().st_mode & 0o777 == 0o700


def test_snapshot_and_checkpoint_are_inert_when_disabled(tmp_path: Path) -> None:
    store = MemoryDB(tmp_path / "memory.db", enabled=False)
    assert store.snapshot() is None
    assert store.checkpoint("TRUNCATE") is False
    assert store.stats() == {
        "enabled": False,
        "episodes": 0,
        "memories": 0,
        "edges": 0,
        "pending_episodes": 0,
        "consolidation_cursor": 0,
        "consolidation_last_attempt_at": None,
        "consolidation_last_ok_at": None,
        "consolidation_last_error": None,
        "db_bytes": 0,
        "wal_bytes": 0,
        "last_checkpoint": None,
        "last_snapshot": None,
    }
    store.close()
