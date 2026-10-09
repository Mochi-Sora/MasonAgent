"""On-demand lookup of the workspace's dated-notes file (``DATES.md``).

Deadlines and date-anchored commitments are the wrong shape for long-term
memory: the date is the whole point, and curation deliberately favors timeless
facts. So they live in a plain markdown file the model maintains, and this tool
finds them when a question turns on timing.
"""

# Tool.execute accepts heterogeneous schemas.
# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from nanobot.utils.helpers import truncate_text

if TYPE_CHECKING:
    from nanobot.agent.tools.context import ToolContext

DATES_FILENAME = "DATES.md"
_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_ENTRY_PREVIEW_CHARS = 300


def _parse_iso(value: str) -> date | None:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _iter_entries(text: str) -> list[tuple[date | None, str]]:
    """Return ``(date, text)`` per list line, skipping headings and blanks.

    The leading ISO date is removed from *text* since the caller renders it
    separately, so an entry does not read its date twice.
    """
    entries: list[tuple[date | None, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.lstrip("-*+ ").strip()
        if not line:
            continue
        match = _DATE_RE.search(line)
        if match is None:
            entries.append((None, line))
            continue
        when = _parse_iso(match.group(0))
        rest = (line[: match.start()] + line[match.end() :]).strip(" -+*\u2014\u2013:|/\t")
        entries.append((when, rest or line))
    return entries


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema("Optional keywords to match within the dated entries."),
        after=StringSchema("Optional ISO date (YYYY-MM-DD): only entries on or after this day."),
        before=StringSchema("Optional ISO date (YYYY-MM-DD): only entries on or before this day."),
        limit=IntegerSchema(
            description="Maximum entries to return (1-50, default 20).",
            minimum=1,
            maximum=50,
        ),
        required=[],
    )
)
class SearchDatesTool(Tool):
    """Search the workspace dated-notes file (``DATES.md``)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return bool(getattr(ctx, "workspace", ""))

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(Path(ctx.workspace) / DATES_FILENAME)

    @property
    def name(self) -> str:
        return "search_dates"

    @property
    def description(self) -> str:
        return (
            "Search dated commitments, deadlines, and events recorded in DATES.md. Use it "
            "whenever an answer turns on timing: 'when is X', 'what is due', 'what is coming "
            "up'. Optionally narrow by keyword or an ISO date range."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        query: str = "",
        after: str | None = None,
        before: str | None = None,
        limit: int = 20,
        **kwargs: Any,
    ) -> str:
        after_date, after_error = self._bound("after", after)
        if after_error is not None:
            return after_error
        before_date, before_error = self._bound("before", before)
        if before_error is not None:
            return before_error

        if not self._path.exists():
            return f"No dated entries recorded yet (write {DATES_FILENAME} to add one)."
        try:
            entries = _iter_entries(self._path.read_text(encoding="utf-8"))
        except OSError:
            return ToolResult.error(f"Error: could not read {DATES_FILENAME}.")

        needle = " ".join(query.split()).lower()
        selected = [
            (when, text)
            for when, text in entries
            if self._matches(text, when, needle, after_date, before_date)
        ]
        if not selected:
            return "No dated entries match."

        selected.sort(key=lambda item: (item[0] is None, item[0] or date.max))
        shown = selected[: max(1, min(int(limit), 50))]
        lines = [f"Dated entries ({len(selected)}, showing {len(shown)}):"]
        for when, text in shown:
            stamp = when.isoformat() if when else "undated"
            lines.append(f"- [{stamp}] {truncate_text(text, _ENTRY_PREVIEW_CHARS)}")
        return "\n".join(lines)

    @staticmethod
    def _bound(name: str, value: str | None) -> tuple[date | None, ToolResult | None]:
        if value is None or not value.strip():
            return None, None
        parsed = _parse_iso(value)
        if parsed is None:
            return None, ToolResult.error(f"Error: {name} must be an ISO date like 2026-10-05.")
        return parsed, None

    @staticmethod
    def _matches(
        text: str,
        when: date | None,
        needle: str,
        after: date | None,
        before: date | None,
    ) -> bool:
        if needle and needle not in text.lower():
            return False
        if after is not None and (when is None or when < after):
            return False
        if before is not None and (when is None or when > before):
            return False
        return True
