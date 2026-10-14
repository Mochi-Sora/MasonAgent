"""Channel capabilities replace hardcoded channel names in delivery policy."""

from __future__ import annotations

from mason.agent.turn_delivery import TurnDeliveryFactory
from mason.bus.events import OutboundMessage
from mason.bus.notification_delivery import notification_is_deliverable
from mason.bus.queue import MessageBus
from mason.channels.base import BaseChannel
from mason.channels.capabilities import (
    INTERACTIVE_NOTIFICATIONS,
    SESSION_NOTIFICATIONS,
    channel_capabilities,
    channel_supports,
    declare_channel_capabilities,
)
from mason.events import ContextCompactionEvent, RecoveryStateEvent, RetryStatusEvent


def test_interactive_notices_follow_declared_capabilities_not_names() -> None:
    # The legacy websocket client keeps its opt-in.
    assert notification_is_deliverable(
        RecoveryStateEvent, channel="websocket", publish_lifecycle=False,
    )
    # An undeclared channel does not receive interactive notices...
    assert not notification_is_deliverable(
        RecoveryStateEvent, channel="desktop", publish_lifecycle=True,
    )
    # ...until it declares the capability, regardless of its name.
    declare_channel_capabilities("test-live", {INTERACTIVE_NOTIFICATIONS})
    assert notification_is_deliverable(
        RecoveryStateEvent, channel="test-live", publish_lifecycle=False,
    )
    # Retry status additionally requires lifecycle publication.
    assert not notification_is_deliverable(
        RetryStatusEvent, channel="test-live", publish_lifecycle=False,
    )
    assert notification_is_deliverable(
        RetryStatusEvent, channel="test-live", publish_lifecycle=True,
    )


def test_session_route_capability_gates_legacy_session_notices() -> None:
    factory = TurnDeliveryFactory(MessageBus())
    assert factory.session_events("telegram:chat", {}).publish is None
    assert factory.session_events("cli:chat", {}).publish is not None

    declare_channel_capabilities("test-direct", {SESSION_NOTIFICATIONS})
    assert factory.session_events("test-direct:chat", {}).publish is not None


async def test_declared_capability_delivers_idle_compaction() -> None:
    factory = TurnDeliveryFactory(MessageBus())
    declare_channel_capabilities("test-direct", {SESSION_NOTIFICATIONS})
    sink = factory.session_events("test-direct:chat", {})
    event = ContextCompactionEvent("compact-1", "succeeded")

    await sink.emit(event)

    outbound = factory.bus.outbound.get_nowait()
    assert (outbound.channel, outbound.chat_id, outbound.event) == (
        "test-direct",
        "chat",
        event,
    )


def test_base_channel_declares_no_capabilities_by_default() -> None:
    assert BaseChannel.capabilities == frozenset()
    assert not channel_supports("unregistered", INTERACTIVE_NOTIFICATIONS)
    assert channel_capabilities("cli") == frozenset({SESSION_NOTIFICATIONS})


def test_build_channel_registers_declared_capabilities() -> None:
    from mason.channels.manager import ChannelManager
    from mason.config.schema import Config

    class _CapableChannel(BaseChannel):
        name = "test-capable-channel"
        capabilities = frozenset({INTERACTIVE_NOTIFICATIONS})

        async def start(self) -> None:
            return

        async def stop(self) -> None:
            return

        async def send(self, msg: OutboundMessage) -> None:
            return

    manager = ChannelManager.__new__(ChannelManager)
    manager.config = Config.model_validate({"channels": {}})
    manager.bus = MessageBus()

    manager._build_channel("test-capable-channel", _CapableChannel, {"enabled": True})

    assert channel_supports("test-capable-channel", INTERACTIVE_NOTIFICATIONS)
