"""SQLite-backed memory storage for mason.

One database file (``<workspace>/memory/memory.db``) is the single memory tier.
It holds two representations of the same memory, not two lifetimes:

* ``episodes`` — the verbatim journal. Every persisted turn is captured as-is
  (minus tool payloads), indexed with FTS5, and kept indefinitely. It is the
  source of truth that consolidation reads and that ``recall_backup`` searches.
* ``memories`` + ``edges`` — structured knowledge derived from the journal:
  facts, preferences, decisions, events, summaries, open knowledge gaps, and
  the relationships between them. Only the consolidation pass writes it.

Retrieval must stay interactive: the schema is created once (gated on
``PRAGMA user_version``), the connection is cached per process, every query is
index-backed and ``LIMIT``-bounded, and the ``LIKE`` fallback only runs when the
FTS index has nothing to offer (for example CJK queries). Standard-library
``sqlite3`` only — WAL plus a busy timeout keeps the single file safe to share
between the gateway, CLI, and SDK processes.

A separate compressed backup of the entire database is written to a sibling
``backups/memory`` directory (``memory.backupDir`` overrides it). It is a
lossless ``.db.gz`` copy taken with ``VACUUM INTO``, never a live copy, so a
corrupted page in the active file cannot take the backup with it.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import shutil
import sqlite3
import tempfile
import threading
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from loguru import logger

from mason.runtime_context import public_history_message
from mason.session.history_visibility import is_hidden_history_message
from mason.utils.helpers import (
    content_with_media_breadcrumbs,
    strip_think,
    truncate_text,
)

_SCHEMA_VERSION = 1
_EPISODE_MAX_CHARS = 8_000
# truncate_text() appends "\n... (truncated)"; budget for it so the cap holds exactly.
_TRUNCATION_SUFFIX_CHARS = len("\n... (truncated)")
_MAX_FTS_TOKENS = 24
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Durability tuning: keep the WAL bounded without waiting on SQLite's default
# 1000-page trigger, warn when it still grows, and keep compressed backups in a
# sibling directory so a corrupt active file never takes them with it.
_WAL_AUTOCHECKPOINT_PAGES = 256
_WAL_WARN_BYTES = 8 * 1024 * 1024
_JOURNAL_SIZE_LIMIT_BYTES = 4 * 1024 * 1024
_BACKUP_DIRNAME = "backups"
_BACKUP_KIND = "full"
_BACKUP_SUFFIX = ".db.gz"
_DEFAULT_SNAPSHOT_KEEP = 7
_WAL_CHECKPOINT_META_KEY = "wal_checkpoint_at"
_SNAPSHOT_META_KEY = "last_snapshot_at"
_CHECKPOINT_MODES = ("PASSIVE", "FULL", "RESTART", "TRUNCATE")

# Consolidation freshness, written by mason/memory/consolidation.py and read
# back by MemoryDB.stats() for the gateway health payload.
CONSOLIDATION_LAST_OK_META_KEY = "consolidation_last_ok_at"
CONSOLIDATION_LAST_ATTEMPT_META_KEY = "consolidation_last_attempt_at"
CONSOLIDATION_LAST_ERROR_META_KEY = "consolidation_last_error"

# Nightly maintenance is deliberately separate from consolidation: it only
# deduplicates and flags contradictions, and it never advances the cursor.
MAINTENANCE_LAST_OK_META_KEY = "maintenance_last_ok_at"
MAINTENANCE_LAST_ATTEMPT_META_KEY = "maintenance_last_attempt_at"
MAINTENANCE_LAST_ERROR_META_KEY = "maintenance_last_error"

_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    session_key TEXT NOT NULL,
    ts TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (session_key, content_hash)
);
CREATE INDEX IF NOT EXISTS episodes_day_idx ON episodes(day);
CREATE INDEX IF NOT EXISTS episodes_session_idx ON episodes(session_key, id);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'fact',
    status TEXT NOT NULL DEFAULT 'active',
    pinned INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0.5,
    source TEXT NOT NULL DEFAULT 'consolidation',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT,
    uses INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS memories_status_idx ON memories(status);
CREATE TABLE IF NOT EXISTS edges (
    src_kind TEXT NOT NULL,
    src_id INTEGER NOT NULL,
    dst_kind TEXT NOT NULL,
    dst_id INTEGER NOT NULL,
    rel TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    PRIMARY KEY (src_kind, src_id, dst_kind, dst_id, rel)
);
CREATE INDEX IF NOT EXISTS edges_dst_idx ON edges(dst_kind, dst_id);
"""

