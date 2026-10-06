"""Full test suite for the memory tools: state, curated recall, and backup recall."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pytest

from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.memory import RecallBackupTool, RecallMemoryTool, UpdateStateTool
from nanobot.config.schema import ToolsConfig
from nanobot.memory.state import MemoryState
from nanobot.memory.store import MemoryDB

_NOW = datetime(2026, 1, 2, 12, 0, 0)
_TOOL_NAMES = {
    UpdateStateTool: "update_state",
    RecallMemoryTool: "recall_memory",
    RecallBackupTool: "recall_backup",
}


@pytest.fixture
def db(tmp_path: Path):
    store = MemoryDB(tmp_path / "memory" / "memory.db")
    yield store
    store.close()


def _iso(day: str, hour: int = 10) -> str:
    return f"{day}T{hour:02d}:00:00"


def _msg(role: str, content: str, ts: str) -> dict[str, object]:
    return {"role": role, "content": content, "timestamp": ts}


# -- tool contract ------------------------------------------------------------


def test_tools_require_memory_context(tmp_path: Path) -> None:
    ctx = ToolContext(config=ToolsConfig(), workspace=str(tmp_path))
    for tool_cls in _TOOL_NAMES:
        assert tool_cls.enabled(ctx) is False
        with pytest.raises(RuntimeError):
            tool_cls.create(ctx)


def test_tools_are_enabled_with_memory_context(tmp_path: Path, db: MemoryDB) -> None:
    ctx = ToolContext(
        config=ToolsConfig(),
        workspace=str(tmp_path),
        memory_db=db,
        memory_state=MemoryState(tmp_path),
    )
    for tool_cls, expected_name in _TOOL_NAMES.items():
        assert tool_cls.enabled(ctx) is True
        assert tool_cls.create(ctx).name == expected_name


# -- update_state -------------------------------------------------------------


async def test_update_state_round_trip_and_clear(tmp_path: Path) -> None:
    state = MemoryState(tmp_path)
    tool = UpdateStateTool(state)
    result = await tool.execute(content="goal: ship phase 3")
    assert "updated" in result
    assert state.read() == "goal: ship phase 3"
    cleared = await tool.execute(content="")
    assert "cleared" in cleared
    assert state.read() == ""


async def test_update_state_keeps_multiline_content(tmp_path: Path) -> None:
    state = MemoryState(tmp_path)
    tool = UpdateStateTool(state)
    content = "## Goals\n- ship memory tools\n\n## Notes\n- user prefers dark mode"
    assert "chars" in await tool.execute(content=content)
    assert state.read() == content


async def test_update_state_treats_whitespace_only_as_clear(tmp_path: Path) -> None:
    state = MemoryState(tmp_path)
    state.write("existing")
    tool = UpdateStateTool(state)
    assert "cleared" in await tool.execute(content="   \n\t ")
    assert state.read() == ""


async def test_update_state_truncates_at_cap(tmp_path: Path) -> None:
    state = MemoryState(tmp_path, max_chars=256)
    tool = UpdateStateTool(state)
    result = await tool.execute(content="x" * 1_000)
    assert "truncated" in result
    assert len(state.read()) == 256


async def test_update_state_leaves_no_temp_files(tmp_path: Path) -> None:
    state = MemoryState(tmp_path)
    tool = UpdateStateTool(state)
    await tool.execute(content="hello")
    leftovers = [p.name for p in state.path.parent.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []


# -- recall_memory ------------------------------------------------------------


async def test_recall_memory_requires_a_query(db: MemoryDB) -> None:
    tool = RecallMemoryTool(db)
    result = await tool.execute(query="   ")
    assert getattr(result, "is_error", False) is True


async def test_recall_memory_formats_id_kind_and_pinned(db: MemoryDB) -> None:
    db.insert_memory("user prefers dark mode", kind="preference", pinned=True, now=_NOW)
    result = await RecallMemoryTool(db).execute(query="dark mode")
    assert "dark mode" in result
    assert "preference" in result
    assert "pinned" in result


async def test_recall_memory_respects_limit(db: MemoryDB) -> None:
    for index in range(3):
        db.insert_memory(f"dark mode variant {index}", now=_NOW)
    result = await RecallMemoryTool(db).execute(query="dark mode", limit=1)
    assert result.count("- [#") == 1


async def test_recall_memory_no_matches(db: MemoryDB) -> None:
    db.insert_memory("user prefers dark mode", now=_NOW)
    result = await RecallMemoryTool(db).execute(query="zzz_absent")
    assert result == "No matching long-term memories."


async def test_recall_memory_includes_candidates(db: MemoryDB) -> None:
    db.insert_memory("tentative fact about tea", status="candidate", now=_NOW)
    result = await RecallMemoryTool(db).execute(query="tea")
    assert "tentative fact about tea" in result


async def test_recall_memory_handles_hostile_queries(db: MemoryDB) -> None:
    db.insert_memory("quotes and stars", now=_NOW)
    tool = RecallMemoryTool(db)
    for query in ['"', "*", "AND", "NEAR(", 'a" OR "b', "()", "..."]:
        await tool.execute(query=query)


async def test_recall_memory_finds_cjk_substrings(db: MemoryDB) -> None:
    db.insert_memory("用户喜欢深色模式", now=_NOW)
    result = await RecallMemoryTool(db).execute(query="深色模式")
    assert "用户喜欢深色模式" in result


async def test_recall_memory_is_fast_on_large_graph(db: MemoryDB) -> None:
    with db._write() as connection:  # bulk fixture: one transaction
        for index in range(4_000):
            connection.execute(
                "INSERT INTO memories"
                " (text, kind, status, pinned, confidence, source, created_at, updated_at,"
                "  content_hash)"
                " VALUES (?, 'fact', 'active', 0, 0.5, 'test', ?, ?, ?)",
                (f"durable note {index} about magpie{index % 13}", _NOW.isoformat(),
                 _NOW.isoformat(), f"hash-{index}"),
            )
    tool = RecallMemoryTool(db)
    started = time.perf_counter()
    result = await tool.execute(query="magpie7 durable")
    elapsed = time.perf_counter() - started
    assert "durable note" in result
    assert elapsed < 0.25, f"recall_memory took {elapsed:.3f}s"


# -- recall_backup ------------------------------------------------------------


async def test_recall_backup_lists_latest_turns(db: MemoryDB) -> None:
    db.capture_messages("cli:1", [_msg("user", "first question", _iso("2026-01-02", 9))])
    db.capture_messages("cli:2", [_msg("assistant", "latest answer", _iso("2026-01-02", 11))])
    result = await RecallBackupTool(db).execute()
    assert "latest answer" in result
    assert "first question" in result
    assert "cli:2" in result
    assert result.index("latest answer") < result.index("first question")


async def test_recall_backup_searches_and_filters_session(db: MemoryDB) -> None:
    db.capture_messages("cli:1", [_msg("user", "sourdough ratio", _iso("2026-01-02", 9))])
    db.capture_messages("cli:2", [_msg("user", "sourdough schedule", _iso("2026-01-02", 10))])
    tool = RecallBackupTool(db)
    assert "sourdough ratio" in await tool.execute(query="sourdough", session="cli:1")
    assert "sourdough schedule" not in await tool.execute(query="sourdough", session="cli:1")


async def test_recall_backup_distinguishes_no_match_from_empty(db: MemoryDB) -> None:
    tool = RecallBackupTool(db)
    assert await tool.execute() == "The recent backup is empty."
    db.capture_messages("cli:1", [_msg("user", "something", _iso("2026-01-02"))])
    assert await tool.execute(query="zzz_absent") == "Nothing in the recent backup matches that."


async def test_recall_backup_skips_tool_payloads(db: MemoryDB) -> None:
    db.capture_messages(
        "cli:1",
        [
            _msg("user", "check the file", _iso("2026-01-02")),
            {"role": "tool", "content": "SECRET_PAYLOAD", "timestamp": _iso("2026-01-02")},
        ],
    )
    assert "SECRET_PAYLOAD" not in await RecallBackupTool(db).execute()


async def test_recall_backup_truncates_long_turns(db: MemoryDB) -> None:
    db.capture_messages("cli:1", [_msg("user", "start " + "x" * 5_000, _iso("2026-01-02"))])
    result = await RecallBackupTool(db).execute(query="start")
    assert "(truncated)" in result
    assert len(result) < 3_000


async def test_recall_backup_respects_limit(db: MemoryDB) -> None:
    for index in range(5):
        db.capture_messages(
            "cli:1", [_msg("user", f"turn {index}", _iso("2026-01-02", 9 + index))],
        )
    result = await RecallBackupTool(db).execute(limit=2)
    assert result.count("- [") == 2
