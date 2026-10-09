"""Tests for the workspace DATES.md lookup tool."""

from __future__ import annotations

from pathlib import Path

from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.dates import DATES_FILENAME, SearchDatesTool
from nanobot.agent.tools.loader import ToolLoader
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import ToolsConfig


def _tool(tmp_path: Path) -> SearchDatesTool:
    return SearchDatesTool(tmp_path / DATES_FILENAME)


def _write(tmp_path: Path, text: str) -> None:
    (tmp_path / DATES_FILENAME).write_text(text, encoding="utf-8")


def test_create_uses_the_workspace_file(tmp_path: Path) -> None:
    ctx = ToolContext(config=ToolsConfig(), workspace=str(tmp_path))
    assert SearchDatesTool.enabled(ctx) is True
    tool = SearchDatesTool.create(ctx)
    assert tool.name == "search_dates"
    assert tool.read_only is True


def test_loader_registers_search_dates(tmp_path: Path) -> None:
    ctx = ToolContext(config=ToolsConfig(), workspace=str(tmp_path))
    registry = ToolRegistry()
    ToolLoader().load(ctx, registry)
    assert registry.has("search_dates")


async def test_missing_file_is_a_clear_message(tmp_path: Path) -> None:
    result = await _tool(tmp_path).execute()
    assert "No dated entries" in result


async def test_search_matches_keywords(tmp_path: Path) -> None:
    _write(tmp_path, "# Dates\n\n- 2026-10-05 — physics test\n- 2026-11-01 — renew domain\n")
    result = await _tool(tmp_path).execute(query="physics")
    assert "physics test" in result
    assert "renew domain" not in result


async def test_date_range_excludes_undated_entries(tmp_path: Path) -> None:
    _write(tmp_path, "- 2026-01-01 — old\n- 2026-06-15 — mid\n- 2026-12-31 — new\n- undated note\n")
    result = await _tool(tmp_path).execute(after="2026-06-01", before="2026-12-01")
    assert "mid" in result
    assert "old" not in result and "new" not in result
    assert "undated note" not in result


async def test_entries_are_sorted_ascending(tmp_path: Path) -> None:
    _write(tmp_path, "- 2026-12-31 — later\n- 2026-01-01 — earlier\n")
    result = await _tool(tmp_path).execute()
    assert result.index("earlier") < result.index("later")


async def test_invalid_bound_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path, "- 2026-01-01 — x\n")
    result = await _tool(tmp_path).execute(after="soon")
    assert getattr(result, "is_error", False) is True


async def test_limit_caps_results(tmp_path: Path) -> None:
    _write(tmp_path, "".join(f"- 2026-01-{day:02d} — day {day}\n" for day in range(1, 11)))
    result = await _tool(tmp_path).execute(limit=3)
    assert result.count("- [") == 3


async def test_no_match_is_distinct_from_empty(tmp_path: Path) -> None:
    _write(tmp_path, "- 2026-01-01 — something\n")
    assert await _tool(tmp_path).execute(query="zzz_absent") == "No dated entries match."