_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
    content,
    content='episodes',
    content_rowid='id',
    tokenize='unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    text,
    content='memories',
    content_rowid='id',
    tokenize='unicode61'
);
CREATE TRIGGER IF NOT EXISTS episodes_fts_ai AFTER INSERT ON episodes BEGIN
    INSERT INTO episodes_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS episodes_fts_ad AFTER DELETE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS episodes_fts_au AFTER UPDATE ON episodes BEGIN
    INSERT INTO episodes_fts(episodes_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO episodes_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_fts_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_fts_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS memories_fts_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO memories_fts(rowid, text) VALUES (new.id, new.text);
END;
"""

# The backup is a full, consistent copy of the active database. It contains the
# verbatim journal and the structured graph exactly as they were at the moment of
# the copy; the only additions are two metadata rows identifying the copy.


@dataclass(frozen=True, slots=True)
class EpisodeHit:
    """A single backup-tier record returned by search or recall."""

    id: int
    session_key: str
    day: str
    ts: str
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class MemoryHit:
    """A single curated long-term memory returned by search or recall."""

    id: int
    text: str
    kind: str
    status: str
    confidence: float
    pinned: bool
    uses: int = 0
    last_used_at: str | None = None


@dataclass(frozen=True, slots=True)
class MessageCapture:
    """A normalized message ready for storage in the backup tier."""

    role: str
    content: str
    ts: str
    day: str
    content_hash: str


def _hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8", errors="replace")).hexdigest()


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _float(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _row_text(row: sqlite3.Row, key: str) -> str:
    return _text(cast(object, row[key]))


def _row_int(row: sqlite3.Row, key: str) -> int:
    return _int(cast(object, row[key]))


def _row_float(row: sqlite3.Row, key: str) -> float:
    return _float(cast(object, row[key]))


def _memory_hit(row: sqlite3.Row) -> MemoryHit:
    return MemoryHit(
        id=_row_int(row, "id"),
        text=_row_text(row, "text"),
        kind=_row_text(row, "kind"),
        status=_row_text(row, "status"),
        confidence=_row_float(row, "confidence"),
        pinned=_row_int(row, "pinned") == 1,
        uses=_row_int(row, "uses"),
        last_used_at=_row_text(row, "last_used_at") or None,
    )


def _fetch_int(cursor: sqlite3.Cursor, default: int = 0) -> int:
    row = cursor.fetchone()
    if row is None:
        return default
    return _int(cast(object, row[0]))


def _fts_query(text: str, *, any_terms: bool) -> str | None:
    """Build a safe FTS5 query: every token is quoted, so raw input cannot inject syntax."""
    tokens = _TOKEN_RE.findall(text)[:_MAX_FTS_TOKENS]
    if not tokens:
        return None
    joiner = " OR " if any_terms else " "
    return joiner.join(f'"{token}"' for token in tokens)


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _timestamp_day(ts: str, fallback: str) -> str:
    candidate = ts[:10]
    if len(candidate) == 10 and candidate[4] == "-" and candidate[7] == "-":
        return candidate
    return fallback


def retention_cutoff(today: str, retain_days: int) -> str:
    """Return the oldest day to keep given a retention window in days.

    ``retain_days=1`` keeps only *today* (the old day-scoped behavior); higher
    values keep that many trailing days including today.
    """
    days = max(1, int(retain_days))
    try:
        anchor = datetime.strptime(today, "%Y-%m-%d")
    except ValueError:
        return today
    return (anchor - timedelta(days=days - 1)).strftime("%Y-%m-%d")


def _message_content_text(message: Mapping[str, Any]) -> str:
    """Flatten a persisted message body to text, including media breadcrumbs."""
    content = message.get("content")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for block in cast(list[object], content):
            if not isinstance(block, Mapping):
                continue
            block_map = cast(Mapping[str, Any], block)
            if block_map.get("type") == "text":
                parts.append(_text(block_map.get("text")))
        text = "\n".join(part for part in parts if part)
    breadcrumb = content_with_media_breadcrumbs(message.get("role"), text, message.get("media"))
    return breadcrumb if isinstance(breadcrumb, str) else text


def plan_entries(plan: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    """Return the list of plan entries under *key*, skipping anything malformed."""
    value = plan.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in cast(list[object], value) if isinstance(item, Mapping)]


def plan_str(item: Mapping[str, object], key: str) -> str:
    return _text(item.get(key)).strip()


def plan_int(item: Mapping[str, object], key: str) -> int | None:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def plan_float(item: Mapping[str, object], key: str, *, default: float) -> float:
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def plan_optional_float(item: Mapping[str, object], key: str) -> float | None:
    """Return the float when present and well-formed, otherwise None (no default)."""
    if key not in item:
        return None
    value = item.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def plan_bool(item: Mapping[str, object], key: str, *, default: bool) -> bool:
    value = item.get(key)
    return value if isinstance(value, bool) else default


def plan_int_list(item: Mapping[str, object], key: str) -> list[int]:
    value = item.get(key)
    if not isinstance(value, list):
        return []
    return [
        entry
        for entry in cast(list[object], value)
        if isinstance(entry, int) and not isinstance(entry, bool)
    ]


def plan_str_list(plan: Mapping[str, object], key: str) -> list[str]:
    value = plan.get(key)
    if not isinstance(value, list):
        return []
    return [entry.strip() for entry in cast(list[object], value) if isinstance(entry, str)]


def _insert_memory_row(
    connection: sqlite3.Connection,
    text: str,
    *,
    kind: str,
    status: str,
    confidence: float,
    source: str,
    pinned: bool,
    moment: str,
) -> int | None:
    """Insert one curated memory inside an open transaction; None when it exists."""
    cursor = connection.execute(
        "INSERT OR IGNORE INTO memories"
        " (text, kind, status, pinned, confidence, source, created_at, updated_at,"
        "  content_hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            text,
            kind,
            status,
            1 if pinned else 0,
            float(confidence),
            source,
            moment,
            moment,
            _hash(text),
        ),
    )
    if cursor.rowcount <= 0:
        return None
    return int(cursor.lastrowid or 0)


def _insert_edge(
    connection: sqlite3.Connection,
    *,
    src_kind: str,
    src_id: int,
    dst_kind: str,
    dst_id: int,
    rel: str,
    weight: float,
    moment: str,
) -> None:
    connection.execute(
        "INSERT INTO edges(src_kind, src_id, dst_kind, dst_id, rel, weight, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(src_kind, src_id, dst_kind, dst_id, rel)"
        " DO UPDATE SET weight = excluded.weight",
        (src_kind, src_id, dst_kind, dst_id, rel, float(weight), moment),
    )


def _memory_ids_exist(connection: sqlite3.Connection, ids: set[int]) -> bool:
    if not ids:
        return False
    placeholders = ", ".join("?" for _ in ids)
    row = connection.execute(
        f"SELECT COUNT(*) FROM memories WHERE id IN ({placeholders})",
        tuple(sorted(ids)),
    ).fetchone()
    return row is not None and _int(cast(object, row[0])) == len(ids)


def _existing_memory_id(connection: sqlite3.Connection, text: str) -> int | None:
    """Return the id of an entry with the same text, if one already exists."""
    row = connection.execute(
        "SELECT id FROM memories WHERE content_hash = ?", (_hash(text),)
    ).fetchone()
    return _int(cast(object, row[0])) if row is not None else None


def _update_memory_text(
    connection: sqlite3.Connection,
    memory_id: int,
    text: str,
    moment: str,
) -> bool:
    """Update a memory's text; returns False when it would collide with an existing entry."""
    try:
        connection.execute(
            "UPDATE memories SET text = ?, content_hash = ?, updated_at = ? WHERE id = ?",
            (text, _hash(text), moment, memory_id),
        )
    except sqlite3.IntegrityError:
        # The new text duplicates another row; leave this entry untouched.
        return False
    return True


class MemoryDB:
    """SQLite storage for the single memory tier and its compressed full backup.

    The connection is opened lazily and cached per process. All public methods
    are safe to call from any thread; they serialize on an internal lock.
    """

    def __init__(
        self,
        path: Path,
        *,
        enabled: bool = True,
        backup_dir: Path | None = None,
    ) -> None:
        self.path = path
        self.enabled = enabled
        self.backup_dir = backup_dir or self._default_backup_dir(path)
        if enabled and backup_dir is None:
            self._migrate_legacy_backups()
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._connection_pid: int | None = None
        self._fts_enabled = False

    @staticmethod
    def _default_backup_dir(path: Path) -> Path:
        """Keep backups outside ``memory/`` but inside the agent workspace."""
        parent = path.parent
        base = parent.parent if parent.name == "memory" else parent
        return base / _BACKUP_DIRNAME / "memory"

    def _migrate_legacy_backups(self) -> None:
        """Move pre-snapshot-layout ``memory/backups/*.db`` files to the current dir.

        Older installs kept uncompressed full copies beside the database. The
        current layout stores ``.db.gz`` files in ``<workspace>/backups/memory``;
        compressing and moving the old copies keeps backup health, retention,
        and pruning working across the upgrade.
        """
        parent = self.path.parent
        if parent.name != "memory":
            return
        legacy = parent / _BACKUP_DIRNAME
        if not legacy.is_dir() or legacy == self.backup_dir:
            return
        try:
            sources = sorted(legacy.glob("memory-*.db"))
        except OSError:
            return
        if not sources:
            return
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            with suppress(OSError):
                self.backup_dir.chmod(0o700)
        except OSError:
            logger.warning("Could not create memory backup directory {}", self.backup_dir)
            return
        for source in sources:
            target = self.backup_dir / f"{source.name}.gz"
            if target.exists():
                continue
            temp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
            try:
                with source.open("rb") as raw, gzip.open(temp, "wb") as compressed:
                    shutil.copyfileobj(raw, compressed)
                os.replace(temp, target)
                with suppress(OSError):
                    target.chmod(0o600)
            except OSError:
                logger.warning("Could not migrate legacy memory backup {}", source)
                with suppress(OSError):
                    temp.unlink()
                continue
            with suppress(OSError):
                source.unlink()
            logger.info("Migrated legacy memory backup {} -> {}", source, target)

    # -- connection management ------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        pid = os.getpid()
        if self._connection is not None and self._connection_pid == pid:
            return self._connection
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(f"PRAGMA wal_autocheckpoint = {_WAL_AUTOCHECKPOINT_PAGES}")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA cache_size = -8000")
        connection.execute("PRAGMA mmap_size = 67108864")
        connection.execute(f"PRAGMA journal_size_limit = {_JOURNAL_SIZE_LIMIT_BYTES}")
        if _fetch_int(connection.execute("PRAGMA user_version")) < _SCHEMA_VERSION:
            connection.executescript(_DDL)
            try:
                connection.executescript(_FTS_DDL)
            except sqlite3.OperationalError:
                # FTS5 missing from this SQLite build: searches use the LIKE fallback.
                logger.warning("SQLite FTS5 unavailable at {}; using LIKE fallback", self.path)
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._fts_enabled = (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'episodes_fts'"
            ).fetchone()
            is not None
        )
        self._connection = connection
        self._connection_pid = pid
        self._restrict_permissions()
        return connection

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                # A clean shutdown leaves one tidy file instead of a warm WAL.
                with suppress(sqlite3.Error):
                    self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._connection.close()
                self._connection = None
                self._connection_pid = None

    # -- durability ------------------------------------------------------------

    def _sidecar(self, suffix: str) -> Path:
        return Path(f"{self.path}{suffix}")

    def _file_bytes(self, path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    def _wal_bytes(self) -> int:
        return self._file_bytes(self._sidecar("-wal"))

    def _backup_files(self) -> list[Path]:
        try:
            return sorted(self.backup_dir.glob(f"memory-*{_BACKUP_SUFFIX}"))
        except OSError:
            return []

    def _backup_bytes(self) -> int:
        return sum(self._file_bytes(path) for path in self._backup_files())

    def _restrict_permissions(self) -> None:
        """Best-effort lock-down of the store and its WAL sidecars."""
        with suppress(OSError):
            self.path.parent.chmod(0o700)
        for candidate in (self.path, self._sidecar("-wal"), self._sidecar("-shm")):
            if candidate.exists():
                with suppress(OSError):
                    candidate.chmod(0o600)

    def checkpoint(self, mode: str = "PASSIVE") -> bool:
        """Fold the WAL back into ``memory.db``.

        ``mode`` is any SQLite checkpoint mode; ``TRUNCATE`` also shrinks the
        WAL to zero when no other reader blocks it. Returns False when the store
        is disabled or the checkpoint could not complete (a busy reader).
        """
        if not self.enabled:
            return False
        normalized = mode.upper()
        if normalized not in _CHECKPOINT_MODES:
            normalized = "PASSIVE"
        wal_bytes = self._wal_bytes()
        with self._lock:
            connection = self._connect()
            # Stamp the attempt first so the checkpoint itself flushes the row;
            # TRUNCATE then leaves a zero-length WAL behind.
            connection.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_WAL_CHECKPOINT_META_KEY, datetime.now().isoformat()),
            )
            try:
                row = connection.execute(f"PRAGMA wal_checkpoint({normalized})").fetchone()
            except sqlite3.Error:
                return False
        busy = row is not None and _int(cast(object, row[0])) != 0
        if wal_bytes >= _WAL_WARN_BYTES:
            logger.warning(
                "Memory WAL at {} reached {} bytes before checkpoint ({}); "
                "snapshots and the daily rollover keep it bounded.",
                self.path,
                wal_bytes,
                normalized,
            )
        return not busy

    def snapshot(
        self,
        dest: Path | None = None,
        *,
        keep: int = _DEFAULT_SNAPSHOT_KEEP,
        now: datetime | None = None,
    ) -> Path | None:
        """Write a compressed, lossless full copy of the database.

        ``VACUUM INTO`` captures committed WAL frames too, so the copy is a
        consistent snapshot rather than a hand-copied file that might be missing
        recent writes. The copy contains the verbatim journal and the structured
        graph; it is gzipped without losing anything. Backups older than *keep*
        are pruned. Returns the ``.db.gz`` path, or None when the store is
        disabled or the copy failed.
        """
        if not self.enabled:
            return None
        moment = now or datetime.now()
        directory = self.backup_dir
        target = dest or directory / f"memory-{moment.strftime('%Y-%m-%d')}{_BACKUP_SUFFIX}"
        target.parent.mkdir(parents=True, exist_ok=True)
        with suppress(OSError):
            target.parent.chmod(0o700)
        compressed = target.with_name(f"{target.name}.{os.getpid()}.tmp")

        with tempfile.TemporaryDirectory(prefix="mason-memory-backup-") as raw_dir:
            raw_path = Path(raw_dir) / "memory.db"
            with self._lock:
                connection = self._connect()
                try:
                    connection.execute("VACUUM INTO ?", (str(raw_path),))
                except sqlite3.Error:
                    logger.exception("Memory backup copy failed at {}", target)
                    return None

            try:
                copy = sqlite3.connect(raw_path)
                try:
                    copy.executemany(
                        "INSERT INTO meta(key, value) VALUES (?, ?)"
                        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (
                            ("backup_kind", _BACKUP_KIND),
                            ("backup_created_at", moment.isoformat()),
                        ),
                    )
                    copy.commit()
                finally:
                    copy.close()
                with raw_path.open("rb") as source, gzip.open(
                    compressed, "wb", compresslevel=6
                ) as sink:
                    shutil.copyfileobj(source, sink)
                compressed.replace(target)
            except (OSError, sqlite3.Error):
                logger.exception("Memory backup compression failed at {}", target)
                return None
            finally:
                with suppress(OSError):
                    compressed.unlink()

        self.set_meta(_SNAPSHOT_META_KEY, moment.isoformat())
        with suppress(OSError):
            target.chmod(0o600)
        if dest is None:
            self._prune_snapshots(directory, keep=max(1, int(keep)))
        logger.info("Memory backup written to {}", target)
        return target

    @staticmethod
    def _prune_snapshots(directory: Path, *, keep: int) -> None:
        try:
            # Names are ISO dates (``memory-YYYY-MM-DD.db.gz``), so a reverse
            # lexicographic sort is chronological and tie-free.
            candidates = sorted(
                (
                    path.name
                    for path in directory.glob(f"memory-*{_BACKUP_SUFFIX}")
                    if path.is_file()
                ),
                reverse=True,
            )
        except OSError:
            return
        for stale in candidates[keep:]:
            with suppress(OSError):
                (directory / stale).unlink()

    def optimize(self) -> None:
        """Run SQLite's own maintenance after a write-heavy pass.

        ``PRAGMA optimize`` refreshes statistics only where they will help;
        FTS5 gets its own merge pass so a growing journal keeps fast lookups.
        The final checkpoint folds the WAL back into the active file.
        """
        if not self.enabled:
            return
        with self._lock:
            connection = self._connect()
            with suppress(sqlite3.Error):
                connection.execute("PRAGMA optimize")
            if self._fts_enabled:
                for table in ("episodes_fts", "memories_fts"):
                    with suppress(sqlite3.Error):
                        connection.execute(f"INSERT INTO {table}({table}) VALUES('optimize')")
        self.checkpoint("TRUNCATE")

    @contextmanager
    def _write(self) -> Generator[sqlite3.Connection]:
        """One serialized write transaction; commits on success, rolls back on error."""
        with self._lock:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")

    # -- metadata --------------------------------------------------------------

    def get_meta(self, key: str) -> str | None:
        if not self.enabled:
            return None
        with self._lock:
            row = self._connect().execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else _row_text(row, "value")

    def set_meta(self, key: str, value: str) -> None:
        if not self.enabled:
            return
        with self._write() as connection:
            connection.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    CONSOLIDATION_CURSOR_KEY = "consolidation_cursor"

    def consolidation_cursor(self) -> int:
        """Highest backup episode id the consolidation job has considered."""
        raw = self.get_meta(self.CONSOLIDATION_CURSOR_KEY)
        return int(raw) if raw is not None and raw.isdigit() else 0

    def set_consolidation_cursor(self, episode_id: int) -> None:
        self.set_meta(self.CONSOLIDATION_CURSOR_KEY, str(max(0, int(episode_id))))

    # -- backup tier: capture --------------------------------------------------

    @staticmethod
    def normalize_message(
        message: Mapping[str, Any],
        *,
        default_day: str,
        default_ts: str,
    ) -> MessageCapture | None:
        """Normalize one session message for the backup tier.

        Returns ``None`` for messages that must not enter the backup: tool
        payloads, hidden runtime/checkpoint messages, command echoes, and empty
        content. Media is reduced to a textual breadcrumb.
        """
        role = message.get("role")
        if role not in {"user", "assistant"}:
            return None
        if message.get("_command") or is_hidden_history_message(message):
            return None
        # Trusted runtime context (goal guidance, state-check notes, attachment
        # breadcrumbs) is framework metadata, not conversation: keep it out of
        # the backup so consolidation never promotes it.
        public = public_history_message(message)
        text = strip_think(_message_content_text(public)).strip()
        if not text:
            return None
        if len(text) > _EPISODE_MAX_CHARS:
            text = truncate_text(text, _EPISODE_MAX_CHARS - _TRUNCATION_SUFFIX_CHARS)
        ts_value = message.get("timestamp")
        ts = ts_value if isinstance(ts_value, str) and ts_value else default_ts
        return MessageCapture(
            role=cast(str, role),
            content=text,
            ts=ts,
            day=_timestamp_day(ts, default_day),
            content_hash=_hash(cast(str, role), text),
        )

    def capture_messages(
        self,
        session_key: str,
        messages: Sequence[Mapping[str, Any]],
        *,
        now: datetime | None = None,
    ) -> int:
        """Deterministically capture persisted messages; returns the inserted count.

        Idempotent on (session, role, content): replays and retries cannot
        duplicate rows, so two identical messages in one session collapse into
        their first occurrence. The backup prioritizes replay-safety.
        """
        if not self.enabled:
            return 0
        moment = now or datetime.now()
        default_ts = moment.isoformat()
        default_day = moment.strftime("%Y-%m-%d")
        captures: list[MessageCapture] = []
        for message in messages:
            capture = self.normalize_message(
                message,
                default_day=default_day,
                default_ts=default_ts,
            )
            if capture is not None:
                captures.append(capture)
        if not captures:
            return 0
        created_at = moment.isoformat()
        inserted = 0
        with self._write() as connection:
            for capture in captures:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO episodes"
                    " (day, session_key, ts, role, content, content_hash, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        capture.day,
                        session_key,
                        capture.ts,
                        capture.role,
                        capture.content,
                        capture.content_hash,
                        created_at,
                    ),
                )
                inserted += max(0, cursor.rowcount)
        return inserted

    # -- backup tier: search and recall ---------------------------------------

    def search_episodes(
        self,
        query: str,
        *,
        session_key: str | None = None,
        day: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> list[EpisodeHit]:
        """Keyword search over captured turns, best match first."""
        if not self.enabled:
            return []
        cleaned = query.strip()
        if not cleaned:
            # A blank query must never degrade into a match-everything scan.
            return []
        bounded = max(1, min(int(limit), 50))
        with self._lock:
            connection = self._connect()
            rows = self._search_episodes_fts(
                connection, cleaned, session_key, day, bounded, offset,
            )
            if not rows:
                # No indexed match (or no FTS5): one bounded substring scan as a last resort.
                rows = self._search_episodes_like(
                    connection, cleaned, session_key, day, bounded, offset,
                )
        return [
            EpisodeHit(
                id=_row_int(row, "id"),
                session_key=_row_text(row, "session_key"),
                day=_row_text(row, "day"),
                ts=_row_text(row, "ts"),
                role=_row_text(row, "role"),
                content=_row_text(row, "content"),
            )
            for row in rows
        ]

    def _search_episodes_fts(
        self,
        connection: sqlite3.Connection,
        query: str,
        session_key: str | None,
        day: str | None,
        limit: int,
        offset: int,
    ) -> list[sqlite3.Row] | None:
        """Return indexed matches, ``[]`` when the index has none, or None to fall back."""
        if not self._fts_enabled:
            return None
        clauses: list[str] = []
        params: list[object] = []
        if session_key:
            clauses.append("e.session_key = ?")
            params.append(session_key)
        if day:
            clauses.append("e.day = ?")
            params.append(day)
        sql = (
            "SELECT e.id, e.session_key, e.day, e.ts, e.role, e.content"
            " FROM episodes_fts f JOIN episodes e ON e.id = f.rowid"
            " WHERE episodes_fts MATCH ?"
            + "".join(f" AND {clause}" for clause in clauses)
            + f" ORDER BY bm25(episodes_fts), e.id DESC LIMIT {limit}"
            + f" OFFSET {max(0, int(offset))}"
        )
        for any_terms in (False, True):
            fts_query = _fts_query(query, any_terms=any_terms)
            if fts_query is None:
                return None
            try:
                rows = connection.execute(sql, (fts_query, *params)).fetchall()
            except sqlite3.OperationalError:
                return None
            if rows:
                return rows
        return []

    def _search_episodes_like(
        self,
        connection: sqlite3.Connection,
        query: str,
        session_key: str | None,
        day: str | None,
        limit: int,
        offset: int,
    ) -> list[sqlite3.Row]:
        sql = (
            "SELECT id, session_key, day, ts, role, content FROM episodes"
            " WHERE content LIKE ? ESCAPE '\\'"
        )
        params: list[object] = [_like_pattern(query)]
        if session_key:
            sql += " AND session_key = ?"
            params.append(session_key)
        if day:
            sql += " AND day = ?"
            params.append(day)
        sql += f" ORDER BY id DESC LIMIT {limit} OFFSET {max(0, int(offset))}"
        return connection.execute(sql, params).fetchall()

    def recent_episodes(
        self,
        *,
        session_key: str | None = None,
        day: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[EpisodeHit]:
        if not self.enabled:
            return []
        bounded = max(1, min(int(limit), 500))
        sql = "SELECT id, session_key, day, ts, role, content FROM episodes"
        clauses: list[str] = []
        params: list[object] = []
        if session_key:
            clauses.append("session_key = ?")
            params.append(session_key)
        if day:
            clauses.append("day = ?")
            params.append(day)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY id DESC LIMIT {bounded} OFFSET {max(0, int(offset))}"
        with self._lock:
            rows = self._connect().execute(sql, params).fetchall()
        return [
            EpisodeHit(
                id=_row_int(row, "id"),
                session_key=_row_text(row, "session_key"),
                day=_row_text(row, "day"),
                ts=_row_text(row, "ts"),
                role=_row_text(row, "role"),
                content=_row_text(row, "content"),
            )
            for row in rows
        ]

    def episodes_after(self, cursor: int, *, limit: int = 40) -> list[EpisodeHit]:
        """Oldest-first batch of backup episodes recorded after *cursor*.

        Oldest-first keeps the consolidation cursor watertight: batches advance
        it past exactly the episodes that were considered, never skipping any.
        """
        if not self.enabled:
            return []
        bounded = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._connect().execute(
                "SELECT id, session_key, day, ts, role, content FROM episodes"
                " WHERE id > ? ORDER BY id ASC LIMIT ?",
                (max(0, int(cursor)), bounded),
            ).fetchall()
        return [
            EpisodeHit(
                id=_row_int(row, "id"),
                session_key=_row_text(row, "session_key"),
                day=_row_text(row, "day"),
                ts=_row_text(row, "ts"),
                role=_row_text(row, "role"),
                content=_row_text(row, "content"),
            )
            for row in rows
        ]

    def list_memories(
        self,
        *,
        statuses: Sequence[str] = ("active", "candidate", "open"),
        limit: int = 200,
    ) -> list[MemoryHit]:
        """Memories for consolidation review, highest-signal first.

        Pinned entries, recently used entries, and recent entries come first so
        a mature store can never starve the newest memories behind its oldest
        ones.
        """
        if not self.enabled or not statuses:
            return []
        bounded = max(1, min(int(limit), 1_000))
        placeholders = ", ".join("?" for _ in statuses)
        with self._lock:
            rows = self._connect().execute(
                "SELECT id, text, kind, status, confidence, pinned, uses, last_used_at"
                " FROM memories"
                f" WHERE status IN ({placeholders})"
                " ORDER BY pinned DESC, COALESCE(last_used_at, '') DESC, id DESC"
                f" LIMIT {bounded}",
                tuple(statuses),
            ).fetchall()
        return [_memory_hit(row) for row in rows]

    # -- long-term tier --------------------------------------------------------

    def insert_memory(
        self,
        text: str,
        *,
        kind: str = "fact",
        status: str = "active",
        confidence: float = 0.5,
        source: str = "consolidation",
        pinned: bool = False,
        now: datetime | None = None,
    ) -> int | None:
        """Insert a curated memory; returns its id, or None when it already exists."""
        if not self.enabled:
            return None
        cleaned = text.strip()
        if not cleaned:
            return None
        moment = (now or datetime.now()).isoformat()
        with self._write() as connection:
            return _insert_memory_row(
                connection,
                cleaned,
                kind=kind,
                status=status,
                confidence=confidence,
                source=source,
                pinned=pinned,
                moment=moment,
            )

    def record_uses(
        self,
        memory_ids: Sequence[int],
        *,
        now: datetime | None = None,
    ) -> int:
        """Record that these memories were served by a recall; returns rows touched.

        A hit is only a proxy — the model may still ignore what it is shown — but
        it is the only signal that lets consolidation learn which entries earn
        their keep and which never get used.
        """
        if not self.enabled:
            return 0
        ids = sorted({int(memory_id) for memory_id in memory_ids if int(memory_id) > 0})
        if not ids:
            return 0
        moment = (now or datetime.now()).isoformat()
        placeholders = ", ".join("?" for _ in ids)
        with self._write() as connection:
            cursor = connection.execute(
                "UPDATE memories SET uses = uses + 1, last_used_at = ?"
                f" WHERE id IN ({placeholders})",
                (moment, *ids),
            )
            return max(0, cursor.rowcount)

    def search_memories(
        self,
        query: str,
        *,
        limit: int = 10,
        include_candidates: bool = True,
        kind: str | None = None,
    ) -> list[MemoryHit]:
        """Keyword search over structured memory.

        An empty query is only valid with *kind* (for example ``kind="gap"``),
        which lists that kind instead of degrading into a match-everything scan.
        """
        if not self.enabled:
            return []
        cleaned = query.strip()
        kind_filter = kind.strip() if kind else None
        if not cleaned and not kind_filter:
            return []
        bounded = max(1, min(int(limit), 50))
        statuses = ("active", "candidate", "open") if include_candidates else ("active", "open")
        placeholders = ", ".join("?" for _ in statuses)
        with self._lock:
            connection = self._connect()
            if not cleaned:
                rows = connection.execute(
                    "SELECT id, text, kind, status, confidence, pinned, uses, last_used_at"
                    " FROM memories"
                    f" WHERE kind = ? AND status IN ({placeholders})"
                    f" ORDER BY id DESC LIMIT {bounded}",
                    (kind_filter, *statuses),
                ).fetchall()
                return [_memory_hit(row) for row in rows]
            rows = self._search_memories_fts(
                connection, cleaned, statuses, placeholders, bounded, kind_filter,
            )
            if not rows:
                rows = self._search_memories_like(
                    connection, cleaned, statuses, placeholders, bounded, kind_filter,
                )
        return [_memory_hit(row) for row in rows]

    def _search_memories_fts(
        self,
        connection: sqlite3.Connection,
        query: str,
        statuses: tuple[str, ...],
        placeholders: str,
        limit: int,
        kind: str | None = None,
    ) -> list[sqlite3.Row] | None:
        if not self._fts_enabled:
            return None
        sql = (
            "SELECT m.id, m.text, m.kind, m.status, m.confidence, m.pinned,"
            " m.uses, m.last_used_at"
            " FROM memories_fts f JOIN memories m ON m.id = f.rowid"
            f" WHERE memories_fts MATCH ? AND m.status IN ({placeholders})"
        )
        params: list[object] = [*statuses]
        if kind:
            sql += " AND m.kind = ?"
            params.append(kind)
        sql += f" ORDER BY bm25(memories_fts), m.id DESC LIMIT {limit}"
        for any_terms in (False, True):
            fts_query = _fts_query(query, any_terms=any_terms)
            if fts_query is None:
                return None
            try:
                rows = connection.execute(sql, (fts_query, *params)).fetchall()
            except sqlite3.OperationalError:
                return None
            if rows:
                return rows
        return []

    def _search_memories_like(
        self,
        connection: sqlite3.Connection,
        query: str,
        statuses: tuple[str, ...],
        placeholders: str,
        limit: int,
        kind: str | None = None,
    ) -> list[sqlite3.Row]:
        sql = (
            "SELECT id, text, kind, status, confidence, pinned, uses, last_used_at"
            " FROM memories"
            f" WHERE text LIKE ? ESCAPE '\\' AND status IN ({placeholders})"
        )
        params: list[object] = [_like_pattern(query), *statuses]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += f" ORDER BY id DESC LIMIT {limit}"
        return connection.execute(sql, params).fetchall()

    def link(
        self,
        *,
        src_kind: str,
        src_id: int,
        dst_kind: str,
        dst_id: int,
        rel: str,
        weight: float = 1.0,
        now: datetime | None = None,
    ) -> None:
        """Add (or strengthen) a graph edge between two stored records."""
        if not self.enabled:
            return
        created_at = (now or datetime.now()).isoformat()
        with self._write() as connection:
            _insert_edge(
                connection,
                src_kind=src_kind,
                src_id=src_id,
                dst_kind=dst_kind,
                dst_id=dst_id,
                rel=rel,
                weight=weight,
                moment=created_at,
            )

    def apply_plan(
        self,
        plan: Mapping[str, object],
        *,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Apply a consolidation plan atomically; returns per-action applied counts.

        Unknown keys, malformed entries, and references to missing rows are
        skipped rather than failing the whole plan.
        """
        counts = {
            "promotions": 0,
            "merges": 0,
            "syntheses": 0,
            "contradictions": 0,
            "rewrites": 0,
            "links": 0,
            "gaps": 0,
            "resolved_gaps": 0,
            "retention": 0,
            "drops": 0,
        }
        if not self.enabled:
            return counts
        moment = (now or datetime.now()).isoformat()
        with self._write() as connection:
            for item in plan_entries(plan, "promotions"):
                text = plan_str(item, "text")
                if not text:
                    continue
                memory_id = _insert_memory_row(
                    connection,
                    text,
                    kind=plan_str(item, "kind") or "fact",
                    status="active",
                    confidence=plan_float(item, "confidence", default=0.5),
                    source="consolidation",
                    pinned=plan_bool(item, "pinned", default=False),
                    moment=moment,
                )
                if memory_id is None:
                    # The fact is already known; keep the new provenance anyway.
                    memory_id = _existing_memory_id(connection, text)
                else:
                    counts["promotions"] += 1
                if memory_id is None:
                    continue
                for source_id in plan_int_list(item, "sources"):
                    _insert_edge(
                        connection,
                        src_kind="memory",
                        src_id=memory_id,
                        dst_kind="episode",
                        dst_id=source_id,
                        rel="derived_from",
                        weight=1.0,
                        moment=moment,
                    )

            for item in plan_entries(plan, "merges"):
                keep_id = plan_int(item, "keep")
                merge_ids = [
                    value for value in plan_int_list(item, "merge") if value != keep_id
                ]
                if keep_id is None or not merge_ids:
                    continue
                if not _memory_ids_exist(connection, {keep_id, *merge_ids}):
                    continue
                text = plan_str(item, "text")
                if text:
                    _update_memory_text(connection, keep_id, text, moment)
                kind = plan_str(item, "kind")
                if kind:
                    connection.execute(
                        "UPDATE memories SET kind = ?, updated_at = ? WHERE id = ?",
                        (kind, moment, keep_id),
                    )
                for merged_id in merge_ids:
                    _insert_edge(
                        connection,
                        src_kind="memory",
                        src_id=keep_id,
                        dst_kind="memory",
                        dst_id=merged_id,
                        rel="merged_from",
                        weight=1.0,
                        moment=moment,
                    )
                    connection.execute(
                        "UPDATE memories SET status = 'merged', updated_at = ? WHERE id = ?",
                        (moment, merged_id),
                    )
                counts["merges"] += 1

            for item in plan_entries(plan, "syntheses"):
                text = plan_str(item, "text")
                if not text:
                    continue
                memory_id = _insert_memory_row(
                    connection,
                    text,
                    kind=plan_str(item, "kind") or "summary",
                    status="active",
                    confidence=plan_float(item, "confidence", default=0.7),
                    source="consolidation",
                    pinned=plan_bool(item, "pinned", default=False),
                    moment=moment,
                )
                if memory_id is None:
                    memory_id = _existing_memory_id(connection, text)
                else:
                    counts["syntheses"] += 1
                if memory_id is None:
                    continue
                for source_id in plan_int_list(item, "sources"):
                    _insert_edge(
                        connection,
                        src_kind="memory",
                        src_id=memory_id,
                        dst_kind="episode",
                        dst_id=source_id,
                        rel="derived_from",
                        weight=1.0,
                        moment=moment,
                    )
                archive_sources = plan_bool(item, "archive_sources", default=False)
                for source_id in plan_int_list(item, "memories"):
                    if source_id == memory_id or not _memory_ids_exist(connection, {source_id}):
                        continue
                    _insert_edge(
                        connection,
                        src_kind="memory",
                        src_id=memory_id,
                        dst_kind="memory",
                        dst_id=source_id,
                        rel="generalizes",
                        weight=1.0,
                        moment=moment,
                    )
                    if archive_sources:
                        connection.execute(
                            "UPDATE memories SET status = 'merged', updated_at = ?"
                            " WHERE id = ? AND status IN ('active', 'candidate')",
                            (moment, source_id),
                        )

            for item in plan_entries(plan, "rewrites"):
                memory_id = plan_int(item, "id")
                if memory_id is None or not _memory_ids_exist(connection, {memory_id}):
                    continue
                text = plan_str(item, "text")
                if text:
                    _update_memory_text(connection, memory_id, text, moment)
                kind = plan_str(item, "kind")
                if kind:
                    connection.execute(
                        "UPDATE memories SET kind = ?, updated_at = ? WHERE id = ?",
                        (kind, moment, memory_id),
                    )
                confidence = plan_optional_float(item, "confidence")
                if confidence is not None:
                    connection.execute(
                        "UPDATE memories SET confidence = ?, updated_at = ? WHERE id = ?",
                        (confidence, moment, memory_id),
                    )
                counts["rewrites"] += 1

            for item in plan_entries(plan, "links"):
                from_id = plan_int(item, "from")
                to_id = plan_int(item, "to")
                if from_id is None or to_id is None or from_id == to_id:
                    continue
                if not _memory_ids_exist(connection, {from_id, to_id}):
                    continue
                _insert_edge(
                    connection,
                    src_kind="memory",
                    src_id=from_id,
                    dst_kind="memory",
                    dst_id=to_id,
                    rel=plan_str(item, "rel") or "related",
                    weight=plan_float(item, "weight", default=1.0),
                    moment=moment,
                )
                counts["links"] += 1

            for item in plan_entries(plan, "contradictions"):
                keep_id = plan_int(item, "keep")
                other_ids = [
                    value
                    for value in plan_int_list(item, "contradicts")
                    if value != keep_id
                ]
                if keep_id is None or not other_ids:
                    continue
                if not _memory_ids_exist(connection, {keep_id, *other_ids}):
                    continue
                text = plan_str(item, "text")
                if text:
                    _update_memory_text(connection, keep_id, text, moment)
                resolution = plan_str(item, "resolution") or "flag"
                for other_id in other_ids:
                    _insert_edge(
                        connection,
                        src_kind="memory",
                        src_id=keep_id,
                        dst_kind="memory",
                        dst_id=other_id,
                        rel="contradicts",
                        weight=1.0,
                        moment=moment,
                    )
                    if resolution == "supersede":
                        _insert_edge(
                            connection,
                            src_kind="memory",
                            src_id=keep_id,
                            dst_kind="memory",
                            dst_id=other_id,
                            rel="supersedes",
                            weight=1.0,
                            moment=moment,
                        )
                        connection.execute(
                            "UPDATE memories SET status = 'archived', updated_at = ? WHERE id = ?",
                            (moment, other_id),
                        )
                    elif resolution == "flag":
                        connection.execute(
                            "UPDATE memories SET confidence = MIN(confidence, 0.4),"
                            " updated_at = ? WHERE id = ?",
                            (moment, other_id),
                        )
                counts["contradictions"] += 1

            for item in plan_entries(plan, "retention"):
                memory_id = plan_int(item, "id")
                if memory_id is None:
                    continue
                row = connection.execute(
                    "SELECT status FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
                if row is None:
                    continue
                status = _row_text(row, "status")
                if status not in {"active", "candidate", "open"}:
                    continue
                score = plan_optional_float(item, "score")
                if score is not None:
                    connection.execute(
                        "UPDATE memories SET confidence = ?, updated_at = ? WHERE id = ?",
                        (max(0.0, min(1.0, score)), moment, memory_id),
                    )
                action = plan_str(item, "action").lower()
                if action == "archive":
                    connection.execute(
                        "UPDATE memories SET status = 'archived', updated_at = ? WHERE id = ?",
                        (moment, memory_id),
                    )
                elif action == "keep" and status in {"active", "candidate"}:
                    connection.execute(
                        "UPDATE memories SET status = 'active', updated_at = ? WHERE id = ?",
                        (moment, memory_id),
                    )
                counts["retention"] += 1

            for item in plan_entries(plan, "gaps"):
                text = plan_str(item, "text")
                if not text:
                    continue
                priority = plan_str(item, "priority").lower()
                confidence = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(priority, 0.5)
                memory_id = _insert_memory_row(
                    connection,
                    text,
                    kind="gap",
                    status="open",
                    confidence=confidence,
                    source="consolidation",
                    pinned=False,
                    moment=moment,
                )
                if memory_id is None:
                    memory_id = _existing_memory_id(connection, text)
                else:
                    counts["gaps"] += 1
                if memory_id is None:
                    continue
                for source_id in plan_int_list(item, "sources"):
                    _insert_edge(
                        connection,
                        src_kind="memory",
                        src_id=memory_id,
                        dst_kind="episode",
                        dst_id=source_id,
                        rel="derived_from",
                        weight=1.0,
                        moment=moment,
                    )

            for item in plan_entries(plan, "resolved_gaps"):
                memory_id = plan_int(item, "id")
                if memory_id is None:
                    continue
                row = connection.execute(
                    "SELECT kind, status FROM memories WHERE id = ?", (memory_id,)
                ).fetchone()
                if row is None or _row_text(row, "kind") != "gap":
                    continue
                if _row_text(row, "status") != "open":
                    continue
                text = plan_str(item, "text")
                if text:
                    _update_memory_text(connection, memory_id, text, moment)
                connection.execute(
                    "UPDATE memories SET status = 'resolved', updated_at = ? WHERE id = ?",
                    (moment, memory_id),
                )
                counts["resolved_gaps"] += 1

            for item in plan_entries(plan, "drops"):
                memory_id = plan_int(item, "id")
                if memory_id is None or not _memory_ids_exist(connection, {memory_id}):
                    continue
                connection.execute(
                    "UPDATE memories SET status = 'archived', updated_at = ? WHERE id = ?",
                    (moment, memory_id),
                )
                counts["drops"] += 1
        return counts

    # -- admin purge and stats -------------------------------------------------

    def rollover(
        self,
        today: str,
        *,
        up_to_id: int | None = None,
        retain_days: int = 1,
    ) -> int:
        """Explicitly purge old verbatim episodes; returns the rows removed.

        This is an administrative escape hatch, not part of normal operation:
        the single memory tier is unbounded, and the daily rollover only writes
        a compressed backup and clears the working state. Call this only when a
        user deliberately wants to forget old raw turns. The structured memory
        graph is never touched.
        """
        if not self.enabled:
            return 0
        cutoff = retention_cutoff(today, retain_days)
        sql = "DELETE FROM episodes WHERE day < ?"
        params: list[object] = [cutoff]
        if up_to_id is not None:
            sql += " AND id <= ?"
            params.append(max(0, int(up_to_id)))
        with self._write() as connection:
            cursor = connection.execute(sql, params)
            removed = max(0, cursor.rowcount)
        if removed:
            logger.info("Memory admin purge: dropped {} verbatim episodes before {}", removed, cutoff)
        return removed

    def stats(self) -> dict[str, object]:
        """Operational health for the store: counts, sizes, and freshness."""
        if not self.enabled:
            return {
                "enabled": False,
                "episodes": 0,
                "memories": 0,
                "edges": 0,
                "pending_episodes": 0,
                "memory_uses_total": 0,
                "memories_never_used": 0,
                "gaps_open": 0,
                "contradictions": 0,
                "consolidation_cursor": 0,
                "consolidation_last_attempt_at": None,
                "consolidation_last_ok_at": None,
                "consolidation_last_error": None,
                "maintenance_last_attempt_at": None,
                "maintenance_last_ok_at": None,
                "maintenance_last_error": None,
                "db_bytes": 0,
                "wal_bytes": 0,
                "last_checkpoint": None,
                "last_snapshot": None,
                "backups": 0,
                "backup_bytes": 0,
                "backup_dir": str(self.backup_dir),
            }
        cursor = self.consolidation_cursor()
        with self._lock:
            connection = self._connect()

            def scalar(sql: str, *params: object) -> int:
                row = connection.execute(sql, params).fetchone()
                return _int(cast(object, row[0])) if row is not None else 0

            counts = {
                "episodes": scalar("SELECT COUNT(*) FROM episodes"),
                "memories": scalar("SELECT COUNT(*) FROM memories"),
                "edges": scalar("SELECT COUNT(*) FROM edges"),
                "pending_episodes": scalar(
                    "SELECT COUNT(*) FROM episodes WHERE id > ?", cursor,
                ),
                "memory_uses_total": scalar("SELECT COALESCE(SUM(uses), 0) FROM memories"),
                "memories_never_used": scalar(
                    "SELECT COUNT(*) FROM memories"
                    " WHERE uses = 0 AND status IN ('active', 'candidate')"
                ),
                "gaps_open": scalar(
                    "SELECT COUNT(*) FROM memories WHERE kind = 'gap' AND status = 'open'"
                ),
                "contradictions": scalar(
                    "SELECT COUNT(*) FROM edges WHERE rel = 'contradicts'"
                ),
                "backups": len(self._backup_files()),
                "backup_bytes": self._backup_bytes(),
            }
        return {
            "enabled": True,
            **counts,
            "consolidation_cursor": cursor,
            "consolidation_last_attempt_at": self.get_meta(
                CONSOLIDATION_LAST_ATTEMPT_META_KEY,
            ),
            "consolidation_last_ok_at": self.get_meta(CONSOLIDATION_LAST_OK_META_KEY),
            "consolidation_last_error": self.get_meta(CONSOLIDATION_LAST_ERROR_META_KEY),
            "maintenance_last_attempt_at": self.get_meta(
                MAINTENANCE_LAST_ATTEMPT_META_KEY,
            ),
            "maintenance_last_ok_at": self.get_meta(MAINTENANCE_LAST_OK_META_KEY),
            "maintenance_last_error": self.get_meta(MAINTENANCE_LAST_ERROR_META_KEY),
            "db_bytes": self._file_bytes(self.path),
            "wal_bytes": self._wal_bytes(),
            "last_checkpoint": self.get_meta(_WAL_CHECKPOINT_META_KEY),
            "last_snapshot": self.get_meta(_SNAPSHOT_META_KEY),
            "backup_dir": str(self.backup_dir),
        }

    def counts(self) -> dict[str, int]:
        """Row counts per tier, for status output and tests."""
        if not self.enabled:
            return {"episodes": 0, "memories": 0, "edges": 0}
        with self._lock:
            connection = self._connect()

            def count(table: str) -> int:
                row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                return _int(cast(object, row[0])) if row is not None else 0

            return {
                "episodes": count("episodes"),
                "memories": count("memories"),
                "edges": count("edges"),
            }
