"""Tool approval gating: broker round trips and the approval hook."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from mason.agent.hook import AgentHookContext
from mason.agent.hooks.tool_approval import ToolApprovalHook
from mason.agent.loop import AgentLoop
from mason.agent.tools.approvals import (
    ToolApprovalBroker,
    ToolApprovalDeniedError,
    resolve_tool_approval,
)
from mason.bus.events import InboundMessage
from mason.bus.outbound_events import ApprovalRequestEvent
from mason.bus.queue import MessageBus
from mason.channels.capabilities import TOOL_APPROVALS, declare_channel_capabilities
from mason.config.schema import ToolApprovalConfig, ToolsConfig
from mason.events import AgentEvent, EventSink
from mason.providers.base import LLMResponse, ToolCallRequest


def _call(name: str = "write_file", arguments: object = None) -> ToolCallRequest:
    return ToolCallRequest(id="call_1", name=name, arguments=arguments or {})


def test_approval_config_defaults_and_camel_case_aliases() -> None:
    default = ToolsConfig()
    assert default.approval.enabled is False
    assert default.approval.require == []
    assert default.approval.timeout_s == 300.0

    custom = ToolsConfig.model_validate({
        "approval": {"enabled": True, "require": ["exec"], "timeoutS": 90},
    })
    assert custom.approval.enabled is True
    assert custom.approval.require == ["exec"]
    assert custom.approval.timeout_s == 90


def _hook_context() -> AgentHookContext:
    return AgentHookContext(iteration=0, messages=[])


async def test_broker_resolves_denies_and_times_out() -> None:
    broker = ToolApprovalBroker()
    approved_id = broker.request()
    waiter = asyncio.create_task(broker.wait(approved_id, timeout_s=5))
    await asyncio.sleep(0)
    assert broker.resolve(approved_id, approved=True) is True
    assert await waiter is True
    assert broker.pending() == frozenset()

    denied_id = broker.request()
    waiter = asyncio.create_task(broker.wait(denied_id, timeout_s=5))
    await asyncio.sleep(0)
    assert broker.resolve(denied_id, approved=False) is True
    assert await waiter is False

    unanswered = broker.request()
    assert await broker.wait(unanswered, timeout_s=0.01) is False
    assert broker.pending() == frozenset()
    assert broker.resolve("approval-missing", approved=True) is False


async def test_gate_denies_without_a_capable_channel() -> None:
    published: list[AgentEvent] = []

    async def collect(event: AgentEvent) -> None:
        published.append(event)

    # Both an unbound sink and a bound sink on an incapable channel deny.
    for events in (EventSink(), EventSink(publish=collect)):
        hook = ToolApprovalHook(
            events=events,
            channel="plain",
            require=frozenset({"write_file"}),
            timeout_s=5,
            broker=ToolApprovalBroker(),
        )
        with pytest.raises(ToolApprovalDeniedError):
            await hook.before_execute_tool(_hook_context(), _call(), None, {})
    assert published == []


async def test_gate_ignores_tools_outside_the_require_list() -> None:
    hook = ToolApprovalHook(
        events=EventSink(),
        channel="plain",
        require=frozenset({"exec"}),
        timeout_s=5,
        broker=ToolApprovalBroker(),
    )
    await hook.before_execute_tool(_hook_context(), _call("write_file"), None, {})


async def test_gate_publishes_and_waits_for_the_decision() -> None:
    declare_channel_capabilities("test-approval-gate", {TOOL_APPROVALS})
    published: list[AgentEvent] = []
    broker = ToolApprovalBroker()

    async def publish(event: AgentEvent) -> None:
        published.append(event)

    hook = ToolApprovalHook(
        events=EventSink(publish=publish),
        channel="test-approval-gate",
        require=frozenset({"write_file"}),
        timeout_s=5,
        broker=broker,
    )
    waiter = asyncio.create_task(
        hook.before_execute_tool(
            _hook_context(), _call(arguments={"path": "note.txt"}), None, {"path": "note.txt"},
        )
    )
    for _ in range(100):
        if published:
            break
        await asyncio.sleep(0.01)

    event = published[0]
    assert isinstance(event, ApprovalRequestEvent)
    assert event.tool == "write_file"
    assert "note.txt" in event.summary
    assert broker.resolve(event.approval_id, approved=True) is True
    await asyncio.wait_for(waiter, 5)  # completed without raising = approved


def _provider(*responses: LLMResponse) -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 4096
    provider.chat_stream_with_retry = AsyncMock(side_effect=list(responses))
    return provider


def _gated_responses(content: str = "approved") -> tuple[LLMResponse, LLMResponse]:
    return (
        LLMResponse(
            content="",
            finish_reason="tool_calls",
            tool_calls=[
                ToolCallRequest(
                    id="call_1",
                    name="write_file",
                    arguments={"path": "note.txt", "content": content},
                )
            ],
        ),
        LLMResponse(content="Saved", finish_reason="stop"),
    )


def _approval_loop(tmp_path: Path, provider: MagicMock) -> AgentLoop:
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        tools_config=ToolsConfig(
            approval=ToolApprovalConfig(enabled=True, require=["write_file"]),
        ),
    )


async def _next_approval(bus: MessageBus) -> ApprovalRequestEvent:
    while True:
        msg = await asyncio.wait_for(bus.consume_outbound(), 5)
        if isinstance(msg.event, ApprovalRequestEvent):
            return msg.event


@pytest.mark.asyncio
async def test_approved_tool_call_executes(tmp_path: Path) -> None:
    declare_channel_capabilities("test-approval-loop", {TOOL_APPROVALS})
    loop = _approval_loop(tmp_path, _provider(*_gated_responses()))

    task = asyncio.create_task(loop._process_message(InboundMessage(
        channel="test-approval-loop", sender_id="user", chat_id="chat-1", content="save a note",
    )))
    event = await _next_approval(loop.bus)
    assert event.tool == "write_file"
    assert event.turn_id
    assert resolve_tool_approval(event.approval_id, approved=True) is True

    result = await asyncio.wait_for(task, 10)
    assert result is not None
    assert result.content == "Saved"
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "approved"


@pytest.mark.asyncio
async def test_denied_tool_call_is_refused_and_reported(tmp_path: Path) -> None:
    declare_channel_capabilities("test-approval-loop", {TOOL_APPROVALS})
    loop = _approval_loop(tmp_path, _provider(*_gated_responses()))

    task = asyncio.create_task(loop._process_message(InboundMessage(
        channel="test-approval-loop", sender_id="user", chat_id="chat-1", content="save a note",
    )))
    event = await _next_approval(loop.bus)
    assert resolve_tool_approval(event.approval_id, approved=False) is True

    result = await asyncio.wait_for(task, 10)
    assert result is not None
    assert result.content == "Saved"
    assert not (tmp_path / "note.txt").exists()
    session = loop.sessions.get_or_create("test-approval-loop:chat-1")
    tool_messages = [m for m in session.messages if m.get("role") == "tool"]
    assert tool_messages
    assert "denied" in tool_messages[-1]["content"]


@pytest.mark.asyncio
async def test_incapable_channel_denies_without_asking(tmp_path: Path) -> None:
    loop = _approval_loop(tmp_path, _provider(*_gated_responses()))

    result = await loop._process_message(InboundMessage(
        channel="plain-channel", sender_id="user", chat_id="chat-1", content="save a note",
    ))

    assert result is not None
    assert result.content == "Saved"
    assert not (tmp_path / "note.txt").exists()
    emitted = []
    while loop.bus.outbound_size > 0:
        emitted.append(await loop.bus.consume_outbound())
    assert not any(isinstance(m.event, ApprovalRequestEvent) for m in emitted)
