"""One-time import of legacy memory files into the SQLite store.

Earlier releases kept long-term memory in ``memory/MEMORY.md`` and the raw
journal in ``memory/history.jsonl``. Both are imported once, then moved to
``memory/legacy/`` — nothing is deleted and repeated runs are no-ops.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from loguru import logger

from nanobot.memory.store import MemoryDB
from nanobot.utils.helpers import load_bundled_template

_MIGRATION_KEY = "legacy_imported"
_MAX_CHUNK_CHARS = 2_000
_LEGACY_SESSION_KEY = "legacy"


@dataclass(frozen=True, slots=True)
class MigrationReport:
    """What a single legacy import moved into the database."""

    memory_chunks: int
    history_entries: int
    moved: tuple[str, ...]


def import_legacy(workspace: Path, db: MemoryDB) -> MigrationReport | None:
    """Import legacy memory files into *db* once; returns None when not needed."""
    if not db.enabled:
        return None
    memory_dir = workspace / "memory"
    memory_file = memory_dir / "MEMORY.md"
    history_file = memory_dir / "history.jsonl"
    if not memory_file.exists() and not history_file.exists():
        # Nothing to import: leave the database untouched so fresh workspaces
        # never create one just to run this check.
        return None
    if db.get_meta(_MIGRATION_KEY) == "1":
        return None

    inserted_chunks = 0
    for chunk in _memory_chunks(memory_file):
        if db.insert_memory(chunk, kind="legacy", source="migration") is not None:
            inserted_chunks += 1

    by_session: dict[str, list[dict[str, Any]]] = {}
    for ts, content, session_key in _history_entries(history_file):
        by_session.setdefault(session_key, []).append(
            {"role": "assistant", "content": content, "timestamp": ts}
        )
    inserted_entries = 0
    for session_key, messages in by_session.items():
        inserted_entries += db.capture_messages(session_key, messages)

    moved: list[str] = []
    for path in (memory_file, history_file, memory_dir / ".cursor"):
        if not path.exists():
            continue
        moved_path = _move_to_legacy(path, memory_dir / "legacy")
        moved.append(moved_path.relative_to(workspace).as_posix())

    db.set_meta(_MIGRATION_KEY, "1")
    report = MigrationReport(
        memory_chunks=inserted_chunks,
        history_entries=inserted_entries,
        moved=tuple(moved),
    )
    if inserted_chunks or inserted_entries or moved:
        logger.info(
            "Migrated legacy memory: {} MEMORY.md chunks, {} history entries, {} files archived",
            inserted_chunks,
            inserted_entries,
            len(moved),
        )
    return report


def _move_to_legacy(path: Path, legacy_dir: Path) -> Path:
    legacy_dir.mkdir(parents=True, exist_ok=True)
    target = legacy_dir / path.name
    if target.exists():
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        target = legacy_dir / f"{path.stem}.{stamp}{path.suffix}"
    path.replace(target)
    return target


def _memory_chunks(path: Path) -> list[str]:
    """Split MEMORY.md into section-sized chunks, skipping the untouched template."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    if not text.strip():
        return []
    template = load_bundled_template("memory/MEMORY.md")
    if template is not None and text.strip() == template.strip():
        return []
    return _chunk_markdown(text)


def _chunk_markdown(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal size
        if current:
            chunks.append("\n\n".join(current))
            current.clear()
            size = 0

    for raw_block in re.split(r"\n\s*\n", text):
        block = raw_block.strip()
        if not block:
            continue
        if current and (block.startswith("#") or size + len(block) + 2 > max_chars):
            flush()
        while len(block) > max_chars:
            chunks.append(block[:max_chars].strip())
            block = block[max_chars:].strip()
        if block:
            current.append(block)
            size += len(block) + 2
    flush()
    return [chunk for chunk in chunks if chunk]


def _history_entries(path: Path) -> list[tuple[str, str, str]]:
    """Read legacy journal entries as ``(timestamp, content, session_key)`` triples."""
    if not path.exists():
        return []
    entries: list[tuple[str, str, str]] = []
    fallback_ts = datetime.now().isoformat()
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    parsed: object = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                record = cast(dict[str, object], parsed)
                content = record.get("content")
                if not isinstance(content, str) or not content.strip():
                    continue
                timestamp = record.get("timestamp")
                ts = timestamp if isinstance(timestamp, str) and timestamp else fallback_ts
                session_key = record.get("session_key")
                key = (
                    session_key
                    if isinstance(session_key, str) and session_key
                    else _LEGACY_SESSION_KEY
                )
                entries.append((ts, content, key))
    except OSError:
        return []
    return entries
