"""Framework-maintained, session-scoped working state.

Every session owns one small state file with four fixed sections:

* ``Current goal`` — what this session is trying to accomplish.
* ``What has been done`` — the durable work completed so far.
* ``What's next`` — the next concrete step, when there is one.
* ``Session state`` — what the conversation is about right now.

The framework refreshes the state after each completed turn. The model can
correct it through ``update_state`` (hidden behind capability discovery), but it
is not responsible for keeping it current.

The state is never cleared by rollover or session maintenance. Only the explicit
``/state clear`` command (or an explicit tool call for a given session) empties
it. Every overwrite archives the outgoing text to a per-session JSONL history so
an accidental rewrite can be inspected.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence, cast

from filelock import FileLock
from loguru import logger

from mason.utils.helpers import atomic_write_lines

DEFAULT_STATE_MAX_CHARS = 4096
DEFAULT_STATE_HISTORY_VERSIONS = 20
_STATE_DIRNAME = "state"
_LEGACY_STATE_FILENAME = "state.md"
_STATE_HISTORY_SUFFIX = ".history.jsonl"
_STATE_LOCK_SUFFIX = ".lock"
_TRUNCATED_SUFFIX = "\n... (truncated)"

#: Canonical section order used by rendering, parsing, and the tool schema.
STATE_SECTION_KEYS: tuple[str, ...] = (
    "current_goal",
    "done",
    "next_steps",
    "session_summary",
)

STATE_SECTION_TITLES: dict[str, str] = {
    "current_goal": "Current goal",
    "done": "What has been done",
    "next_steps": "What's next",
    "session_summary": "Session state",
}

_STATE_HEADING_TO_KEY: dict[str, str] = {
    title.casefold(): key for key, title in STATE_SECTION_TITLES.items()
}
_STATE_HEADING_TO_KEY.update({
    "session": "session_summary",
    "what's next to do": "next_steps",
    "what has been done": "done",
    "what's been done": "done",
})


@dataclass(frozen=True, slots=True)
class SessionState:
    """The four sections of one session's working state."""

    current_goal: str = ""
    done: str = ""
    next_steps: str = ""
    session_summary: str = ""

    def normalized(self) -> "SessionState":
        return SessionState(
            current_goal=self.current_goal.strip(),
            done=self.done.strip(),
            next_steps=self.next_steps.strip(),
            session_summary=self.session_summary.strip(),
        )

    def is_empty(self) -> bool:
        value = self.normalized()
        return not any(getattr(value, key) for key in STATE_SECTION_KEYS)

    def with_preserved(self, previous: "SessionState") -> "SessionState":
        """Keep the previous value for sections this update left blank."""
        current = self.normalized()
        prior = previous.normalized()
        return SessionState(
            current_goal=current.current_goal or prior.current_goal,
            done=current.done or prior.done,
            next_steps=current.next_steps or prior.next_steps,
            session_summary=current.session_summary or prior.session_summary,
        )

    def as_dict(self) -> dict[str, str]:
        value = self.normalized()
        return {key: getattr(value, key) for key in STATE_SECTION_KEYS}


@dataclass(frozen=True, slots=True)
class StateVersion:
    """One archived working-state snapshot."""

    recorded_at: str
    content: str


