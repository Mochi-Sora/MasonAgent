"""Memory tools: working state, curated recall, and backup recall."""

# Tool.execute accepts heterogeneous schemas.
# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from nanobot.utils.helpers import truncate_text

if TYPE_CHECKING:
    from nanobot.agent.tools.context import ToolContext
    from nanobot.memory.state import MemoryState
    from nanobot.memory.store import MemoryDB

_HIT_PREVIEW_CHARS = 1_200


@tool_parameters(
    tool_parameters_schema(
        content=StringSchema(
            "Full replacement text for the working state. Keep it compact (about 2 KB): "
            "active goals, decisions, user preferences, open questions. Use an empty "
            "string to clear it.",
        ),
        required=["content"],
    )
)
class UpdateStateTool(Tool):
    """Replace the small working state that stays in the system prompt."""

    def __init__(self, state: MemoryState):
        self._state = state

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.memory_state is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        state = ctx.memory_state
        if state is None:
            raise RuntimeError("UpdateStateTool requires an initialized memory state")
        return cls(state)

    @property
    def name(self) -> str:
        return "update_state"

    @property
    def description(self) -> str:
        return (
            "Replace the working state shown in your system prompt. Call this when the "
            "picture of what matters changed: new goals, decisions, preferences, or "
            "corrections. Keep it compact. This is your short-lived scratchpad; durable "
            "memory is curated automatically from the backup."
        )

    async def execute(self, content: str, **kwargs: Any) -> str:
        stored, truncated = self._state.write(content)
        if not stored:
            return "Working state cleared."
        suffix = " (truncated)" if truncated else ""
        return f"Working state updated ({len(stored)}/{self._state.max_chars} chars{suffix})."


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema("What to recall, as a few keywords"),
        limit=IntegerSchema(
            description="Maximum number of memories to return (1-20, default 5).",
            minimum=1,
            maximum=20,
        ),
        required=["query"],
    )
)
class RecallMemoryTool(Tool):
    """Search the curated long-term memory tier."""

    def __init__(self, db: MemoryDB):
        self._db = db

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.memory_db is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        db = ctx.memory_db
        if db is None:
            raise RuntimeError("RecallMemoryTool requires an initialized memory store")
        return cls(db)

    @property
    def name(self) -> str:
        return "recall_memory"

    @property
    def description(self) -> str:
        return (
            "Search curated long-term memory: durable facts, preferences, and decisions "
            "kept across sessions. Use it before answering questions about the user, past "
            "decisions, or ongoing work that the working state does not already cover."
        )

    async def execute(self, query: str, limit: int = 5, **kwargs: Any) -> str:
        if not query.strip():
            return ToolResult.error(
                "Error: query is required (use recall_backup to list recent turns).",
            )
        hits = self._db.search_memories(query, limit=limit)
        if not hits:
            return "No matching long-term memories."
        try:
            self._db.record_uses([hit.id for hit in hits])
        except Exception:
            # Usage tracking must never break a recall.
            logger.exception("Could not record memory usage")
        lines = [f"Long-term memory matches for {query!r} ({len(hits)}):"]
        for hit in hits:
            flags = ", pinned" if hit.pinned else ""
            lines.append(f"- [#{hit.id} {hit.kind}{flags}] {hit.text}")
        return "\n".join(lines)


@tool_parameters(
    tool_parameters_schema(
        query=StringSchema(
            "Keywords to search for. Leave empty to list the most recent turns.",
        ),
        session=StringSchema(
            "Optional session key to restrict the search to a single conversation.",
        ),
        limit=IntegerSchema(
            description="Maximum number of turns to return (1-20, default 10).",
            minimum=1,
            maximum=20,
        ),
        required=[],
    )
)
class RecallBackupTool(Tool):
    """Search the recent verbatim turn-by-turn backup."""

    def __init__(self, db: MemoryDB):
        self._db = db

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.memory_db is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        db = ctx.memory_db
        if db is None:
            raise RuntimeError("RecallBackupTool requires an initialized memory store")
        return cls(db)

    @property
    def name(self) -> str:
        return "recall_backup"

    @property
    def description(self) -> str:
        return (
            "Search your recent backup of conversations: every turn from the last few "
            "days, captured verbatim. Use it for exact recent wording, details, or "
            "events that long-term memory has not kept. Older days are discarded over "
            "time."
        )

    async def execute(
        self,
        query: str = "",
        session: str | None = None,
        limit: int = 10,
        **kwargs: Any,
    ) -> str:
        if query.strip():
            hits = self._db.search_episodes(query, session_key=session, limit=limit)
            if not hits:
                return "Nothing in the recent backup matches that."
            header = f"Backup matches for {query!r} ({len(hits)}):"
        else:
            hits = self._db.recent_episodes(session_key=session, limit=limit)
            if not hits:
                return "The recent backup is empty."
            header = f"Latest backup turns ({len(hits)}):"
        lines = [header]
        for hit in hits:
            content = truncate_text(hit.content, _HIT_PREVIEW_CHARS)
            lines.append(f"- [{hit.ts}] {hit.session_key} {hit.role}: {content}")
        return "\n".join(lines)
