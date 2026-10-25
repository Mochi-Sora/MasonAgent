"""Memory tools: session state, curated recall, and backup recall."""

# Tool.execute accepts heterogeneous schemas.
# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from mason.agent.tools.base import Tool, ToolResult, tool_parameters
from mason.agent.tools.context import current_request_session_key
from mason.agent.tools.schema import IntegerSchema, StringSchema, tool_parameters_schema
from mason.memory.state import MemoryState, MemoryStateStore, SessionState
from mason.utils.helpers import truncate_text

if TYPE_CHECKING:
    from mason.agent.tools.context import ToolContext
    from mason.memory.store import MemoryDB

_HIT_PREVIEW_CHARS = 1_200


def _target_session(target_session: str | None) -> str | None:
    """Resolve the target session for a state tool call."""
    target = (target_session or "").strip()
    if target:
        return target
    return current_request_session_key()


def _state_for(
    store: MemoryStateStore | None,
    fallback: MemoryState | None,
    session_key: str | None,
) -> MemoryState | None:
    if store is not None:
        return store.for_session(session_key)
    if session_key is not None and fallback is not None and fallback.session_key != session_key:
        return None
    return fallback


@tool_parameters(
    tool_parameters_schema(
        current_goal=StringSchema(
            "What this session is trying to accomplish right now. "
            "Omit to keep the previous value; use an empty string to clear it.",
            nullable=True,
        ),
        done=StringSchema(
            "Durable work completed in this session. "
            "Omit to keep the previous value; use an empty string to clear it.",
            nullable=True,
        ),
        next_steps=StringSchema(
            "The next concrete step, or an empty string when there is none. "
            "Omit to keep the previous value.",
            nullable=True,
        ),
        session_summary=StringSchema(
            "What the session is about right now. "
            "Omit to keep the previous value; use an empty string to clear it.",
            nullable=True,
        ),
        target_session=StringSchema(
            "Optional session key to inspect or edit another session's state. "
            "Omit for the current session. Use search_sessions to find a key.",
            nullable=True,
        ),
        required=[],
    )
)
class UpdateStateTool(Tool):
    """Edit one session's framework-maintained working state."""

    def __init__(
        self,
        store: MemoryStateStore | None = None,
        fallback: MemoryState | None = None,
    ):
        self._store = store
        self._fallback = fallback

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.memory_state_store is not None or ctx.memory_state is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.memory_state_store is None and ctx.memory_state is None:
            raise RuntimeError("UpdateStateTool requires an initialized state store")
        return cls(store=ctx.memory_state_store, fallback=ctx.memory_state)

    @property
    def name(self) -> str:
        return "update_state"

    @property
    def description(self) -> str:
        return (
            "Edit the framework-maintained state (up to 4 KB) for this session, or for "
            "another session named by target_session. The state has four sections: current "
            "goal, what has been done, what is next, and what the session is about. "
            "The framework updates it after every turn; use this only for corrections "
            "or explicit user changes. Load it first with "
            "find_capabilities(need=\"edit session state\")."
        )

    async def execute(
        self,
        current_goal: str | None = None,
        done: str | None = None,
        next_steps: str | None = None,
        session_summary: str | None = None,
        target_session: str | None = None,
        **kwargs: Any,
    ) -> str:
        session_key = _target_session(target_session)
        state = _state_for(self._store, self._fallback, session_key)
        if state is None:
            return ToolResult.error(
                "Error: no state store is available for that session.",
            )
        if all(value is None for value in (current_goal, done, next_steps, session_summary)):
            return ToolResult.error(
                "Error: provide at least one state section. "
                "Use /state clear to clear the whole state explicitly.",
            )

        previous = state.read_sections()
        updated = SessionState(
            current_goal=previous.current_goal if current_goal is None else current_goal,
            done=previous.done if done is None else done,
            next_steps=previous.next_steps if next_steps is None else next_steps,
            session_summary=(
                previous.session_summary
                if session_summary is None
                else session_summary
            ),
        ).normalized()
        if updated.is_empty():
            return ToolResult.error(
                "Error: the update would empty the state. "
                "Use /state clear to clear it explicitly.",
            )

        try:
            stored, truncated = state.write_sections(updated)
        except (OSError, ValueError) as exc:
            logger.opt(exception=True).warning("Could not update session state: {}", exc)
            return ToolResult.error(f"Error: could not update session state: {exc}")

        target = session_key or "the current session"
        suffix = " (truncated)" if truncated else ""
        return (
            f"Session state updated for {target} "
            f"({len(stored)}/{state.max_chars} chars{suffix})."
        )


@tool_parameters(
    tool_parameters_schema(
        target_session=StringSchema(
            "Optional session key to read another session's state. "
            "Omit for the current session.",
            nullable=True,
        ),
        required=[],
    )
)
class ReadStateTool(Tool):
    """Read one session's framework-maintained working state."""

    def __init__(
        self,
        store: MemoryStateStore | None = None,
        fallback: MemoryState | None = None,
    ):
        self._store = store
        self._fallback = fallback

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.memory_state_store is not None or ctx.memory_state is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        if ctx.memory_state_store is None and ctx.memory_state is None:
            raise RuntimeError("ReadStateTool requires an initialized state store")
        return cls(store=ctx.memory_state_store, fallback=ctx.memory_state)

    @property
    def name(self) -> str:
        return "read_state"

    @property
    def description(self) -> str:
        return (
            "Read the framework-maintained state for this session or another session "
            "named by target_session. The current session's state is already in your "
            "prompt; use this only to inspect a different session."
        )

    async def execute(
        self,
        target_session: str | None = None,
        **kwargs: Any,
    ) -> str:
        session_key = _target_session(target_session)
        state = _state_for(self._store, self._fallback, session_key)
        if state is None:
            return ToolResult.error(
                "Error: no state store is available for that session.",
            )
        content = state.read()
        if not content:
            return f"No state is stored for {session_key or 'the current session'}."
        return content


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
