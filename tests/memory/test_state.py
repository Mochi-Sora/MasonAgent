"""Tests for the framework-maintained, session-scoped working state."""

from __future__ import annotations

from pathlib import Path

from mason.memory.state import (
    DEFAULT_STATE_MAX_CHARS,
    MemoryState,
    MemoryStateStore,
    SessionState,
    parse_session_state,
    render_session_state,
)


def test_state_is_session_scoped(tmp_path: Path) -> None:
    state = MemoryState(tmp_path, session_key="telegram:42")

    assert state.path.parent == tmp_path / "memory" / "state"
    assert state.path.suffix == ".md"
    assert state.path.name != "state.md"
    assert MemoryState(tmp_path).path == tmp_path / "memory" / "state.md"


def test_default_state_cap_is_4kb(tmp_path: Path) -> None:
    assert MemoryState(tmp_path).max_chars == DEFAULT_STATE_MAX_CHARS == 4096


def test_write_read_cap_and_clear(tmp_path: Path) -> None:
    state = MemoryState(tmp_path, session_key="cli:1", max_chars=256)
    assert state.read() == ""
    stored, truncated = state.write("goal: finish the memory system")
    assert stored == "goal: finish the memory system"
    assert truncated is False
    assert state.read() == stored

    stored, truncated = state.write("x" * 400)
    assert truncated is True
    assert len(stored) <= 256
    assert state.read() == stored

    state.clear()
    assert state.read() == ""
    assert state.path.exists()


def test_sections_render_and_parse(tmp_path: Path) -> None:
    state = MemoryState(tmp_path, session_key="cli:1")
    sections = SessionState(
        current_goal="ship the state rewrite",
        done="wrote the store\nmigrated the tool",
        next_steps="add the writer tests",
        session_summary="working on session state",
    )
    stored, truncated = state.write_sections(sections)

    assert truncated is False
    assert "## Current goal" in stored
    assert "## What has been done" in stored
    assert "## What's next" in stored
    assert "## Session state" in stored
    assert parse_session_state(stored) == sections.normalized()


def test_sections_are_trimmed_to_the_cap(tmp_path: Path) -> None:
    state = MemoryState(tmp_path, session_key="cli:1", max_chars=512)
    stored, truncated = state.write_sections(
        SessionState(
            current_goal="g" * 800,
            done="d" * 800,
            next_steps="n" * 800,
            session_summary="s" * 800,
        )
    )

    assert truncated is True
    assert len(stored) <= 512
    parsed = parse_session_state(stored)
    assert parsed.current_goal
    assert parsed.session_summary


def test_markdown_headings_in_state_do_not_corrupt_history(tmp_path: Path) -> None:
    state = MemoryState(tmp_path, session_key="cli:1")
    state.write("## Goals\nfinish the memory system")
    state.write("## Decisions\nuse sqlite")
    state.write("## Next\nwrite docs")

    versions = state.versions()
    assert [version.content for version in versions] == [
        "## Decisions\nuse sqlite",
        "## Goals\nfinish the memory system",
    ]
    assert all("T" in version.recorded_at for version in versions)


def test_history_is_bounded(tmp_path: Path) -> None:
    state = MemoryState(tmp_path, session_key="cli:1", history_versions=2)
    for index in range(5):
        state.write(f"version {index}")

    assert [version.content for version in state.versions()] == [
        "version 3",
        "version 2",
    ]


def test_clear_archives_the_outgoing_state(tmp_path: Path) -> None:
    state = MemoryState(tmp_path, session_key="cli:1")
    state.write("daily context")
    state.clear()

    assert state.read() == ""
    assert "daily context" in state.history_path.read_text(encoding="utf-8")


def test_write_sections_refuses_to_clear(tmp_path: Path) -> None:
    state = MemoryState(tmp_path, session_key="cli:1")
    state.write_sections(SessionState(current_goal="keep me"))

    try:
        state.write_sections(SessionState())
    except ValueError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("empty section updates must not clear the state")

    assert "keep me" in state.read()


def test_write_sections_if_unchanged_skips_after_clear(tmp_path: Path) -> None:
    state = MemoryState(tmp_path, session_key="cli:1")
    state.write_sections(SessionState(current_goal="old goal"))
    snapshot = state.read()

    state.clear()
    written = state.write_sections_if_unchanged(
        snapshot,
        SessionState(current_goal="late writer"),
    )

    assert written is None
    assert state.read() == ""


def test_store_resolves_sessions_and_lists_them(tmp_path: Path) -> None:
    store = MemoryStateStore(tmp_path)
    store.for_session("telegram:42").write_sections(SessionState(current_goal="telegram"))
    store.for_session("discord:7").write_sections(SessionState(current_goal="discord"))

    assert store.for_session("telegram:42").read_sections().current_goal == "telegram"
    assert store.list_session_keys() == ["discord:7", "telegram:42"]


def test_render_session_state_keeps_the_full_cap(tmp_path: Path) -> None:
    rendered, truncated = render_session_state(
        SessionState(current_goal="goal", done="done", next_steps="next", session_summary="session"),
        max_chars=DEFAULT_STATE_MAX_CHARS,
    )

    assert truncated is False
    assert len(rendered) <= DEFAULT_STATE_MAX_CHARS
