"""Tests for the one-time import of legacy memory files."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from nanobot.memory.migration import import_legacy
from nanobot.memory.store import MemoryDB
from nanobot.utils.helpers import load_bundled_template


def _write_legacy(workspace: Path) -> Path:
    memory_dir = workspace / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    (memory_dir / "MEMORY.md").write_text(
        "# Facts\n\nThe user likes dark mode.\n\n# Projects\n\nnanobot rewrite.\n",
        encoding="utf-8",
    )
    (memory_dir / "history.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {"cursor": 1, "timestamp": "2025-12-01 10:00", "content": "talked about sqlite"}
                ),
                json.dumps(
                    {
                        "cursor": 2,
                        "timestamp": "2025-12-01 10:05",
                        "content": "decided on FTS5",
                        "session_key": "cli:1",
                    }
                ),
                "not json",
                json.dumps({"cursor": 3, "unrelated": True}),
            ]
        ),
        encoding="utf-8",
    )
    (memory_dir / ".cursor").write_text("3", encoding="utf-8")
    return memory_dir


def test_imports_and_archives_legacy_files(tmp_path: Path) -> None:
    memory_dir = _write_legacy(tmp_path)
    store = MemoryDB(memory_dir / "memory.db")
    report = import_legacy(tmp_path, store)
    assert report is not None
    assert report.memory_chunks == 2
    assert report.history_entries == 2
    assert set(report.moved) == {
        "memory/legacy/MEMORY.md",
        "memory/legacy/history.jsonl",
        "memory/legacy/.cursor",
    }
    assert not (memory_dir / "MEMORY.md").exists()
    assert not (memory_dir / "history.jsonl").exists()
    assert (memory_dir / "legacy" / "MEMORY.md").exists()
    assert (memory_dir / "legacy" / "history.jsonl").exists()
    memory_hits = store.search_memories("dark mode")
    assert [hit.kind for hit in memory_hits] == ["legacy"]
    assert store.search_episodes("sqlite memory")
    assert store.counts()["episodes"] == 2
    store.close()


def test_import_runs_once(tmp_path: Path) -> None:
    memory_dir = _write_legacy(tmp_path)
    store = MemoryDB(memory_dir / "memory.db")
    assert import_legacy(tmp_path, store) is not None
    counts_after_first = store.counts()
    assert import_legacy(tmp_path, store) is None
    assert store.counts() == counts_after_first
    store.close()


def test_reimport_dedupes_when_files_reappear(tmp_path: Path) -> None:
    memory_dir = _write_legacy(tmp_path)
    store = MemoryDB(memory_dir / "memory.db")
    assert import_legacy(tmp_path, store) is not None
    counts = store.counts()
    for name in ("MEMORY.md", "history.jsonl"):
        shutil.copy(memory_dir / "legacy" / name, memory_dir / name)
    store.set_meta("legacy_imported", "0")
    report = import_legacy(tmp_path, store)
    assert report is not None
    assert report.memory_chunks == 0
    assert report.history_entries == 0
    assert store.counts() == counts
    store.close()


def test_untouched_template_is_not_imported(tmp_path: Path) -> None:
    template = load_bundled_template("memory/MEMORY.md")
    assert template
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text(template, encoding="utf-8")
    store = MemoryDB(memory_dir / "memory.db")
    report = import_legacy(tmp_path, store)
    assert report is not None
    assert report.memory_chunks == 0
    assert store.counts()["memories"] == 0
    store.close()


def test_missing_files_never_touch_the_database(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True)
    store = MemoryDB(memory_dir / "memory.db")
    assert import_legacy(tmp_path, store) is None
    assert not (memory_dir / "memory.db").exists()
    store.close()
