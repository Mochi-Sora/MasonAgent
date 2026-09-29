"""On-demand capability discovery: one always-visible search tool.

The catalog lives in the ``find_capabilities`` schema (see
:mod:`nanobot.agent.tools.capability_gate`), so the model knows what exists
without paying for every tool schema and skill description in the prompt.
"""

# Tool.execute accepts heterogeneous schemas.
# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from rapidfuzz import fuzz

from nanobot.agent.tools.base import Tool, ToolResult
from nanobot.agent.tools.capability_gate import (
    FIND_CAPABILITIES_TOOL_NAME,
    CapabilityGate,
    current_capability_gate,
)
from nanobot.agent.tools.schema import StringSchema, tool_parameters_schema
from nanobot.utils.helpers import truncate_text

if TYPE_CHECKING:
    from nanobot.agent.skills import SkillsLoader
    from nanobot.agent.tools.context import ToolContext

_MATCH_LIMIT = 5
_SKILL_MATCH_LIMIT = 3
_MIN_SCORE = 2.0
# A skill body is only worth injecting when the need clearly points at that skill.
_SKILL_BODY_MIN_SCORE = 4.0
_SKILL_BODY_CHARS = 6_000
_RESULT_DESCRIPTION_CHARS = 220

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset({
    "a", "about", "an", "and", "any", "are", "as", "at", "be", "can", "do", "does",
    "for", "from", "get", "help", "how", "i", "in", "is", "it", "its", "me", "my",
    "need", "of", "on", "or", "please", "should", "some", "that", "the", "then",
    "there", "this", "to", "use", "using", "want", "what", "when", "where", "which",
    "with", "you", "your",
})


def _normalize_token(token: str) -> str:
    """Fold simple plurals so 'reminder' matches 'reminders'."""
    if len(token) > 3 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(text: str) -> set[str]:
    return {
        _normalize_token(token)
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 1 and token not in _STOPWORDS
    }


def _flatten(name: str) -> str:
    return name.replace("_", " ").replace("-", " ").strip()


def score_need(need: str, name: str, description: str) -> float:
    """Rank one capability against a plain-language need."""
    need_lower = " ".join(need.lower().split())
    name_text = _flatten(name).lower()
    description_lower = description.lower()
    need_tokens = _tokens(need_lower)
    if not need_tokens:
        return 0.0
    name_tokens = _tokens(name_text)
    description_tokens = _tokens(description_lower[:800])
    score = 3.0 * len(need_tokens & name_tokens)
    score += 1.0 * len(need_tokens & (description_tokens - name_tokens))
    score += 2.0 * fuzz.partial_ratio(need_lower, name_text) / 100.0
    score += 1.5 * fuzz.partial_ratio(need_lower, description_lower[:300]) / 100.0
    if need_lower in name_text or name_text in need_lower:
        score += 4.0
    return score


def _signature(tool: Tool, limit: int = 200) -> str:
    """Compact ``name(required: type, optional?: type)`` call shape."""
    schema: dict[str, Any] = tool.parameters or {}
    raw_properties = schema.get("properties")
    if not isinstance(raw_properties, dict) or not raw_properties:
        return f"{tool.name}()"
    properties = cast("dict[str, Any]", raw_properties)
    raw_required = schema.get("required")
    required_names: set[str] = (
        set(cast("list[str]", raw_required)) if isinstance(raw_required, list) else set()
    )
    parts: list[str] = []
    for param, raw_fragment in properties.items():
        if not isinstance(raw_fragment, dict):
            continue
        fragment = cast("dict[str, Any]", raw_fragment)
        raw_type = fragment.get("type")
        if isinstance(raw_type, list):
            candidates = cast("list[Any]", raw_type)
            type_name = str(next((item for item in candidates if item != "null"), "any"))
        else:
            type_name = str(raw_type or "any")
        raw_enum = fragment.get("enum")
        if isinstance(raw_enum, list) and raw_enum:
            type_name = "|".join(str(item) for item in cast("list[Any]", raw_enum)[:6])
        marker = "" if param in required_names else "?"
        parts.append(f"{param}{marker}: {type_name}")
    rendered = ", ".join(parts)
    if len(rendered) > limit:
        rendered = rendered[: limit - 3].rstrip() + "..."
    return f"{tool.name}({rendered})"


@dataclass(frozen=True)
class _Candidate:
    name: str
    description: str
    tool: Tool | None = None
    available: bool = True


