"""The model-owned working state file (``memory/state.md``).

The state is a small scratchpad that stays in the system prompt: the main model
rewrites it with the ``update_state`` tool after any turn that changed the
picture of what matters. It is deliberately independent from the SQLite tiers —
losing it is a minor inconvenience, never data loss. The daily rollover empties
it in place; the file itself is never removed.

Every overwrite first archives the outgoing text to ``memory/state_history.md``
(newest first, bounded), so a rewrite that drops a line is recoverable even
though only the current state ever reaches the prompt.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from loguru import logger

from nanobot.utils.helpers import atomic_write_lines

DEFAULT_STATE_MAX_CHARS = 2048
DEFAULT_STATE_HISTORY_VERSIONS = 20
_STATE_HISTORY_FILENAME = "state_history.md"


class MemoryState:
    """Reads and writes the bounded, atomically replaced working state."""

    def __init__(
        self,
        workspace: Path,
        *,
        max_chars: int = DEFAULT_STATE_MAX_CHARS,
        history_versions: int = DEFAULT_STATE_HISTORY_VERSIONS,
    ) -> None:
        self.path = workspace / "memory" / "state.md"
        self.max_chars = max(256, int(max_chars))
        self.history_path = self.path.parent / _STATE_HISTORY_FILENAME
        self.history_versions = max(0, int(history_versions))

    def read(self) -> str:
        """Return the current state, or an empty string when absent/unreadable."""
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def write(self, content: str) -> tuple[str, bool]:
        """Replace the state; returns ``(stored_text, truncated)``."""
        text = content.strip()
        truncated = len(text) > self.max_chars
        if truncated:
            text = text[: self.max_chars].rstrip()
        previous = self.read()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_lines(self.path, text.splitlines(), fsync=True)
        if previous and previous != text:
            self._archive(previous)
        if truncated:
            logger.warning(
                "Working state truncated at {} chars ({} dropped)",
                self.max_chars,
                len(text) - self.max_chars,
            )
        return text, truncated

    def clear(self) -> None:
        """Empty the state in place so the path stays stable across days."""
        self.write("")

    # -- undo history ---------------------------------------------------------

    def _archive(self, previous: str) -> None:
        """Prepend the outgoing state to the bounded history file."""
        if self.history_versions <= 0:
            return
        stamp = datetime.now().isoformat(timespec="seconds")
        blocks = self._history_blocks()
        blocks.insert(0, f"## {stamp}\n\n{previous}")
        kept = blocks[: self.history_versions]
        try:
            atomic_write_lines(
                self.history_path,
                "\n\n".join(kept).splitlines(),
                fsync=False,
            )
        except OSError:
            logger.exception("Could not archive working state to {}", self.history_path)

    def _history_blocks(self) -> list[str]:
        """Return existing archived states, newest first."""
        try:
            text = self.history_path.read_text(encoding="utf-8")
        except OSError:
            return []
        blocks: list[list[str]] = []
        current: list[str] | None = None
        for line in text.splitlines():
            if line.startswith("## "):
                current = [line]
                blocks.append(current)
            elif current is not None:
                current.append(line)
        return ["\n".join(block).rstrip() for block in blocks if block]
