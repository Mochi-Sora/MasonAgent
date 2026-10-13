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
    """Rename ``~/.nanobot`` to ``~/.mason`` once; returns the new path if moved.

    Runs at most once per process, and never overwrites an existing ``~/.mason``
    (if both exist the install is left alone and the caller keeps using the new
    directory).
    """
    global _data_dir_checked
    if _data_dir_checked:
        return None
    _data_dir_checked = True

    target = data_dir()
    source = legacy_data_dir()
    if target.exists() or not source.exists():
        return None
    try:
        shutil.move(str(source), str(target))
    except OSError:
        logger.exception("Could not migrate {} to {}", source, target)
        return None
    logger.info("Migrated legacy data directory {} to {}", source, target)
    return target


def apply_legacy_compat() -> None:
    """Run every legacy shim. Safe to call repeatedly."""
    migrate_legacy_data_dir()
    migrate_legacy_env()
