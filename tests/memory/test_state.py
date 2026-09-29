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
