"""Tests for the model-owned working state file."""

from __future__ import annotations

from pathlib import Path

from nanobot.memory.state import MemoryState


def test_state_path_is_workspace_memory(tmp_path: Path) -> None:
    assert MemoryState(tmp_path).path == tmp_path / "memory" / "state.md"


def test_write_read_cap_and_clear(tmp_path: Path) -> None:
    state = MemoryState(tmp_path, max_chars=256)
    assert state.read() == ""
    stored, truncated = state.write("goal: finish the memory system")
    assert stored == "goal: finish the memory system"
    assert truncated is False
    assert state.read() == stored

    stored, truncated = state.write("x" * 400)
    assert truncated is True
    assert len(stored) == 256
    assert state.read() == stored

    state.clear()
    assert state.read() == ""
    assert state.path.exists()


def test_write_creates_parent_directory(tmp_path: Path) -> None:
    state = MemoryState(tmp_path / "nested")
    state.write("hello")
    assert (tmp_path / "nested" / "memory" / "state.md").is_file()


def test_history_archives_outgoing_state_newest_first(tmp_path: Path) -> None:
    state = MemoryState(tmp_path)
    state.write("first")
    state.write("second")
    state.write("third")

    history = state.history_path.read_text(encoding="utf-8")
    # Newest archived version is on top; the live state is never duplicated.
    assert history.index("second") < history.index("first")
    assert "third" not in history


def test_history_is_bounded(tmp_path: Path) -> None:
    state = MemoryState(tmp_path, history_versions=2)
    for index in range(5):
        state.write(f"version {index}")

    history = state.history_path.read_text(encoding="utf-8")
    assert "version 3" in history
    assert "version 2" in history
    assert "version 1" not in history
    assert "version 0" not in history


def test_history_is_disabled_at_zero_versions(tmp_path: Path) -> None:
    state = MemoryState(tmp_path, history_versions=0)
    state.write("first")
    state.write("second")
    assert not state.history_path.exists()


def test_clear_archives_the_outgoing_state(tmp_path: Path) -> None:
    state = MemoryState(tmp_path)
    state.write("daily context")
    state.clear()
    assert state.read() == ""
    assert "daily context" in state.history_path.read_text(encoding="utf-8")


def test_versions_parses_archived_states_newest_first(tmp_path: Path) -> None:
    state = MemoryState(tmp_path)
    state.write("first")
    state.write("second")
    state.write("third")

    versions = state.versions()
    assert [version.content for version in versions] == ["second", "first"]
    assert all(version.recorded_at for version in versions)


def test_versions_respects_limit_and_empty(tmp_path: Path) -> None:
    state = MemoryState(tmp_path)
    for index in range(6):
        state.write(f"version {index}")

    assert [v.content for v in state.versions(limit=2)] == ["version 4", "version 3"]
    assert state.versions(limit=0) == []
