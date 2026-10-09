"""Periodic consolidation of long-term memory.

Once per interval — and once more before each daily rollover — the job reviews
the backup episodes recorded since its cursor and asks the configured model for
a bounded JSON plan: promote durable memories, merge duplicates, rewrite vague
entries, link related ones, drop stale ones, and distill tiny skills from stable
patterns. The plan is applied transactionally and the cursor advances only after
a successful run, so a failed run simply retries the same batch next time.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import json_repair
from loguru import logger

from nanobot.llm_usage.context import llm_usage_source
from nanobot.memory.state import MemoryState, StateVersion
from nanobot.memory.store import (
    CONSOLIDATION_LAST_ATTEMPT_META_KEY,
    CONSOLIDATION_LAST_ERROR_META_KEY,
    CONSOLIDATION_LAST_OK_META_KEY,
    EpisodeHit,
    MemoryDB,
    MemoryHit,
    plan_entries,
    plan_str,
    plan_str_list,
)
from nanobot.utils.helpers import atomic_write_lines, truncate_text
from nanobot.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from nanobot.utils.llm_runtime import LLMRuntime

_CONSOLIDATION_PROMPT = render_template("agent/consolidation.md", strip=True)

_BATCH_EPISODES = 40
_MAX_MEMORIES = 150
# Per-section character budgets. Each section gets its own so a long backup batch
# can never starve the long-term memory list the curator needs for de-duplication
# (or vice versa). Worst case is ~100 KB (~25k tokens): a background job on a
# large-context model, not a per-turn cost.
_EPISODE_BUDGET = 44_000
_MEMORY_BUDGET = 44_000
_STATE_BUDGET = 12_000
# One turn's share of the episode budget. A small batch shows up to 6 KB, so a
# multi-KB report is judged on its body rather than its first few hundred
# characters; a busy batch shrinks the share fairly so every turn is still shown.
_EPISODE_PREVIEW_CHARS = 6_000
_EPISODE_MIN_PREVIEW_CHARS = 800
_MEMORY_PREVIEW_CHARS = 240
_STATE_PREVIEW_CHARS = 2_500
_STATE_VERSION_PREVIEW_CHARS = 1_000
_MAX_STATE_VERSIONS = 5
# When a turn must be shortened, keep both ends so a report's conclusion survives.
_PREVIEW_ELISION = "\n…\n"
_PREVIEW_HEAD_RATIO = 0.6
_SKILL_BODY_MAX_CHARS = 1_200
_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,40}$")
_GENERATED_MARKER = "<!-- generated-by: memory-consolidation -->"


@dataclass(frozen=True, slots=True)
class ConsolidationResult:
    """Outcome of one consolidation pass."""

    ok: bool
    episodes: int = 0
    cursor: int = 0
    applied: Mapping[str, int] | None = None
    skills: tuple[str, ...] = ()
    retired_skills: tuple[str, ...] = ()
    snapshot: str | None = None
    error: str | None = None

    def summary(self) -> str:
        if not self.ok:
            return f"failed after {self.episodes} episode(s): {self.error}"
        if not self.episodes:
            return "nothing new to consolidate"
        applied = dict(self.applied or {})
        parts = [f"{key}={value}" for key, value in applied.items() if value]
        if self.skills:
            parts.append(f"skills={len(self.skills)}")
        if self.retired_skills:
            parts.append(f"retired_skills={len(self.retired_skills)}")
        return f"{self.episodes} episode(s): " + (", ".join(parts) or "no changes")


class MemoryConsolidator:
    """Runs one consolidation pass; independent of AgentLoop for testability."""

    def __init__(
        self,
        workspace: Path,
        db: MemoryDB,
        *,
        state: MemoryState | None = None,
    ) -> None:
        self.workspace = workspace
        self.db = db
        # The working state is the model's own distillation of what mattered;
        # consolidation mines it alongside the raw turns.
        self.state = state or MemoryState(workspace)

    async def run(self, runtime: LLMRuntime, *, now: datetime | None = None) -> ConsolidationResult:
        moment = now or datetime.now()
        cursor = self.db.consolidation_cursor()
        episodes = self.db.episodes_after(cursor, limit=_BATCH_EPISODES)
        if not episodes:
            # Record the heartbeat so health shows the job is still ticking.
            self.db.set_meta(CONSOLIDATION_LAST_ATTEMPT_META_KEY, moment.isoformat())
            return ConsolidationResult(ok=True, cursor=cursor)
        memories = self.db.list_memories(limit=_MAX_MEMORIES)
        state_text = self.state.read()
        state_versions = self.state.versions(limit=_MAX_STATE_VERSIONS)
        prompt = self._build_prompt(episodes, memories, state_text, state_versions)
        plan, error = await self._request_plan(runtime, prompt)
        if plan is None:
            self._record_failure(error, moment)
            logger.warning("Memory consolidation skipped: {}", error)
            return ConsolidationResult(
                ok=False,
                episodes=len(episodes),
                cursor=cursor,
                error=error,
            )
        applied = self.db.apply_plan(plan, now=now)
        created_skills, retired_skills = self._apply_skills(plan)
        new_cursor = max(episode.id for episode in episodes)
        self.db.set_consolidation_cursor(new_cursor)
        self._record_success(moment)
        snapshot = self._write_snapshot(
            plan,
            applied,
            created_skills,
            retired_skills,
            now=now,
        )
        result = ConsolidationResult(
            ok=True,
            episodes=len(episodes),
            cursor=new_cursor,
            applied=applied,
            skills=tuple(created_skills),
            retired_skills=tuple(retired_skills),
            snapshot=snapshot,
        )
        logger.info("Memory consolidation: {}", result.summary())
        return result

    def _record_success(self, moment: datetime) -> None:
        stamp = moment.isoformat()
        self.db.set_meta(CONSOLIDATION_LAST_OK_META_KEY, stamp)
        self.db.set_meta(CONSOLIDATION_LAST_ATTEMPT_META_KEY, stamp)
        self.db.set_meta(CONSOLIDATION_LAST_ERROR_META_KEY, "")

    def _record_failure(self, error: str | None, moment: datetime) -> None:
        self.db.set_meta(CONSOLIDATION_LAST_ATTEMPT_META_KEY, moment.isoformat())
        self.db.set_meta(CONSOLIDATION_LAST_ERROR_META_KEY, (error or "unknown")[:500])

    # -- prompt ---------------------------------------------------------------

    def _build_prompt(
        self,
        episodes: Sequence[EpisodeHit],
        memories: Sequence[MemoryHit],
        state_text: str = "",
        state_versions: Sequence[StateVersion] = (),
    ) -> str:
        lines = ["## New backup turns", *self._episode_lines(episodes)]
        lines += ["", "## Current working state", *self._state_lines(state_text)]
        lines += [
            "",
            "## Recent working states (newest first)",
            *self._state_version_lines(state_versions),
        ]
        lines += ["", "## Current long-term memory", *self._memory_lines(memories)]
        return "\n".join(lines)

    def _episode_lines(self, episodes: Sequence[EpisodeHit]) -> list[str]:
        if not episodes:
            return ["(none)"]
        allowance = min(
            _EPISODE_PREVIEW_CHARS,
            max(_EPISODE_MIN_PREVIEW_CHARS, _EPISODE_BUDGET // len(episodes)),
        )
        return [
            f"#{episode.id} [{episode.ts}] {episode.role}: "
            f"{_preview(episode.content, allowance)}"
            for episode in episodes
        ]

    def _state_lines(self, state_text: str) -> list[str]:
        if not state_text:
            return ["(empty)"]
        return [truncate_text(state_text, _STATE_PREVIEW_CHARS)]

    def _state_version_lines(self, state_versions: Sequence[StateVersion]) -> list[str]:
        if not state_versions:
            return ["(none)"]
        budget = _STATE_BUDGET
        lines: list[str] = []
        for version in state_versions:
            content = truncate_text(version.content, _STATE_VERSION_PREVIEW_CHARS)
            entry = f"- [{version.recorded_at}] {content}"
            if len(entry) > budget:
                break
            budget -= len(entry)
            lines.append(entry)
        return lines or ["(none)"]

    def _memory_lines(self, memories: Sequence[MemoryHit]) -> list[str]:
        if not memories:
            return ["(empty)"]
        allowance = max(120, min(_MEMORY_PREVIEW_CHARS, _MEMORY_BUDGET // len(memories)))
        return [
            f"#{memory.id} [{memory.kind}] uses={memory.uses} "
            f"{truncate_text(memory.text, allowance)}"
            for memory in memories
        ]

    async def _request_plan(
        self,
        runtime: LLMRuntime,
        prompt: str,
    ) -> tuple[Mapping[str, object] | None, str | None]:
        messages = [
            {"role": "system", "content": _CONSOLIDATION_PROMPT},
            {"role": "user", "content": prompt},
        ]
        try:
            with llm_usage_source("consolidation"):
                response = await runtime.provider.chat_stream_with_retry(
                    model=runtime.model,
                    messages=messages,
                    tools=[],
                    temperature=runtime.generation.temperature,
                    max_tokens=runtime.generation.max_tokens,
                )
        except Exception as exc:
            return None, f"model call failed: {exc}"
        content = getattr(response, "content", None)
        if not isinstance(content, str) or not content.strip():
            return None, "model returned no content"
        plan = parse_plan(content)
        if plan is None:
            return None, "model returned no usable JSON plan"
        return plan, None

    # -- generated skills -----------------------------------------------------

    def _apply_skills(self, plan: Mapping[str, object]) -> tuple[list[str], list[str]]:
        created: list[str] = []
        for item in plan_entries(plan, "skills"):
            name = plan_str(item, "name")
            description = plan_str(item, "description")
            body = plan_str(item, "body")
            if not _SKILL_NAME_RE.match(name) or not description or not body:
                continue
            path = self.workspace / "skills" / name / "SKILL.md"
            if path.exists() and not _is_generated(path):
                logger.info("Consolidation left user-authored skill {} untouched", name)
                continue
            document = _skill_document(name, description, truncate_text(body, _SKILL_BODY_MAX_CHARS))
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_lines(path, document.splitlines())
            created.append(name)

        retired: list[str] = []
        for name in plan_str_list(plan, "retire_skills"):
            if not _SKILL_NAME_RE.match(name):
                continue
            path = self.workspace / "skills" / name / "SKILL.md"
            if not path.exists() or not _is_generated(path):
                continue
            target_dir = self.workspace / "skills" / "retired" / name
            target_dir.mkdir(parents=True, exist_ok=True)
            atomic_write_lines(target_dir / "SKILL.md", path.read_text(encoding="utf-8").splitlines())
            path.unlink()
            with suppress(OSError):
                path.parent.rmdir()
            retired.append(name)
        return created, retired

    # -- audit snapshot -------------------------------------------------------

    def _write_snapshot(
        self,
        plan: Mapping[str, object],
        applied: Mapping[str, int],
        created_skills: Sequence[str],
        retired_skills: Sequence[str],
        *,
        now: datetime | None,
    ) -> str:
        moment = now or datetime.now()
        lines = [f"# Consolidation snapshot {moment.isoformat(timespec='seconds')}", ""]
        lines.append("- Applied: " + (json.dumps(dict(applied)) if applied else "{}"))
        if created_skills:
            lines.append(f"- Skills created/updated: {', '.join(created_skills)}")
        if retired_skills:
            lines.append(f"- Skills retired: {', '.join(retired_skills)}")
        for key in ("promotions", "merges", "rewrites", "links", "drops"):
            entries = plan_entries(plan, key)
            if not entries:
                continue
            lines.extend(["", f"## {key.capitalize()}"])
            for item in entries[:50]:
                lines.append(f"- {json.dumps(item, ensure_ascii=False, sort_keys=True)}")
        path = self.workspace / "memory" / "consolidation" / "latest.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_lines(path, lines)
        return str(path)


def _preview(text: str, limit: int) -> str:
    """Shorten *text* to about *limit* chars, keeping both ends.

    A plain head-truncation loses the conclusion of a long report, which is
    often the part worth promoting; keeping head and tail preserves the setup
    and the result.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    marker = _PREVIEW_ELISION
    if limit <= len(marker) + 2:
        return text[:limit]
    usable = limit - len(marker)
    head = int(usable * _PREVIEW_HEAD_RATIO)
    tail = usable - head
    return text[:head].rstrip() + marker + text[-tail:].lstrip()


def parse_plan(text: str) -> Mapping[str, object] | None:
    """Extract and repair the JSON plan from a model response."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed: object = json_repair.loads(text[start : end + 1])
    except Exception:
        return None
    if not isinstance(parsed, Mapping):
        return None
    return cast(Mapping[str, object], parsed)


def _is_generated(path: Path) -> bool:
    try:
        return _GENERATED_MARKER in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _skill_document(name: str, description: str, body: str) -> str:
    safe_description = " ".join(description.split())
    escaped = safe_description.replace("\\", "\\\\").replace('"', '\\"')
    title = name.replace("-", " ").title()
    return (
        "---\n"
        f"name: {name}\n"
        f'description: "{escaped}"\n'
        "---\n"
        f"{_GENERATED_MARKER}\n\n"
        f"# {title}\n\n"
        f"{body}\n"
    )
