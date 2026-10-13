"""One-time compatibility shims for installs that predate the Mason rename.

The framework was previously named ``nanobot``: runtime data lived under
``~/.nanobot`` and configuration read ``NANOBOT_*`` environment variables. Both
moved to ``mason``. Without migration an existing install would start with an
empty data directory, so the legacy directory is renamed once and legacy
environment variables are mirrored onto their new names.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import cast

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


def _rewrite_legacy_paths(value: object) -> tuple[object, int]:
    """Rewrite strings that point into the legacy data directory."""
    if isinstance(value, str):
        rewritten = value.replace(f"~/{LEGACY_DATA_DIR_NAME}", f"~/{DATA_DIR_NAME}")
        rewritten = rewritten.replace(str(legacy_data_dir()), str(data_dir()))
        return (rewritten, 1) if rewritten != value else (value, 0)
    if isinstance(value, dict):
        source = cast("dict[object, object]", value)
        result: dict[object, object] = {}
        count = 0
        for key, item in source.items():
            new_item, item_count = _rewrite_legacy_paths(item)
            result[key] = new_item
            count += item_count
        return result, count
    if isinstance(value, list):
        result_list: list[object] = []
        count = 0
        for item in cast("list[object]", value):
            new_item, item_count = _rewrite_legacy_paths(item)
            result_list.append(new_item)
            count += item_count
        return result_list, count
    return value, 0


def rewrite_migrated_config(path: Path) -> int:
    """Repoint config values that still reference ``~/.nanobot``.

    A stored ``workspace`` path would otherwise keep pointing at the old
    location, so the agent would start with a fresh, empty workspace while the
    real one sits unused under ``~/.mason``.
    """
    try:
        payload: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, dict):
        return 0
    rewritten, count = _rewrite_legacy_paths(cast(object, payload))
    if not count:
        return 0
    try:
        path.write_text(json.dumps(rewritten, indent=2) + "\n", encoding="utf-8")
    except OSError:
        logger.exception("Could not rewrite migrated config {}", path)
        return 0
    logger.info("Repointed {} legacy path(s) in {}", count, path)
    return count


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
    if not source.is_dir():
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
    config_path = target / "config.json"
    if config_path.is_file():
        rewrite_migrated_config(config_path)
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
