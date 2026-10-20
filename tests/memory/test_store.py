"""Tests for the SQLite single-tier memory store."""

from __future__ import annotations

import gzip
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from mason.memory.store import MemoryDB, retention_cutoff
from mason.runtime_context import (
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


def test_record_uses_increments_and_timestamps(db: MemoryDB) -> None:
    first = db.insert_memory("user prefers dark mode", now=_NOW)
    second = db.insert_memory("user is based in Berlin", now=_NOW)
    assert first is not None and second is not None
    later = datetime(2026, 1, 2, 13, 0, 0)

    assert db.record_uses([first, second], now=later) == 2

    hits = {hit.id: hit for hit in db.search_memories("user")}
    assert hits[first].uses == 1
    assert hits[first].last_used_at == later.isoformat()
    # Repeated serving keeps counting.
    db.record_uses([first], now=later)
    assert db.search_memories("dark mode")[0].uses == 2


def test_record_uses_ignores_unknown_and_empty_ids(db: MemoryDB) -> None:
    assert db.record_uses([]) == 0
    assert db.record_uses([999]) == 0
    assert db.counts()["memories"] == 0


def test_list_memories_carries_usage(db: MemoryDB) -> None:
    memory_id = db.insert_memory("durable fact")
    assert memory_id is not None
    db.record_uses([memory_id], now=_NOW)
    hit = next(entry for entry in db.list_memories() if entry.id == memory_id)
    assert hit.uses == 1
    assert hit.last_used_at == _NOW.isoformat()


def test_search_can_list_open_gaps(db: MemoryDB) -> None:
    gap = db.insert_memory("which timezone does the user deploy from?", kind="gap", status="open")
    db.insert_memory("user likes tea", now=_NOW)
    assert gap is not None

    hits = db.search_memories("", kind="gap")
    assert [hit.id for hit in hits] == [gap]
    assert hits[0].status == "open"
    assert db.search_memories("timezone")[0].kind == "gap"
    # A blank query without a kind must not become a match-everything scan.
    assert db.search_memories("") == []


def test_apply_plan_supports_synthesis_contradiction_gap_and_retention(
    db: MemoryDB,
) -> None:
    potatoes = db.insert_memory("user likes potatoes", now=_NOW)
    badminton = db.insert_memory("user likes badminton", now=_NOW)
    berlin = db.insert_memory("user is based in Berlin", now=_NOW)
    paris = db.insert_memory("user is based in Paris", now=_NOW)
    stale = db.insert_memory("temporary note", now=_NOW)
    existing_gap = db.insert_memory(
        "what is the user's deploy timezone?", kind="gap", status="open", now=_NOW
    )
    assert all(isinstance(value, int) for value in (potatoes, badminton, berlin, paris, stale))
    assert existing_gap is not None

    applied = db.apply_plan(
        {
            "syntheses": [
                {
                    "text": "user likes potatoes and badminton",
                    "kind": "summary",
                    "memories": [potatoes, badminton],
                    "archive_sources": True,
                }
            ],
            "contradictions": [
                {
                    "keep": berlin,
                    "contradicts": [paris],
                    "resolution": "supersede",
                }
            ],
            "gaps": [
                {"text": "does the user play badminton regularly?", "priority": "high"}
            ],
            "resolved_gaps": [
                {"id": existing_gap, "text": "user deploys from Europe/Berlin"}
            ],
            "retention": [{"id": stale, "action": "archive", "score": 0.1}],
        },
        now=_NOW,
    )

    assert applied["syntheses"] == 1
    assert applied["contradictions"] == 1
    assert applied["gaps"] == 1
    assert applied["resolved_gaps"] == 1
    assert applied["retention"] == 1
    assert [hit.text for hit in db.search_memories("potatoes and badminton")] == [
        "user likes potatoes and badminton"
    ]
    # Archive_sources on a full synthesis and supersede on a contradiction both
    # remove the now-redundant entries from recall.
    assert all(
        hit.text != "user likes potatoes" for hit in db.search_memories("potatoes")
    )
    assert all(hit.text != "user is based in Paris" for hit in db.search_memories("Paris"))
    assert all(hit.text != "temporary note" for hit in db.search_memories("temporary"))
    assert all(hit.id != existing_gap for hit in db.search_memories("", kind="gap"))
    open_gaps = db.search_memories("", kind="gap")
    assert [hit.text for hit in open_gaps] == ["does the user play badminton regularly?"]
    assert db.stats()["gaps_open"] == 1
    assert db.stats()["contradictions"] == 1


def test_duplicate_promotion_still_records_provenance(db: MemoryDB) -> None:
    db.capture_messages("cli:1", [_msg("user", "I like tea")], now=_NOW)
    existing = db.insert_memory("user likes tea", now=_NOW)
    assert existing is not None

    applied = db.apply_plan(
        {"promotions": [{"text": "user likes tea", "sources": [1]}]}, now=_NOW
    )

    assert applied["promotions"] == 0  # no duplicate row was created
    assert db.counts()["edges"] == 1  # the new turn is linked to the known fact


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


def test_snapshot_captures_uncheckpointed_wal_and_roundtrips(db: MemoryDB, tmp_path: Path) -> None:
    """A hand copy of memory.db would miss the WAL; the backup export must not."""
    db.capture_messages("cli:1", [_msg("user", "wal-only turn")], now=_NOW)
    assert db._wal_bytes() > 0  # committed, not yet folded into memory.db

    snapshot = db.snapshot(now=_NOW)
    assert snapshot is not None and snapshot.suffix == ".gz" and snapshot.exists()
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(snapshot.read_bytes()))
    copy = sqlite3.connect(restored)
    try:
        rows = copy.execute("SELECT role, content FROM episodes").fetchall()
    finally:
        copy.close()
    assert rows == [("user", "wal-only turn")]
    assert db.get_meta("last_snapshot_at") is not None