class FindCapabilitiesTool(Tool):
    """Search the hidden tool and skill libraries and load what matches."""

    def __init__(self, skills: SkillsLoader | None = None) -> None:
        self._skills = skills

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        config = getattr(ctx, "config", None)
        lazy = getattr(config, "lazy_capabilities", None)
        return bool(getattr(lazy, "enabled", True))

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        return cls(getattr(ctx, "skills_loader", None))

    @property
    def name(self) -> str:
        return FIND_CAPABILITIES_TOOL_NAME

    @property
    def description(self) -> str:
        return (
            "Find tools and skills by describing what you need in plain language. Tool "
            "schemas and skill descriptions are loaded on demand, so most capabilities "
            "are not in your tool list yet: call this before concluding something is "
            "impossible or working around a missing tool. Matching tools become callable "
            "on your next step; the best-matching skill is returned in full."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return tool_parameters_schema(
            need=StringSchema(
                "What you want to do, in plain language, e.g. 'read a file', "
                "'search the web', 'schedule a reminder', 'transcribe audio'.",
            ),
            kind=StringSchema(
                "Restrict the search to tools or skills. Default 'any' searches both.",
                enum=["any", "tool", "skill"],
            ),
            required=["need"],
        )

    async def execute(self, need: str = "", kind: str = "any", **kwargs: Any) -> Any:
        query = " ".join(str(need or "").split())
        if not query:
            return ToolResult.error(
                "Error: need is required. Describe what you want to do, e.g. "
                "find_capabilities(need=\"search the web\").",
            )
        tool_filter = str(kind or "any").lower()
        if tool_filter not in {"any", "tool", "skill"}:
            return ToolResult.error("Error: kind must be one of 'any', 'tool', 'skill'.")

        gate = current_capability_gate()
        skills = self._resolve_skills(gate)
        tool_matches = (
            self._rank(query, self._tool_candidates(gate))[:_MATCH_LIMIT]
            if tool_filter != "skill"
            else []
        )
        skill_matches = (
            self._rank(query, self._skill_candidates(skills))[:_SKILL_MATCH_LIMIT]
            if tool_filter != "tool"
            else []
        )
        if not tool_matches and not skill_matches:
            return self._no_match(query, gate, skills, tool_filter)

        loaded: list[str] = []
        if gate is not None and tool_matches:
            loaded = gate.unlock([candidate.name for _, candidate in tool_matches])
        return self._render(query, tool_matches, skill_matches, gate, skills, set(loaded))

    # --- Candidates ---

    def _resolve_skills(self, gate: CapabilityGate | None) -> SkillsLoader | None:
        """Prefer the loader from the run; fall back to the one built at registration."""
        if self._skills is not None:
            return self._skills
        return gate.skills if gate is not None else None

    @staticmethod
    def _tool_candidates(gate: CapabilityGate | None) -> list[_Candidate]:
        if gate is None:
            return []
        return [
            _Candidate(name=name, description=description, tool=gate.registry.get(name))
            for name, description in gate.tool_entries()
        ]

    @staticmethod
    def _skill_candidates(skills: SkillsLoader | None) -> list[_Candidate]:
        if skills is None:
            return []
        return [
            _Candidate(
                name=str(entry.get("name", "")),
                description=str(entry.get("description", "")),
                available=bool(entry.get("available", True)),
            )
            for entry in skills.list_skill_details(filter_unavailable=False)
            if entry.get("name")
        ]

    @staticmethod
    def _rank(
        need: str,
        candidates: list[_Candidate],
    ) -> list[tuple[float, _Candidate]]:
        scored = [
            (score_need(need, candidate.name, candidate.description), candidate)
            for candidate in candidates
        ]
        ranked = [(score, candidate) for score, candidate in scored if score >= _MIN_SCORE]
        ranked.sort(key=lambda item: (-item[0], item[1].name))
        return ranked

    # --- Rendering ---

    def _no_match(
        self,
        query: str,
        gate: CapabilityGate | None,
        skills: SkillsLoader | None,
        tool_filter: str,
    ) -> str:
        lines = [f"No capability matched {query!r}."]
        if tool_filter != "skill":
            if gate is None:
                lines.append("Tool search is unavailable in this run.")
            else:
                names = [name for name, _description in gate.tool_entries()]
                if names:
                    lines.append(
                        "Available tools: " + ", ".join(f"`{name}`" for name in names[:40]) + "."
                    )
        if tool_filter != "tool" and skills is not None:
            names = [candidate.name for candidate in self._skill_candidates(skills)][:20]
            if names:
                lines.append("Available skills: " + ", ".join(f"`{name}`" for name in names) + ".")
        lines.append("Try different words, or continue with the tools already in your tool list.")
        return "\n".join(lines)

    def _render(
        self,
        query: str,
        tool_matches: list[tuple[float, _Candidate]],
        skill_matches: list[tuple[float, _Candidate]],
        gate: CapabilityGate | None,
        skills: SkillsLoader | None,
        loaded: set[str],
    ) -> str:
        lines = [f"Capabilities for {query!r}:"]
        if tool_matches:
            lines.append("")
            lines.append("Tools:")
            for _score, candidate in tool_matches:
                if candidate.name in loaded:
                    state = "now loaded, call it on your next step"
                elif gate is not None and gate.is_visible(candidate.name):
                    state = "already loaded"
                else:
                    state = "available"
                tool = candidate.tool
                shape = _signature(tool) if tool is not None else candidate.name
                lines.append(
                    f"- `{candidate.name}` ({state}): {shape} — "
                    f"{truncate_text(candidate.description, _RESULT_DESCRIPTION_CHARS).strip()}"
                )
        if skill_matches:
            best_score, best = skill_matches[0]
            strong = best_score >= _SKILL_BODY_MIN_SCORE
            body = self._skill_body(best.name, skills) if strong and best.available else None
            if body:
                lines.append("")
                lines.append(f"Skill `{best.name}` (follow these instructions):")
                lines.append(body)
            elif strong:
                lines.append("")
                lines.append(f"Skill `{best.name}` matches, but its requirements are not met.")
            listed = skill_matches[1:] if body or strong else skill_matches
            if listed:
                names = ", ".join(f"`{candidate.name}`" for _score, candidate in listed)
                lines.append("")
                lines.append(f"Other matching skills: {names}.")
        if loaded:
            lines.append("")
            lines.append(
                "Loaded tools appear in your tool list on the next step; call them directly."
            )
        return "\n".join(lines)

    @staticmethod
    def _skill_body(name: str, skills: SkillsLoader | None) -> str | None:
        if skills is None:
            return None
        content = skills.load_skills_for_context([name])
        if not content:
            return None
        return truncate_text(content, _SKILL_BODY_CHARS).strip()
