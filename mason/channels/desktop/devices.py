"""Device ledger for the desktop channel.

Device tokens are sender IDs approved in the pairing store.  This ledger only
remembers which tokens were ever approved, so a hello carrying a revoked token
can be answered with ``reason: "revoked"`` instead of ``"unknown"``.  It
stores no secrets: a token is an opaque random identifier.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, cast

from loguru import logger

from mason.config.paths import get_runtime_subdir

LEDGER_FILENAME = "devices.json"


class DeviceLedger:
    """Tolerant JSON ledger of device tokens that were paired at some point."""

    def __init__(self, path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._path = path

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = get_runtime_subdir("desktop") / LEDGER_FILENAME
        return self._path

    def _load(self) -> set[str]:
        try:
            raw: object = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return set()
        if isinstance(raw, list):
            items: list[object] = cast(list[object], raw)
        elif isinstance(raw, dict):
            stored = cast(dict[str, Any], raw).get("issued", [])
            items = cast(list[object], stored) if isinstance(stored, list) else []
        else:
            return set()
        return {item for item in items if isinstance(item, str) and item}

    def record(self, token: str) -> None:
        """Remember *token* as a device that was approved at least once."""
        if not token:
            return
        with self._lock:
            known = self._load()
            if token in known:
                return
            known.add(token)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(sorted(known), ensure_ascii=False),
                    encoding="utf-8",
                )
                temporary.replace(self.path)
            except OSError:
                logger.warning("desktop: could not persist the device ledger")

    def is_known(self, token: str) -> bool:
        """Return whether *token* was recorded before (approved at some point)."""
        with self._lock:
            return token in self._load()
