"""Memory tools: working state, curated recall, and backup recall."""

# Tool.execute accepts heterogeneous schemas.
# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from mason.agent.tools.base import Tool, ToolResult, tool_parameters
from mason.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from mason.utils.helpers import truncate_text

if TYPE_CHECKING:
    from mason.agent.tools.context import ToolContext
    from mason.memory.state import MemoryState
    from mason.memory.store import MemoryDB

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
        query=StringSchema(
            "What to recall, as a few keywords. Leave empty when using kind."
        ),
        kind=StringSchema(
            "Optional kind filter: fact, preference, decision, project, procedure, event, "
            "summary, or gap. With no query, lists that kind (for example kind=\"gap\" "
            "shows open knowledge gaps).",
        ),
        limit=IntegerSchema(
            description="Maximum number of memories to return (1-20, default 5).",
            minimum=1,
            maximum=20,
        ),
        required=[],
    )
)
class RecallMemoryTool(Tool):
    """Search structured memory, optionally filtered by kind."""

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
            "Search structured memory: durable facts, preferences, decisions, events, "
            "summaries, relationships, and open knowledge gaps. Use it before answering "
            "questions about the user, past decisions, or ongoing work that the working "
            "state does not already cover. Pass kind=\"gap\" with an empty query to list "
            "what you know you do not know."
        )

    async def execute(
        self, query: str = "", kind: str | None = None, limit: int = 5, **kwargs: Any
    ) -> str:
        if not query.strip() and not (kind or "").strip():
            return ToolResult.error(
                "Error: query or kind is required (use recall_journal to list recent turns).",
            )
        hits = self._db.search_memories(query, limit=limit, kind=kind)
        if not hits:
            return "No matching memories."
        try:
            self._db.record_uses([hit.id for hit in hits])
        except Exception:
            # Usage tracking must never break a recall.
            logger.exception("Could not record memory usage")
        lines = [f"Memory matches ({len(hits)}):"]
        for hit in hits:
            flags = ", pinned" if hit.pinned else ""
            status = "" if hit.status == "active" else f", {hit.status}"
            lines.append(f"- [#{hit.id} {hit.kind}{status}{flags}] {hit.text}")
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
class RecallJournalTool(Tool):
    """Search the permanent verbatim journal."""

    def __init__(self, db: MemoryDB):
        self._db = db

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.memory_db is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        db = ctx.memory_db
        if db is None:
            raise RuntimeError("RecallJournalTool requires an initialized memory store")
        return cls(db)

    @property
    def name(self) -> str:
        return "recall_journal"

    @property
    def description(self) -> str:
        return (
            "Search the full verbatim journal: every captured turn, kept indefinitely. "
            "Use it for exact wording, details, or events that structured memory has not "
            "kept. An empty query lists the most recent turns."
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
                return "Nothing in the verbatim journal matches that."
            header = f"Verbatim matches for {query!r} ({len(hits)}):"
        else:
            hits = self._db.recent_episodes(session_key=session, limit=limit)
            if not hits:
                return "The verbatim journal is empty."
            header = f"Latest verbatim turns ({len(hits)}):"
        lines = [header]
        for hit in hits:
            content = truncate_text(hit.content, _HIT_PREVIEW_CHARS)
            lines.append(f"- [{hit.ts}] {hit.session_key} {hit.role}: {content}")
        return "\n".join(lines)
