"""Tests for the periodic consolidation pass."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.skills import SkillsLoader
from nanobot.memory.consolidation import MemoryConsolidator
from nanobot.memory.store import MemoryDB
from nanobot.providers.base import GenerationSettings, LLMResponse
from nanobot.utils.llm_runtime import LLMRuntime

_NOW = datetime(2026, 1, 2, 12, 0, 0)


@pytest.fixture
def db(tmp_path: Path):
    store = MemoryDB(tmp_path / "memory" / "memory.db")
    yield store
    store.close()


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.chat_stream_with_retry = AsyncMock()
    provider.generation = GenerationSettings(max_tokens=100)
    return provider


@pytest.fixture
def runtime(mock_provider):
    return LLMRuntime.capture(mock_provider, "test-model", context_window_tokens=1000)


def _plan_response(payload: dict[str, object]) -> LLMResponse:
    return LLMResponse(content=json.dumps(payload))


def _seed_episodes(db: MemoryDB, count: int = 1, *, offset: int = 0) -> None:
    db.capture_messages(
        "cli:1",
        [
            {
                "role": "user",
                "content": f"turn {offset + index}",
                "timestamp": "2026-01-02T09:00:00",
            }
            for index in range(count)
        ],
        now=_NOW,
    )


async def test_no_episodes_skips_the_model(
    tmp_path: Path, db: MemoryDB, runtime, mock_provider
) -> None:
    result = await MemoryConsolidator(tmp_path, db).run(runtime)

    assert result.ok is True
    assert result.episodes == 0
    mock_provider.chat_stream_with_retry.assert_not_awaited()


async def test_promotions_link_sources_and_advance_cursor(
    tmp_path: Path, db: MemoryDB, runtime, mock_provider
) -> None:
    _seed_episodes(db)
    mock_provider.chat_stream_with_retry.return_value = _plan_response({
        "promotions": [
            {
                "text": "user prefers dark mode",
                "kind": "preference",
                "confidence": 0.9,
                "sources": [1],
            }
        ]
    })

    result = await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)

    assert result.ok is True
    assert result.applied is not None and result.applied["promotions"] == 1
    assert db.consolidation_cursor() == 1
    hits = db.search_memories("dark mode")
    assert [hit.text for hit in hits] == ["user prefers dark mode"]
    assert hits[0].kind == "preference"
    assert db.counts()["edges"] == 1
    snapshot = tmp_path / "memory" / "consolidation" / "latest.md"
    assert snapshot.is_file()
    assert "user prefers dark mode" in snapshot.read_text(encoding="utf-8")


async def test_merges_rewrites_links_and_drops(
    tmp_path: Path, db: MemoryDB, runtime, mock_provider
) -> None:
    first = db.insert_memory("user prefers dark mode", now=_NOW)
    second = db.insert_memory("user likes dark themes", now=_NOW)
    third = db.insert_memory("user is based in Berlin", now=_NOW)
    assert first and second and third
    _seed_episodes(db)
    mock_provider.chat_stream_with_retry.return_value = _plan_response({
        "merges": [{"keep": first, "merge": [second], "text": "user prefers dark mode"}],
        "rewrites": [{"id": third, "text": "user is based in Berlin, Germany", "confidence": 0.9}],
        "links": [{"from": first, "to": third, "rel": "related"}],
        "drops": [{"id": third, "reason": "test"}],
    })

    result = await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)

    assert result.ok is True
    assert result.applied == {
        "promotions": 0,
        "merges": 1,
        "rewrites": 1,
        "links": 1,
        "drops": 1,
    }
    # The merged duplicate is excluded from recall; a merged_from edge records why.
    assert [hit.text for hit in db.search_memories("dark")] == ["user prefers dark mode"]
    dropped = db.search_memories("Berlin")
    assert dropped == []  # dropped → archived
    # One merged_from edge plus one related link.
    assert db.counts()["edges"] == 2


async def test_invalid_plan_leaves_cursor_untouched(
    tmp_path: Path, db: MemoryDB, runtime, mock_provider
) -> None:
    _seed_episodes(db)
    mock_provider.chat_stream_with_retry.return_value = LLMResponse(content="not json at all")

    result = await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)

    assert result.ok is False
    assert result.error is not None
    assert db.consolidation_cursor() == 0
    assert db.counts()["memories"] == 0


async def test_model_failure_is_reported_not_raised(
    tmp_path: Path, db: MemoryDB, runtime, mock_provider
) -> None:
    _seed_episodes(db)
    mock_provider.chat_stream_with_retry.side_effect = RuntimeError("api down")

    result = await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)

    assert result.ok is False
    assert result.error is not None and "model call failed" in result.error
    assert db.consolidation_cursor() == 0


async def test_unknown_and_malformed_entries_are_skipped(
    tmp_path: Path, db: MemoryDB, runtime, mock_provider
) -> None:
    _seed_episodes(db)
    mock_provider.chat_stream_with_retry.return_value = _plan_response({
        "promotions": [{"kind": "fact"}, "not an object"],
        "merges": [{"keep": 999, "merge": [998]}],
        "rewrites": [{"id": 999, "text": "ghost"}],
        "links": [{"from": 1, "to": 1}],
        "drops": [{"id": 999}],
        "unexpected": {"nested": True},
    })

    result = await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)

    assert result.ok is True
    assert result.applied == {
        "promotions": 0,
        "merges": 0,
        "rewrites": 0,
        "links": 0,
        "drops": 0,
    }
    assert db.consolidation_cursor() == 1


async def test_batches_advance_incrementally(
    tmp_path: Path, db: MemoryDB, runtime, mock_provider
) -> None:
    _seed_episodes(db, count=50)
    mock_provider.chat_stream_with_retry.return_value = _plan_response({})

    first = await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)
    assert first.episodes == 40
    assert db.consolidation_cursor() == 40

    second = await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)
    assert second.episodes == 10
    assert db.consolidation_cursor() == 50

    third = await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)
    assert third.episodes == 0


async def test_prompt_carries_turns_and_current_memories(
    tmp_path: Path, db: MemoryDB, runtime, mock_provider
) -> None:
    db.insert_memory("existing durable fact", now=_NOW)
    _seed_episodes(db)
    mock_provider.chat_stream_with_retry.return_value = _plan_response({})

    await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)

    call = mock_provider.chat_stream_with_retry.await_args.kwargs
    assert "long-term memory" in call["messages"][0]["content"]
    assert "#1" in call["messages"][1]["content"]
    assert "existing durable fact" in call["messages"][1]["content"]


async def test_skills_are_created_then_retired(
    tmp_path: Path, db: MemoryDB, runtime, mock_provider
) -> None:
    _seed_episodes(db)
    mock_provider.chat_stream_with_retry.return_value = _plan_response({
        "skills": [
            {
                "name": "release-checklist",
                "description": "Run the release checklist",
                "body": "1. Tag\n2. Build\n3. Publish",
            },
            {"name": "Not Kebab", "description": "bad", "body": "ignored"},
        ]
    })
    result = await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)

    assert result.skills == ("release-checklist",)
    skill_path = tmp_path / "skills" / "release-checklist" / "SKILL.md"
    assert skill_path.is_file()
    loader = SkillsLoader(tmp_path)
    assert "Run the release checklist" in (loader.get_skill_description("release-checklist") or "")
    assert "Tag" in (loader.load_skill("release-checklist") or "")

    _seed_episodes(db, count=1, offset=100)
    mock_provider.chat_stream_with_retry.return_value = _plan_response({
        "retire_skills": ["release-checklist"],
    })
    retired = await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)

    assert retired.retired_skills == ("release-checklist",)
    assert not skill_path.exists()
    assert (tmp_path / "skills" / "retired" / "release-checklist" / "SKILL.md").is_file()
    assert loader.list_skills() and all(
        entry["name"] != "release-checklist" for entry in loader.list_skills()
    )


async def test_user_authored_skills_are_never_touched(
    tmp_path: Path, db: MemoryDB, runtime, mock_provider
) -> None:
    user_skill = tmp_path / "skills" / "release-checklist" / "SKILL.md"
    user_skill.parent.mkdir(parents=True)
    user_skill.write_text("---\nname: release-checklist\n---\n\nMine.\n", encoding="utf-8")
    _seed_episodes(db)
    mock_provider.chat_stream_with_retry.return_value = _plan_response({
        "skills": [
            {"name": "release-checklist", "description": "overwrite", "body": "nope"},
        ],
        "retire_skills": ["release-checklist"],
    })

    result = await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)

    assert result.skills == ()
    assert result.retired_skills == ()
    assert user_skill.read_text(encoding="utf-8").endswith("Mine.\n")


async def test_success_records_health_metadata(
    tmp_path: Path, db: MemoryDB, runtime, mock_provider
) -> None:
    _seed_episodes(db)
    mock_provider.chat_stream_with_retry.return_value = _plan_response({})

    await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)

    stats = db.stats()
    assert stats["consolidation_last_ok_at"] == _NOW.isoformat()
    assert stats["consolidation_last_attempt_at"] == _NOW.isoformat()
    assert stats["consolidation_last_error"] == ""


async def test_failure_records_error_and_keeps_cursor(
    tmp_path: Path, db: MemoryDB, runtime, mock_provider
) -> None:
    _seed_episodes(db)
    mock_provider.chat_stream_with_retry.side_effect = RuntimeError("api down")

    await MemoryConsolidator(tmp_path, db).run(runtime, now=_NOW)

    stats = db.stats()
    assert stats["consolidation_last_ok_at"] is None
    assert stats["consolidation_last_attempt_at"] == _NOW.isoformat()
    assert isinstance(stats["consolidation_last_error"], str)
    assert "model call failed" in stats["consolidation_last_error"]
    assert stats["consolidation_cursor"] == 0
