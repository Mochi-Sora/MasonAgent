"""Daily maintenance for the single memory tier and the working state.

The active database is unbounded: verbatim episodes are the source of truth and
are never discarded automatically. Once a day the gateway writes a compressed,
verbatim-only backup outside ``memory/``, empties the working state in place, and
runs SQLite's own optimisation pass. The structured memory graph is left alone.

A gateway that was offline over midnight runs the same check on startup.
"""

from __future__ import annotations

from datetime import datetime

from loguru import logger

from mason.memory.state import MemoryState
from mason.memory.store import MemoryDB

ROLLOVER_META_KEY = "last_rollover_day"


def current_day(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d")


def maybe_rollover(
    memory_db: MemoryDB,
    memory_state: MemoryState,
    *,
    snapshot_enabled: bool = True,
    snapshot_keep: int = 7,
    now: datetime | None = None,
) -> bool:
    """Run the daily maintenance at most once per day; returns True when it ran.

    The verbatim journal is not purged. *snapshot_enabled* writes the compressed
    verbatim backup before the state is cleared; *snapshot_keep* is how many
    daily backups remain. The working state is emptied in place so its path
    stays stable across days.
    """
    if not memory_db.enabled:
        return False
    today = current_day(now)
    if memory_db.get_meta(ROLLOVER_META_KEY) == today:
        return False
    if snapshot_enabled:
        if memory_db.snapshot(keep=snapshot_keep, now=now) is None:
            logger.warning("Memory backup failed; rollover continues without a backup copy")
    memory_state.clear()
    memory_db.optimize()
    memory_db.set_meta(ROLLOVER_META_KEY, today)
    logger.info(
        "Memory rollover for {}: verbatim journal retained, working state cleared, "
        "backup kept ({})",
        today,
        f"{max(1, int(snapshot_keep))} daily copy" if snapshot_enabled else "disabled",
    )
    return True