def render_session_state(
    state: SessionState | Iterable[tuple[str, str]],
    *,
    max_chars: int = DEFAULT_STATE_MAX_CHARS,
) -> tuple[str, bool]:
    """Render the four sections and enforce the character cap.

    Sections are trimmed proportionally instead of cutting the rendered document
    blindly, so a long "done" section cannot erase the current goal. Returns the
    rendered text and whether anything was truncated.
    """
    if isinstance(state, SessionState):
        sections = state.as_dict()
    else:
        sections = {key: value.strip() for key, value in state if key in STATE_SECTION_KEYS}

    populated = [
        (key, sections.get(key, "").strip())
        for key in STATE_SECTION_KEYS
        if sections.get(key, "").strip()
    ]
    if not populated:
        return "", False

    def render(items: Sequence[tuple[str, str]]) -> str:
        return "\n\n".join(
            f"## {STATE_SECTION_TITLES[key]}\n\n{value}" for key, value in items
        )

    rendered = render(populated)
    if len(rendered) <= max_chars:
        return rendered, False

    headings = sum(
        len(f"## {STATE_SECTION_TITLES[key]}") + 4 for key, _ in populated
    )
    separators = max(0, len(populated) - 1) * 2
    content_budget = max(0, max_chars - headings - separators)
    per_section = max(1, content_budget // len(populated))
    trimmed = [
        (key, _truncate_to(value, per_section)) for key, value in populated
    ]
    rendered = render(trimmed)
    if len(rendered) > max_chars:
        rendered = _truncate_to(rendered, max_chars)
    return rendered, True


def _truncate_to(text: str, limit: int) -> str:
    """Truncate *text* so the result never exceeds *limit* characters."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= len(_TRUNCATED_SUFFIX):
        return text[:limit]
    return text[: limit - len(_TRUNCATED_SUFFIX)].rstrip() + _TRUNCATED_SUFFIX


def parse_session_state(text: str) -> SessionState:
    """Parse the rendered section format; unknown content falls back to the summary."""
    raw = text.strip()
    if not raw:
        return SessionState()

    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in raw.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip().casefold()
            key = _STATE_HEADING_TO_KEY.get(heading)
            if key is not None:
                current = key
                sections.setdefault(current, [])
                continue
        if current is not None:
            sections[current].append(line)

    if not sections:
        return SessionState(session_summary=raw)

    return SessionState(
        current_goal="\n".join(sections.get("current_goal", [])).strip(),
        done="\n".join(sections.get("done", [])).strip(),
        next_steps="\n".join(sections.get("next_steps", [])).strip(),
        session_summary="\n".join(sections.get("session_summary", [])).strip(),
    )


def state_session_token(session_key: str) -> str:
    """Return a reversible, path-safe filename token for a session key."""
    encoded = base64.urlsafe_b64encode(session_key.encode("utf-8", errors="replace"))
    return encoded.decode("ascii").rstrip("=")


def session_key_from_state_token(token: str) -> str | None:
    """Decode a token produced by :func:`state_session_token`."""
    if not token:
        return None
    try:
        padding = 4 - len(token) % 4
        if padding != 4:
            token += "=" * padding
        key = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError, TypeError):
        return None
    if state_session_token(key) != token.rstrip("="):
        return None
    return key


class MemoryState:
    """Reads and writes one session's bounded, atomically replaced state.

    ``session_key=None`` keeps the legacy workspace-global path for migration and
    SDK compatibility. New callers should always pass a session key.
    """

    def __init__(
        self,
        workspace: Path,
        *,
        session_key: str | None = None,
        max_chars: int = DEFAULT_STATE_MAX_CHARS,
        history_versions: int = DEFAULT_STATE_HISTORY_VERSIONS,
    ) -> None:
        self.workspace = Path(workspace)
        self.session_key = session_key
        self.max_chars = max(1, int(max_chars))
        self.history_versions = max(0, int(history_versions))

        if session_key:
            token = state_session_token(session_key)
            directory = self.workspace / "memory" / _STATE_DIRNAME
            self.path = directory / f"{token}.md"
            self.history_path = directory / f"{token}{_STATE_HISTORY_SUFFIX}"
        else:
            self.path = self.workspace / "memory" / _LEGACY_STATE_FILENAME
            self.history_path = self.workspace / "memory" / "state_history.jsonl"
        self._lock = FileLock(f"{self.path}{_STATE_LOCK_SUFFIX}", timeout=10)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def label(self) -> str:
        return self.session_key or "global"

    def read(self) -> str:
        """Return the current rendered state, or an empty string when absent."""
        try:
            return self.path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def read_sections(self) -> SessionState:
        return parse_session_state(self.read())

    def versions(self, *, limit: int = DEFAULT_STATE_HISTORY_VERSIONS) -> list[StateVersion]:
        """Return archived states, newest first, bounded by *limit*."""
        if limit <= 0:
            return []
        return self._read_versions(limit=limit)

    def write(self, content: str) -> tuple[str, bool]:
        """Replace the state with raw text; returns ``(stored_text, truncated)``.

        Raw writes are retained for the SDK and migration paths. New code should
        use :meth:`write_sections` so the four-section contract is preserved.
        """
        stored = content.strip()
        truncated = len(stored) > self.max_chars
        stored = _truncate_to(stored, self.max_chars) if truncated else stored
        with self._lock:
            previous = self.read()
            self._write_text(stored)
            if previous and previous != stored:
                self._archive(previous)
        if truncated:
            logger.warning(
                "Session state for {} truncated to {} chars",
                self.label,
                self.max_chars,
            )
        return stored, truncated

    def write_sections(self, state: SessionState) -> tuple[str, bool]:
        """Replace the state with the four-section format.

        An all-empty update is rejected so an automatic writer cannot erase the
        section wholesale; explicit clearing goes through :meth:`clear`.
        """
        rendered, truncated = render_session_state(state, max_chars=self.max_chars)
        if not rendered:
            raise ValueError("state update is empty; use the explicit clear command")
        with self._lock:
            previous = self.read()
            if previous == rendered:
                return rendered, False
            self._write_text(rendered)
            if previous:
                self._archive(previous)
        return rendered, truncated

    def write_sections_if_unchanged(
        self,
        expected_previous: str,
        state: SessionState,
    ) -> tuple[str, bool] | None:
        """Write only when the state still matches the writer's input snapshot.

        This prevents a slow framework writer from restoring content after the
        user explicitly cleared the state (or another process replaced it).
        """
        rendered, truncated = render_session_state(state, max_chars=self.max_chars)
        if not rendered:
            return None
        with self._lock:
            current = self.read()
            if current != expected_previous.strip():
                return None
            if current == rendered:
                return rendered, False
            self._write_text(rendered)
            if current:
                self._archive(current)
        return rendered, truncated

    def clear(self) -> None:
        """Explicitly empty the state in place; the file itself is retained."""
        with self._lock:
            previous = self.read()
            self._write_text("")
            if previous:
                self._archive(previous)
        logger.info("Session state cleared for {}", self.label)

    # -- internals -----------------------------------------------------------

    def _write_text(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_lines(self.path, text.splitlines(), fsync=True)

    def _archive(self, previous: str) -> None:
        if self.history_versions <= 0:
            return
        stamp = datetime.now().isoformat(timespec="seconds")
        versions = self._read_versions(limit=self.history_versions - 1)
        versions.insert(0, StateVersion(stamp, previous))
        lines = [
            json.dumps(
                {"recorded_at": version.recorded_at, "content": version.content},
                ensure_ascii=False,
            )
            for version in versions
        ]
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_lines(self.history_path, lines, fsync=False)
        except OSError:
            logger.exception(
                "Could not archive session state for {} to {}",
                self.label,
                self.history_path,
            )

    def _read_versions(self, *, limit: int) -> list[StateVersion]:
        try:
            raw = self.history_path.read_text(encoding="utf-8")
        except OSError:
            return []
        versions: list[StateVersion] = []
        for line in raw.splitlines():
            if len(versions) >= limit:
                break
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(value, dict):
                continue
            entry = cast(dict[str, object], value)
            recorded_at = entry.get("recorded_at")
            content = entry.get("content")
            if isinstance(recorded_at, str) and isinstance(content, str) and content.strip():
                versions.append(StateVersion(recorded_at, content.strip()))
        return versions


class MemoryStateStore:
    """Resolve session-scoped states inside one agent workspace."""

    def __init__(
        self,
        workspace: Path,
        *,
        max_chars: int = DEFAULT_STATE_MAX_CHARS,
        history_versions: int = DEFAULT_STATE_HISTORY_VERSIONS,
    ) -> None:
        self.workspace = Path(workspace)
        self.max_chars = max(1, int(max_chars))
        self.history_versions = max(0, int(history_versions))
        self.state_dir = self.workspace / "memory" / _STATE_DIRNAME

    def for_session(self, session_key: str | None) -> MemoryState:
        return MemoryState(
            self.workspace,
            session_key=session_key,
            max_chars=self.max_chars,
            history_versions=self.history_versions,
        )

    def legacy(self) -> MemoryState:
        return self.for_session(None)

    def list_session_keys(self) -> list[str]:
        if not self.state_dir.is_dir():
            return []
        keys: list[str] = []
        try:
            paths = sorted(self.state_dir.glob("*.md"))
        except OSError:
            return []
        for path in paths:
            key = session_key_from_state_token(path.stem)
            if key is not None:
                keys.append(key)
        return sorted(keys)


def state_summary_text(state: SessionState) -> str:
    """One-line description used by tools and logs."""
    parts = [f"{STATE_SECTION_TITLES[key]}: {len(value)} chars" for key, value in state.as_dict().items() if value]
    return ", ".join(parts) if parts else "empty"


__all__ = [
    "DEFAULT_STATE_HISTORY_VERSIONS",
    "DEFAULT_STATE_MAX_CHARS",
    "STATE_SECTION_KEYS",
    "STATE_SECTION_TITLES",
    "MemoryState",
    "MemoryStateStore",
    "SessionState",
    "StateVersion",
    "parse_session_state",
    "render_session_state",
    "session_key_from_state_token",
    "state_session_token",
    "state_summary_text",
]
