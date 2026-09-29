"""Auto compact: size-gated compression for idle sessions.

Idle compaction is a backup, not a routine. It only runs for sessions whose
next prompt would consume a large share of the model's context window, and the
summary it produces is a handoff for the run that consumed it: it is never
re-injected into later prompts. The steady-state prompt stays identity plus the
working state; anything older is recalled on demand from the memory tiers.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from loguru import logger

from nanobot.events import NO_EVENTS, EventSink
from nanobot.session.manager import Session, SessionManager
from nanobot.session.summary import SessionSummary, is_summary_checkpoint

if TYPE_CHECKING:
    from nanobot.agent.memory import Consolidator
    from nanobot.utils.llm_runtime import LLMRuntime

SessionEventFactory = Callable[[str], EventSink]

#: An idle session is summarized only when its next prompt would otherwise
#: consume at least this share of the model's context window. On large windows
#: compaction stays dormant until a session is genuinely heavy.
AUTO_COMPACT_WINDOW_FRACTION = 0.5


class AutoCompact:
    def __init__(self, sessions: SessionManager, consolidator: Consolidator,
                 session_ttl_minutes: int = 0,
                 bind_events: SessionEventFactory | None = None):
        self.sessions = sessions
        self.consolidator = consolidator
        self._ttl = session_ttl_minutes
        self._archiving: set[str] = set()
        self._bind_events = bind_events

    def _is_expired(self, ts: datetime | str | None,
                    now: datetime | None = None) -> bool:
        if self._ttl <= 0 or not ts:
            return False
        try:
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            current = now or datetime.now()
            if getattr(ts, "tzinfo", None) is not None or current.tzinfo is not None:
                idle_seconds = current.timestamp() - ts.timestamp()
            else:
                idle_seconds = (current - ts).total_seconds()
        except (OSError, OverflowError, TypeError, ValueError):
            # list_sessions() forwards raw persisted metadata; an unusable value
            # must not escape the idle scan and stop the agent loop.
            return False
        return idle_seconds >= self._ttl * 60

    def _has_unarchived_messages(self, key: str) -> bool:
        session = self.sessions.get_or_create(key)
        return any(
            not message.get("_command") and not is_summary_checkpoint(message)
            for message in session.messages[session.last_archived:]
        )

    def _over_window_fraction(self, session: Session, runtime: LLMRuntime) -> bool:
        """Return whether the session's next prompt is heavy enough to compact."""
        try:
            window = int(getattr(runtime, "context_window_tokens", 0) or 0)
            if window <= 0:
                return False
            threshold = max(1, int(window * AUTO_COMPACT_WINDOW_FRACTION))
            estimated, _source = self.consolidator.estimate_session_prompt_tokens(
                session,
                runtime=runtime,
            )
            return int(estimated) >= threshold
        except Exception:
            # An unmeasurable session must not block the idle scan; it simply
            # never qualifies for the backup compaction.
            logger.exception("Auto-compact: size estimate failed for {}", session.key)
            return False

    def check_expired(
        self,
        schedule_background: Callable[[Coroutine[Any, Any, None]], None],
        resolve_runtime: Callable[[Session], LLMRuntime],
        active_session_keys: Collection[str] = (),
    ) -> None:
        """Schedule archival for idle sessions that are also over the size threshold."""
        now = datetime.now()
        for info in self.sessions.list_sessions():
            key = info.get("key", "")
            if not key or key in self._archiving:
                continue
            if key in active_session_keys:
                continue
            updated_at = info.get("updated_at")
            if not (self._is_expired(updated_at, now) and self._has_unarchived_messages(key)):
                continue
            session = self.sessions.get_or_create(key)
            try:
                runtime = resolve_runtime(session)
            except (KeyError, ValueError):
                # Invalid session selections remain recoverable through /model.
                continue
            if not self._over_window_fraction(session, runtime):
                logger.debug(
                    "Auto-compact: {} is idle but under the size threshold; skipping",
                    key,
                )
                continue
            self._archiving.add(key)
            schedule_background(self._archive(key, runtime=runtime))

    async def _archive(self, key: str, *, runtime: LLMRuntime) -> None:
        try:
            await self.consolidator.compact_idle_session(
                key,
                runtime=runtime,
                events=self._bind_events(key) if self._bind_events else NO_EVENTS,
            )
        except Exception:
            logger.exception("Auto-compact: failed for {}", key)
        finally:
            self._archiving.discard(key)

    def prepare_session(self, session: Session, key: str) -> tuple[Session, SessionSummary | None]:
        """Return the session to use for a new turn.

        The second element is a one-turn handoff slot that stays empty: archived
        summaries are never re-injected into later prompts, so the system prompt
        is identity plus working state at all times. A session that was archived
        in the background is reloaded so the turn sees the new boundary.
        """
        if key in self._archiving or self._is_expired(session.updated_at):
            logger.info("Auto-compact: reloading session {} (archiving={})", key, key in self._archiving)
            session = self.sessions.get_or_create(key)
        return session, None
