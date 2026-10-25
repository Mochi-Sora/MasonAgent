"""Framework-triggered session-state updates.

The main model no longer has to decide whether the state is worth updating. After
each eligible turn the framework asks a model for the four state sections and
applies the result itself. The model can still correct the state through the
``update_state`` capability, but the routine path is owned here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

import json_repair
from loguru import logger

from mason.llm_usage.context import llm_usage_source
from mason.memory.state import MemoryState, SessionState
from mason.utils.llm_runtime import LLMRuntime
from mason.utils.prompt_templates import render_template

_STATE_WRITER_SYSTEM = render_template("agent/state_update.md", strip=True)
_MAX_USER_CHARS = 2_000
_MAX_ASSISTANT_CHARS = 4_000
_MAX_TOOL_LINES = 20
_MAX_TOOL_LINE_CHARS = 200
_MAX_OUTPUT_TOKENS = 1_200
_TRUNCATED_SUFFIX = "…"


@dataclass(frozen=True, slots=True)
class StateWriterResult:
    """Outcome of one framework state-update pass."""

    ok: bool
    updated: bool = False
    truncated: bool = False
    error: str | None = None


class StateWriter:
    """Ask a model for the four state sections and apply them safely."""

    def __init__(self, *, max_output_tokens: int = _MAX_OUTPUT_TOKENS) -> None:
        self.max_output_tokens = max(128, int(max_output_tokens))

    async def update(
        self,
        state: MemoryState,
        runtime: LLMRuntime,
        *,
        session_key: str,
        user_message: str,
        assistant_response: str,
        tool_lines: Sequence[str] = (),
        goal_context: str = "",
    ) -> StateWriterResult:
        previous_text = state.read()
        previous = state.read_sections()
        prompt = _build_user_prompt(
            previous=previous_text,
            user_message=user_message,
            assistant_response=assistant_response,
            tool_lines=tool_lines,
            goal_context=goal_context,
        )
        messages = [
            {"role": "system", "content": _STATE_WRITER_SYSTEM},
            {"role": "user", "content": prompt},
        ]
        try:
            with llm_usage_source("state"):
                response = await runtime.provider.chat(
                    messages=messages,
                    tools=[],
                    model=runtime.model,
                    max_tokens=max(
                        128,
                        min(self.max_output_tokens, runtime.generation.max_tokens),
                    ),
                    temperature=0.1,
                )
        except Exception as exc:
            logger.warning(
                "State writer call failed for {}: {}",
                session_key,
                type(exc).__name__,
            )
            return StateWriterResult(ok=False, error=f"model call failed: {exc}")

        if response.finish_reason == "error":
            return StateWriterResult(
                ok=False,
                error=(response.content or "model returned an error")[:500],
            )
        content = response.content
        if not isinstance(content, str) or not content.strip():
            return StateWriterResult(ok=False, error="model returned no content")
        plan = _parse_plan(content)
        if plan is None:
            return StateWriterResult(ok=False, error="model returned no usable JSON state")

        candidate = SessionState(
            current_goal=_coalesce(
                _plan_text(plan, "current_goal", "goal"), previous.current_goal
            ),
            done=_coalesce(
                _plan_text(plan, "done", "completed", "what_has_been_done"),
                previous.done,
            ),
            next_steps=_coalesce(
                _plan_text(plan, "next_steps", "next", "whats_next"),
                previous.next_steps,
            ),
            session_summary=_coalesce(
                _plan_text(plan, "session_summary", "session", "about"),
                previous.session_summary,
            ),
        ).normalized()
        if candidate.is_empty():
            return StateWriterResult(ok=True, updated=False, error="empty state plan")
        merged = candidate

        try:
            written = state.write_sections_if_unchanged(previous_text, merged)
        except Exception as exc:
            logger.warning("State writer could not persist state for {}: {}", session_key, exc)
            return StateWriterResult(ok=False, error=f"write failed: {exc}")
        if written is None:
            # The state changed or was cleared while the model was running.
            logger.debug(
                "State writer skipped stale result for {} (state changed)",
                session_key,
            )
            return StateWriterResult(ok=True, updated=False)
        _stored, truncated = written
        return StateWriterResult(ok=True, updated=True, truncated=truncated)


def _build_user_prompt(
    *,
    previous: str,
    user_message: str,
    assistant_response: str,
    tool_lines: Sequence[str],
    goal_context: str,
) -> str:
    tools = "\n".join(
        _clip(line, _MAX_TOOL_LINE_CHARS) for line in list(tool_lines)[:_MAX_TOOL_LINES]
    )
    return "\n".join(
        [
            "## Previous state",
            previous.strip() or "(none)",
            "",
            "## Active goal signal",
            goal_context.strip() or "(none)",
            "",
            "## This turn",
            "### User",
            _clip(user_message.strip(), _MAX_USER_CHARS) or "(none)",
            "",
            "### Assistant",
            _clip(assistant_response.strip(), _MAX_ASSISTANT_CHARS) or "(none)",
            "",
            "### Tool activity",
            tools or "(none)",
            "",
            "Return only the JSON object described by the system prompt.",
        ]
    )


def _parse_plan(text: str) -> Mapping[str, object] | None:
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


def _plan_text(plan: Mapping[str, object], *keys: str) -> str | None:
    """Return a section's value; ``None`` means the key was omitted."""
    for key in keys:
        if key not in plan:
            continue
        value = plan.get(key)
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return "\n".join(
                str(item).strip()
                for item in cast(list[object], value)
                if isinstance(item, str) and item.strip()
            )
        if value is None:
            return ""
    return None


def _coalesce(value: str | None, previous: str) -> str:
    return previous if value is None else value


def _clip(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    if limit <= len(_TRUNCATED_SUFFIX):
        return text[:limit]
    return text[: limit - len(_TRUNCATED_SUFFIX)].rstrip() + _TRUNCATED_SUFFIX


__all__ = ["StateWriter", "StateWriterResult"]
