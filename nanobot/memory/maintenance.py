"""Daily rollover for the backup tier and the working state.

Backup episodes are day-scoped: when a new day starts, everything recorded
before it is discarded and the working state is emptied in place. The curated
long-term tier is never touched. The consolidation job gets its chance at the
outgoing day before this runs — see ``nanobot/memory/consolidation.py`` — and a
gateway that was offline over midnight runs the same check on startup.
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger

from nanobot.memory.state import MemoryState
from nanobot.memory.store import MemoryDB

ROLLOVER_META_KEY = "last_rollover_day"


def current_day(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


def maybe_rollover(
    memory_db: MemoryDB,
    memory_state: MemoryState,
    *,
    consolidation_enabled: bool = True,
    now: datetime | None = None,
) -> bool:
    """Run the daily rollover at most once per day; returns True when it ran.

    With consolidation enabled the purge stops at the consolidation cursor, so
    backup the job has not considered yet survives until it has. With it
disabled the backup tier is strictly day-scoped.
    """
    if not memory_db.enabled:
        return False
    today = current_day(now)
    if memory_db.get_meta(ROLLOVER_META_KEY) == today:
        return False
    purge_through = memory_db.consolidation_cursor() if consolidation_enabled else None
    removed = memory_db.rollover(today, up_to_id=purge_through)
    memory_state.clear()
    memory_db.set_meta(ROLLOVER_META_KEY, today)
    logger.info("Memory rollover for {}: {} backup episodes discarded", today, removed)
    return True
