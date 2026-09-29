"""The model-owned working state file (``memory/state.md``).

The state is a small scratchpad that stays in the system prompt: the main model
rewrites it with the ``update_state`` tool after any turn that changed the
picture of what matters. It is deliberately independent from the SQLite tiers —
losing it is a minor inconvenience, never data loss. The daily rollover empties
it in place; the file itself is never removed.
"""

from __future__ import annotations

from pathlib import Path

from nanobot.utils.helpers import atomic_write_lines

DEFAULT_STATE_MAX_CHARS = 2048


class MemoryState:
    """Reads and writes the bounded, atomically replaced working state."""

    def __init__(self, workspace: Path, *, max_chars: int = DEFAULT_STATE_MAX_CHARS) -> None:
        self.path = workspace / "memory" / "state.md"
        self.max_chars = max(256, int(max_chars))

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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_lines(self.path, text.splitlines(), fsync=True)
        return text, truncated

    def clear(self) -> None:
        """Empty the state in place so the path stays stable across days."""
        self.write("")
