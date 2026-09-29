"""Tests for on-demand capability discovery (``find_capabilities`` + gate)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.agent.skills import SkillsLoader
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.capability_gate import (
    FIND_CAPABILITIES_TOOL_NAME,
    CapabilityGate,
    bind_capability_gate,
    current_capability_gate,
    reset_capability_gate,
)
from nanobot.agent.tools.capability_search import FindCapabilitiesTool, score_need
from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import LazyCapabilitiesConfig, ToolsConfig


class _FakeTool(Tool):
    """Minimal registered tool with a controllable name and description."""

    def __init__(
        self,
        tool_name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
    ) -> None:
        self._name = tool_name
        self._description = description
        self._parameters = parameters or {"type": "object", "properties": {}}

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> Any:
        return "ok"


def _registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def _write_skill(workspace: Path, name: str, description: str, body: str) -> None:
    skill_dir = workspace / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _skills_loader(workspace: Path) -> SkillsLoader:
    empty_builtin = workspace / "empty-builtin"
    empty_builtin.mkdir(exist_ok=True)
    return SkillsLoader(workspace, builtin_skills_dir=empty_builtin)


# --- Scoring ---


def test_score_prefers_name_over_unrelated_description() -> None:
    matching = score_need("read a file", "read_file", "Read file contents with pagination.")
    unrelated = score_need("read a file", "cron", "Schedule reminders and recurring jobs.")

    assert matching > unrelated
    assert unrelated < 2.0


def test_score_matches_skill_description_words() -> None:
    score = score_need("transcribe audio", "speech", "Transcribe audio files with whisper.")

    assert score >= 2.0


# --- Gate visibility ---


def test_gate_hides_tools_until_unlocked() -> None:
    gate = CapabilityGate(registry=_registry(_FakeTool("read_file", "Read files.")))

    assert gate.visible_names() == []
    assert gate.visible_definitions() == []

    assert gate.unlock(["read_file"]) == ["read_file"]
    assert gate.visible_names() == ["read_file"]
    assert [item["function"]["name"] for item in gate.visible_definitions()] == ["read_file"]


def test_gate_reports_only_newly_unlocked_registered_tools() -> None:
    gate = CapabilityGate(registry=_registry(_FakeTool("read_file", "Read files.")))

    assert gate.unlock(["read_file", "missing_tool"]) == ["read_file"]
    assert gate.unlock(["read_file"]) == []
    assert gate.visible_names() == ["read_file"]


def test_gate_filters_unknown_always_visible_names() -> None:
    gate = CapabilityGate(
        registry=_registry(_FakeTool("read_file", "Read files.")),
        always_visible=frozenset({"update_state", "read_file"}),
    )

    assert gate.visible_names() == ["read_file"]


def test_gate_caches_definitions_until_unlock() -> None:
    gate = CapabilityGate(registry=_registry(_FakeTool("read_file", "Read files.")))

    first = gate.visible_definitions()
    assert gate.visible_definitions() is first

    gate.unlock(["read_file"])
    assert gate.visible_definitions() is not first


def test_gate_injects_catalog_only_into_find_capabilities() -> None:
    registry = _registry(
        FindCapabilitiesTool(),
        _FakeTool("read_file", "Read file contents."),
    )
    gate = CapabilityGate(registry=registry)
    definitions = {item["function"]["name"]: item for item in gate.visible_definitions()}

    search_description = definitions[FIND_CAPABILITIES_TOOL_NAME]["function"]["description"]
    assert "`read_file`" in search_description
    assert "Not loaded yet" in search_description

    gate.unlock(["read_file"])
    definitions = {item["function"]["name"]: item for item in gate.visible_definitions()}
    # The catalog only lists what is still hidden.
    assert "`read_file`" not in definitions[FIND_CAPABILITIES_TOOL_NAME]["function"]["description"]


def test_catalog_lists_skills_with_availability(tmp_path: Path) -> None:
    _write_skill(tmp_path, "pdf-report", "Render PDF reports.", "# PDF\n\nSteps.")
    gate = CapabilityGate(
        registry=_registry(FindCapabilitiesTool()),
        skills=_skills_loader(tmp_path),
    )

    catalog = gate.catalog()

    assert "`pdf-report`: Render PDF reports." in catalog


# --- Tool execution ---


async def test_find_capabilities_unlocks_matching_tools() -> None:
    registry = _registry(
        FindCapabilitiesTool(),
        _FakeTool(
            "read_file",
            "Read file contents with pagination.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
                "required": ["path"],
            },
        ),
        _FakeTool("cron", "Schedule reminders and recurring jobs."),
    )
    gate = CapabilityGate(registry=registry)
    token = bind_capability_gate(gate)
    try:
        result = await registry.execute("find_capabilities", {"need": "read a file"})
    finally:
        reset_capability_gate(token)

    assert "read_file" in gate.visible_names()
    assert "cron" not in gate.visible_names()
    assert "read_file(path: string, limit?: integer)" in str(result)
    assert "now loaded" in str(result)
    assert "Schedule reminders" not in str(result)


async def test_weak_skill_match_is_listed_without_injecting_its_body(tmp_path: Path) -> None:
    description = "Search community skills for any topic."
    score = score_need("search the web", "clawhub", description)
    # Precondition for the fixture: above the listing threshold, below the body threshold.
    assert 2.0 <= score < 4.0
    _write_skill(tmp_path, "clawhub", description, "# ClawHub\n\nRegistry instructions.")
    registry = _registry(FindCapabilitiesTool(), _FakeTool("web_search", "Search the web for pages."))
    gate = CapabilityGate(registry=registry, skills=_skills_loader(tmp_path))
    token = bind_capability_gate(gate)
    try:
        result = await registry.execute("find_capabilities", {"need": "search the web"})
    finally:
        reset_capability_gate(token)

    assert "`web_search`" in str(result)
    assert "`clawhub`" in str(result)
    assert "Registry instructions" not in str(result)


async def test_find_capabilities_requires_a_need() -> None:
    tool = FindCapabilitiesTool()

    result = await tool.execute(need="   ")

    assert result.is_error
    assert "need is required" in str(result)


async def test_find_capabilities_returns_skill_body(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "release-notes",
        "Write release notes from merged changes.",
        "# Release notes\n\nGroup changes by user impact, not by commit order.",
    )
    tool = FindCapabilitiesTool(_skills_loader(tmp_path))

    result = await tool.execute(need="write release notes")

    assert "Skill `release-notes`" in str(result)
    assert "Group changes by user impact" in str(result)
    assert "description:" not in str(result)


async def test_find_capabilities_skill_kind_does_not_unlock_tools(tmp_path: Path) -> None:
    _write_skill(tmp_path, "release-notes", "Write release notes from merged changes.", "# Steps")
    registry = _registry(FindCapabilitiesTool(), _FakeTool("read_file", "Read file contents."))
    gate = CapabilityGate(registry=registry, skills=_skills_loader(tmp_path))
    token = bind_capability_gate(gate)
    try:
        result = await registry.execute(
            "find_capabilities",
            {"need": "write release notes", "kind": "skill"},
        )
    finally:
        reset_capability_gate(token)

    assert "read_file" not in gate.visible_names()
    assert "Skill `release-notes`" in str(result)
    assert "now loaded" not in str(result)


async def test_find_capabilities_reports_no_match() -> None:
    registry = _registry(FindCapabilitiesTool(), _FakeTool("cron", "Schedule reminders."))
    gate = CapabilityGate(registry=registry)
    token = bind_capability_gate(gate)
    try:
        result = await registry.execute(FIND_CAPABILITIES_TOOL_NAME, {"need": "zzzz qqqq"})
    finally:
        reset_capability_gate(token)

    assert "No capability matched" in str(result)
    assert "Available tools:" in str(result)
    assert "`cron`" in str(result)
    assert "Try different words" in str(result)


def test_find_capabilities_respects_config_toggle() -> None:
    enabled_ctx = ToolContext(config=ToolsConfig(), workspace="/tmp")
    disabled_ctx = ToolContext(
        config=ToolsConfig(lazy_capabilities=LazyCapabilitiesConfig(enabled=False)),
        workspace="/tmp",
    )

    assert FindCapabilitiesTool.enabled(enabled_ctx) is True
    assert FindCapabilitiesTool.enabled(disabled_ctx) is False


async def test_find_capabilities_uses_skill_loader_from_context(tmp_path: Path) -> None:
    _write_skill(tmp_path, "pdf-report", "Render PDF reports.", "# PDF\n\nRender it.")
    ctx = ToolContext(
        config=ToolsConfig(),
        workspace=str(tmp_path),
        skills_loader=_skills_loader(tmp_path),
    )

    tool = FindCapabilitiesTool.create(ctx)

    assert isinstance(tool, FindCapabilitiesTool)
    assert tool._skills is not None
    assert (await tool.execute(need="render pdf report")).find("Skill `pdf-report`") >= 0


def test_gate_context_round_trip() -> None:
    assert current_capability_gate() is None

    gate = CapabilityGate(registry=_registry(_FakeTool("read_file", "Read files.")))
    token = bind_capability_gate(gate)
    try:
        assert current_capability_gate() is gate
    finally:
        reset_capability_gate(token)

    assert current_capability_gate() is None


def test_gate_nested_binding_restores_outer_gate() -> None:
    outer = CapabilityGate(registry=_registry(_FakeTool("read_file", "Read files.")))
    inner = CapabilityGate(registry=_registry(_FakeTool("cron", "Schedules.")))

    outer_token = bind_capability_gate(outer)
    inner_token = bind_capability_gate(inner)
    try:
        assert current_capability_gate() is inner
    finally:
        reset_capability_gate(inner_token)
    try:
        assert current_capability_gate() is outer
    finally:
        reset_capability_gate(outer_token)


def test_gate_catalog_marks_unavailable_skills(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skills" / "needs-cli"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: needs-cli\n"
        "description: Ship with the missing binary.\n"
        "metadata:\n"
        "  nanobot:\n"
        "    requires:\n"
        "      bins: [nanobot-missing-binary]\n"
        "---\n\nBody.\n",
        encoding="utf-8",
    )
    gate = CapabilityGate(registry=_registry(FindCapabilitiesTool()), skills=_skills_loader(tmp_path))

    catalog = gate.catalog()

    assert "`needs-cli`" in catalog
    assert "unavailable: CLI: nanobot-missing-binary" in catalog


def test_skills_loader_details_include_description_and_availability(tmp_path: Path) -> None:
    _write_skill(tmp_path, "alpha", "Alpha work.", "# Alpha")

    details = _skills_loader(tmp_path).list_skill_details()

    assert [(item["name"], item["description"], item["available"]) for item in details] == [
        ("alpha", "Alpha work.", True)
    ]


def test_find_capabilities_parameters_expose_kind_enum() -> None:
    schema = FindCapabilitiesTool().parameters

    assert schema["properties"]["kind"]["enum"] == ["any", "tool", "skill"]
    assert schema["required"] == ["need"]
