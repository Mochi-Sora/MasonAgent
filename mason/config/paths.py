"""Runtime path helpers derived from the active config context."""

from __future__ import annotations

from pathlib import Path

from mason.config.compat import migrate_legacy_data_dir
from mason.utils.helpers import ensure_dir


def _home() -> Path:
    """Home directory, after running the one-time legacy data-dir migration."""
    migrate_legacy_data_dir()
    return Path.home()


def get_config_path() -> Path:
    """Get the configuration file path (lazy import to break circular dependency).

    Delegates to ``mason.config.loader.get_config_path`` at call time so
    that importing this module never triggers a circular import during startup.
    """
    from mason.config.loader import get_config_path as _loader_get_config_path
    return _loader_get_config_path()


def get_data_dir() -> Path:
    """Return the instance-level runtime data directory."""
    return ensure_dir(get_config_path().parent)


def get_runtime_subdir(name: str) -> Path:
    """Return a named runtime subdirectory under the instance data dir."""
    return ensure_dir(get_data_dir() / name)


def get_media_dir(channel: str | None = None) -> Path:
    """Return the media directory, optionally namespaced per channel."""
    base = get_runtime_subdir("media")
    return ensure_dir(base / channel) if channel else base


def get_cron_dir() -> Path:
    """Return the cron storage directory."""
    return get_runtime_subdir("cron")


def get_logs_dir() -> Path:
    """Return the logs directory."""
    return get_runtime_subdir("logs")


def get_workspace_path(workspace: str | Path | None = None) -> Path:
    """Resolve and ensure the agent workspace path."""
    if workspace:
        path = Path(workspace).expanduser()
    else:
        path = _home() / ".mason" / "workspace"
    return ensure_dir(path)


def is_default_workspace(workspace: str | Path | None) -> bool:
    """Return whether a workspace resolves to mason's default workspace path."""
    default = _home() / ".mason" / "workspace"
    current = Path(workspace).expanduser() if workspace is not None else default
    return current.resolve(strict=False) == default.resolve(strict=False)


def get_cli_history_path() -> Path:
    """Return the shared CLI history file path."""
    return _home() / ".mason" / "history" / "cli_history"


def get_legacy_sessions_dir() -> Path:
    """Return the legacy global session directory used for migration fallback."""
    return _home() / ".mason" / "sessions"
