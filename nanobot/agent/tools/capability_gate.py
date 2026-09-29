"""Prompt-level capability gating.

Only a small set of tools is sent to the model on every request. Everything else
stays registered but hidden until the model discovers it through
``find_capabilities``; this module tracks what one run has discovered and renders
the schema list the provider actually receives.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nanobot.agent.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from collections.abc import Iterable

    from nanobot.agent.skills import SkillsLoader

FIND_CAPABILITIES_TOOL_NAME = "find_capabilities"

#: Tools that are always sent, even before discovery. The memory tools stay
#: unconditional because the prompt contract depends on them being callable, and
#: the search tool itself has to remain reachable.
DEFAULT_ALWAYS_VISIBLE: frozenset[str] = frozenset({
    FIND_CAPABILITIES_TOOL_NAME,
    "update_state",
    "recall_memory",
    "recall_backup",
})

_CATALOG_MAX_ENTRIES = 60
_CATALOG_DESCRIPTION_CHARS = 80

_CURRENT_CAPABILITY_GATE: ContextVar["CapabilityGate | None"] = ContextVar(
    "nanobot_capability_gate",
    default=None,
)


def bind_capability_gate(gate: CapabilityGate | None) -> Token[CapabilityGate | None]:
    """Bind the gate for the current run; ``None`` explicitly hides outer gates."""
    return _CURRENT_CAPABILITY_GATE.set(gate)


def reset_capability_gate(token: Token[CapabilityGate | None]) -> None:
    _CURRENT_CAPABILITY_GATE.reset(token)


def current_capability_gate() -> CapabilityGate | None:
    return _CURRENT_CAPABILITY_GATE.get()


def clip_description(text: str, limit: int = _CATALOG_DESCRIPTION_CHARS) -> str:
    """Collapse whitespace and shorten one catalog line."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3].rstrip() + "..."


@dataclass
class CapabilityGate:
    """What the current run may see, and what it has discovered so far."""

    registry: ToolRegistry
    skills: SkillsLoader | None = None
    always_visible: frozenset[str] = DEFAULT_ALWAYS_VISIBLE
    max_catalog_entries: int = _CATALOG_MAX_ENTRIES

    _unlocked: set[str] = field(default_factory=set, init=False)
    _catalog: str | None = field(default=None, init=False, repr=False)
    _cache_key: tuple[str, ...] | None = field(default=None, init=False, repr=False)
    _cache: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)

    # --- Visibility ---

    def visible_names(self) -> list[str]:
        """Registered tool names sent to the model right now, in stable order."""
        registered = set(self.registry.tool_names)
        return sorted((self.always_visible | self._unlocked) & registered)

    def is_visible(self, name: str) -> bool:
        return name in set(self.visible_names())

    def unlock(self, names: Iterable[str]) -> list[str]:
        """Make tools visible for the rest of the run; returns the newly added ones."""
        registered = set(self.registry.tool_names)
        added: list[str] = []
        for name in names:
            if name in registered and name not in self._unlocked and name not in self.always_visible:
                self._unlocked.add(name)
                added.append(name)
        if added:
            self._cache_key = None
            self._catalog = None
        return sorted(added)

    def visible_definitions(self) -> list[dict[str, Any]]:
        """Provider tool schemas for the visible set, with the catalog rendered in."""
        names = tuple(self.visible_names())
        if self._cache_key == names:
            return self._cache
        self._cache = [self._definition(name) for name in names]
        self._cache_key = names
        return self._cache

    def _definition(self, name: str) -> dict[str, Any]:
        tool = self.registry.get(name)
        if tool is None:  # unreachable: visible_names() only returns registered names
            return {"type": "function", "function": {"name": name, "parameters": {}}}
        schema = tool.to_schema()
        if name == FIND_CAPABILITIES_TOOL_NAME:
            function = schema.setdefault("function", {})
            catalog = self.catalog()
            if catalog:
                function["description"] = f"{tool.description}\n\n{catalog}"
        return schema

    # --- Catalog ---

    def catalog(self) -> str:
        """Names plus one-line purposes for everything the model cannot see yet."""
        if self._catalog is None:
            self._catalog = self._render_catalog()
        return self._catalog

    def tool_entries(self) -> list[tuple[str, str]]:
        """Every registered tool as ``(name, description)``, sorted by name."""
        entries: list[tuple[str, str]] = []
        for name in sorted(self.registry.tool_names):
            tool = self.registry.get(name)
            if tool is not None:
                entries.append((name, tool.description))
        return entries

    def hidden_tool_entries(self) -> list[tuple[str, str]]:
        visible = set(self.visible_names())
        return [(name, description) for name, description in self.tool_entries() if name not in visible]

    def skill_entries(self) -> list[dict[str, Any]]:
        """Skill details from the loader, sorted for a stable prompt, or empty without one."""
        if self.skills is None:
            return []
        return sorted(
            self.skills.list_skill_details(filter_unavailable=False),
            key=lambda entry: str(entry.get("name", "")),
        )

    @staticmethod
    def _skill_catalog_line(entry: dict[str, Any]) -> str:
        suffix = ""
        if not entry.get("available", True):
            missing = str(entry.get("missing") or "")
            suffix = f" (unavailable: {missing})" if missing else " (unavailable)"
        return f"- `{entry.get('name', '')}`: {clip_description(str(entry.get('description', '')))}{suffix}"

    def _render_catalog(self) -> str:
        tools = self.hidden_tool_entries()
        skills = self.skill_entries()
        budget = max(self.max_catalog_entries, 0)
        shown_tools = tools[:budget]
        shown_skills = skills[: max(budget - len(shown_tools), 0)]
        lines: list[str] = ["Not loaded yet; call this tool to load what you need."]
        if shown_tools or shown_skills:
            lines.append("")
        if shown_tools:
            lines.append("Tools:")
            lines.extend(
                f"- `{name}`: {clip_description(description)}" for name, description in shown_tools
            )
        if shown_skills:
            if shown_tools:
                lines.append("")
            lines.append("Skills:")
            lines.extend(self._skill_catalog_line(entry) for entry in shown_skills)
        omitted = (len(tools) - len(shown_tools)) + (len(skills) - len(shown_skills))
        if omitted > 0:
            lines.append("")
            lines.append(f"({omitted} more not listed; search with a specific need.)")
        return "\n".join(lines)
