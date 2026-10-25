"""Daily maintenance for the single memory tier.

The active database is unbounded: verbatim episodes are the source of truth and
are never discarded automatically. Once a day the gateway writes a compressed,
lossless copy of the whole database outside ``memory/``, and runs SQLite's own
optimisation pass. The structured memory graph and every session's working state
are deliberately left untouched; state is cleared only by an explicit
``/state clear`` command.

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
    memory_state: MemoryState | None = None,
    *,
    snapshot_enabled: bool = True,
    snapshot_keep: int = 7,
    now: datetime | None = None,
) -> bool:
    """Run the daily backup at most once per day; returns True when it ran.

    The verbatim journal and every session state are retained. *snapshot_enabled*
    writes the compressed full backup; *snapshot_keep* is how many daily backups
    remain. ``memory_state`` is accepted for backward compatibility and is never
    modified.
    """
    del memory_state  # State is intentionally retained across rollover.
    if not memory_db.enabled:
        return False
    today = current_day(now)
    if memory_db.get_meta(ROLLOVER_META_KEY) == today:
        return False
    if snapshot_enabled:
        if memory_db.snapshot(keep=snapshot_keep, now=now) is None:
            logger.warning("Memory backup failed; rollover continues without a backup copy")
    memory_db.optimize()
    memory_db.set_meta(ROLLOVER_META_KEY, today)
    logger.info(
        "Memory rollover for {}: verbatim journal and session states retained, "
        "backup kept ({})",
        today,
        f"{max(1, int(snapshot_keep))} daily copy" if snapshot_enabled else "disabled",
    )
    return True
