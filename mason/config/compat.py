"""One-time compatibility shims for installs that predate the Mason rename.

The framework was previously named ``nanobot``: runtime data lived under
``~/.nanobot`` and configuration read ``NANOBOT_*`` environment variables. Both
moved to ``mason``. Without migration an existing install would start with an
empty data directory, so the legacy directory is renamed once and legacy
environment variables are mirrored onto their new names.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from loguru import logger

DATA_DIR_NAME = ".mason"
LEGACY_DATA_DIR_NAME = ".nanobot"
ENV_PREFIX = "MASON_"
LEGACY_ENV_PREFIX = "NANOBOT_"

# Install artifacts that must not move: relocating a checkout invalidates the
# editable install, and a virtualenv bakes in absolute paths (it would break).
LEGACY_KEEP_NAMES = frozenset({"src", "venv", ".venv", ".git", "node_modules"})

_data_dir_checked = False


def data_dir() -> Path:
    """Return the (current) runtime data directory for this user."""
    return Path.home() / DATA_DIR_NAME


def legacy_data_dir() -> Path:
    """Return the pre-rename runtime data directory."""
    return Path.home() / LEGACY_DATA_DIR_NAME


def migrate_legacy_env(environ: dict[str, str] | None = None) -> list[str]:
    """Mirror ``NANOBOT_*`` onto ``MASON_*`` where the new name is unset.

    Returns the names that were added. Existing ``MASON_*`` values always win,
    so an explicitly configured install is never overwritten.
    """
    env = os.environ if environ is None else environ
    migrated: list[str] = []
    for key, value in list(env.items()):
        if not key.startswith(LEGACY_ENV_PREFIX):
            continue
        target = ENV_PREFIX + key[len(LEGACY_ENV_PREFIX) :]
        if target in env:
            continue
        env[target] = value
        migrated.append(target)
    return migrated


def migrate_legacy_data_dir() -> Path | None:
    """Move runtime data from ``~/.nanobot`` to ``~/.mason`` once.

    Only runtime entries move. A checkout (``src/``) and a virtualenv stay put
    because moving them breaks the editable install and the venv's absolute
    paths; the legacy directory is left in place holding just those. Never
    overwrites an existing ``~/.mason`` entry. Returns the new data dir when
    anything moved, otherwise ``None``.
    """
    global _data_dir_checked
    if _data_dir_checked:
        return None
    _data_dir_checked = True

    target = data_dir()
    source = legacy_data_dir()
    if target.exists() or not source.is_dir():
        return None
    movable = [entry for entry in source.iterdir() if entry.name not in LEGACY_KEEP_NAMES]
    if not movable:
        return None
    target.mkdir(parents=True, exist_ok=True)
    moved = False
    for entry in movable:
        destination = target / entry.name
        if destination.exists():
            continue
        try:
            shutil.move(str(entry), str(destination))
            moved = True
        except OSError:
            logger.exception("Could not migrate {} to {}", entry, destination)
    if not moved:
        return None
    logger.info(
        "Migrated runtime data from {} to {}; left {} in place",
        source,
        target,
        ", ".join(sorted(LEGACY_KEEP_NAMES)),
    )
    return target


def apply_legacy_compat() -> None:
    """Run every legacy shim. Safe to call repeatedly."""
    migrate_legacy_data_dir()
    migrate_legacy_env()
