"""Tests for runner-level capability gating: only discovered tools reach the provider."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from agent.runner_helpers import make_run_spec
from nanobot.agent.runner import AgentRunner
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.capability_gate import CapabilityGate
from nanobot.agent.tools.capability_search import FindCapabilitiesTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.config.schema import AgentDefaults
from nanobot.providers.base import LLMResponse, ToolCallRequest

_MAX_TOOL_RESULT_CHARS = AgentDefaults().max_tool_result_chars


class _FakeTool(Tool):
    def __init__(self, tool_name: str, description: str) -> None:
        self._name = tool_name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "additionalProperties": False}

    async def execute(self, **kwargs: Any) -> Any:
        return f"{self._name} ran"


def _request_tool_names(kwargs: dict[str, Any]) -> list[str]:
    tools = kwargs.get("tools") or []
    return [item["function"]["name"] for item in tools]


async def test_runner_sends_only_visible_tools_and_adds_discovered_ones() -> None:
    registry = ToolRegistry()
    registry.register(FindCapabilitiesTool())
    registry.register(_FakeTool("web_search", "Search the web for current information."))
    gate = CapabilityGate(registry=registry)

    provider = MagicMock()
    sent: list[list[str]] = []
    descriptions: list[str] = []
    call_count = {"n": 0}

    async def chat_stream_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        sent.append(_request_tool_names(kwargs))
        for item in kwargs.get("tools") or []:
            if item["function"]["name"] == "find_capabilities":
                descriptions.append(item["function"]["description"])
        if call_count["n"] == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="find_capabilities",
                        arguments={"need": "search the web"},
                    )
                ],
                usage=None,
            )
        return LLMResponse(content="done", tool_calls=[], usage=None)

    provider.chat_stream_with_retry = chat_stream_with_retry
    result = await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "look this up"}],
        tools=registry,
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        capability_gate=gate,
    ))

    assert result.final_content == "done"
    assert sent[0] == ["find_capabilities"]
    assert "web_search" in sent[1]
    # The catalog ships inside the search tool description, not the system prompt.
    assert "`web_search`" in descriptions[0]


async def test_runner_without_gate_sends_every_tool() -> None:
    registry = ToolRegistry()
    registry.register(_FakeTool("web_search", "Search the web."))
    registry.register(_FakeTool("read_file", "Read files."))

    provider = MagicMock()
    sent: list[list[str]] = []

    async def chat_stream_with_retry(*, messages, **kwargs):
        sent.append(_request_tool_names(kwargs))
        return LLMResponse(content="done", tool_calls=[], usage=None)

    provider.chat_stream_with_retry = chat_stream_with_retry
    await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "hello"}],
        tools=registry,
        model="test-model",
        max_iterations=1,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
    ))

    assert sent[0] == ["read_file", "web_search"]


async def test_runner_binds_gate_for_tool_execution_and_resets_it() -> None:
    from nanobot.agent.tools.capability_gate import current_capability_gate

    registry = ToolRegistry()
    gate = CapabilityGate(registry=registry)
    observed: list[Any] = []

    class _ProbeTool(Tool):
        @property
        def name(self) -> str:
            return "probe"

        @property
        def description(self) -> str:
            return "Report the active capability gate."

        @property
        def parameters(self) -> dict[str, Any]:
            return {"type": "object", "properties": {}, "additionalProperties": False}

        async def execute(self, **kwargs: Any) -> Any:
            observed.append(current_capability_gate())
            return "ok"

    registry.register(_ProbeTool())
    provider = MagicMock()
    call_count = {"n": 0}

    async def chat_stream_with_retry(*, messages, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return LLMResponse(
                content="",
                tool_calls=[ToolCallRequest(id="call_1", name="probe", arguments={})],
                usage=None,
            )
        return LLMResponse(content="done", tool_calls=[], usage=None)

    provider.chat_stream_with_retry = chat_stream_with_retry
    await AgentRunner().run(make_run_spec(
        provider,
        initial_messages=[{"role": "user", "content": "probe"}],
        tools=registry,
        model="test-model",
        max_iterations=2,
        max_tool_result_chars=_MAX_TOOL_RESULT_CHARS,
        capability_gate=gate,
    ))

    assert observed == [gate]
    assert current_capability_gate() is None