def test_backup_is_a_full_compressed_copy(db: MemoryDB, tmp_path: Path) -> None:
    db.capture_messages("cli:1", [_msg("user", "raw turn")], now=_NOW)
    db.insert_memory("structured fact", now=_NOW)

    snapshot = db.snapshot(now=_NOW)
    assert snapshot is not None
    assert snapshot.parent == db.backup_dir
    assert snapshot.parent != db.path.parent

    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(snapshot.read_bytes()))
    copy = sqlite3.connect(restored)
    try:
        tables = {
            row[0]
            for row in copy.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        indices = {
            row[0]
            for row in copy.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
        rows = copy.execute("SELECT content FROM episodes").fetchall()
        memories = copy.execute("SELECT text FROM memories").fetchall()
        kind = copy.execute("SELECT value FROM meta WHERE key = 'backup_kind'").fetchone()
    finally:
        copy.close()
    assert {"episodes", "memories", "edges"} <= tables
    assert {"episodes_day_idx", "episodes_session_idx"} <= indices
    assert rows == [("raw turn",)]
    assert memories == [("structured fact",)]
    assert kind == ("full",)


def test_snapshot_prunes_to_newest(db: MemoryDB) -> None:
    for day in range(1, 6):
        assert db.snapshot(keep=3, now=datetime(2026, 1, day)) is not None
    assert sorted(path.name for path in db.backup_dir.glob("memory-*.db.gz")) == [
        "memory-2026-01-03.db.gz",
        "memory-2026-01-04.db.gz",
        "memory-2026-01-05.db.gz",
    ]


def test_legacy_uncompressed_backups_are_migrated(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    legacy = memory_dir / "backups"
    legacy.mkdir(parents=True)
    payload = b"SQLite format 3\x00legacy-backup"
    source = legacy / "memory-2026-01-01.db"
    source.write_bytes(payload)

    store = MemoryDB(memory_dir / "memory.db")
    try:
        migrated = store.backup_dir / "memory-2026-01-01.db.gz"
        assert migrated.exists()
        assert gzip.decompress(migrated.read_bytes()) == payload
        assert not source.exists()
        assert store.stats()["backups"] == 1
    finally:
        store.close()


def test_explicit_backup_dir_does_not_migrate_legacy_files(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    legacy = memory_dir / "backups"
    legacy.mkdir(parents=True)
    source = legacy / "memory-2026-01-01.db"
    source.write_bytes(b"legacy")

    store = MemoryDB(memory_dir / "memory.db", backup_dir=tmp_path / "elsewhere")
    try:
        assert source.exists()
        assert not (tmp_path / "elsewhere" / "memory-2026-01-01.db.gz").exists()
    finally:
        store.close()


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
    assert stats["memory_uses_total"] == 0
    assert stats["memories_never_used"] == 1
    assert isinstance(stats["db_bytes"], int) and stats["db_bytes"] > 0
    assert isinstance(stats["wal_bytes"], int) and stats["wal_bytes"] >= 0

    db.set_consolidation_cursor(1)
    assert db.stats()["pending_episodes"] == 0
    assert db.stats()["consolidation_cursor"] == 1


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX permission bits; the memory subsystem is unsupported on Windows",
)
def test_store_files_are_not_world_readable(db: MemoryDB) -> None:
    db.counts()  # force connect/create
    assert db.path.stat().st_mode & 0o777 == 0o600
    assert db.path.parent.stat().st_mode & 0o777 == 0o700


def test_snapshot_and_checkpoint_are_inert_when_disabled(tmp_path: Path) -> None:
    store = MemoryDB(tmp_path / "memory.db", enabled=False)
    assert store.snapshot() is None
    assert store.checkpoint("TRUNCATE") is False
    stats = store.stats()
    assert stats["enabled"] is False
    assert stats["episodes"] == 0
    assert stats["memories"] == 0
    assert stats["edges"] == 0
    assert stats["pending_episodes"] == 0
    assert stats["memory_uses_total"] == 0
    assert stats["memories_never_used"] == 0
    assert stats["gaps_open"] == 0
    assert stats["contradictions"] == 0
    assert stats["consolidation_cursor"] == 0
    assert stats["consolidation_last_attempt_at"] is None
    assert stats["consolidation_last_ok_at"] is None
    assert stats["consolidation_last_error"] is None
    assert stats["db_bytes"] == 0
    assert stats["wal_bytes"] == 0
    assert stats["last_checkpoint"] is None
    assert stats["last_snapshot"] is None
    assert stats["backups"] == 0
    assert stats["backup_bytes"] == 0
    assert stats["backup_dir"] == str(store.backup_dir)
    assert store.record_uses([1, 2]) == 0
    store.close()
