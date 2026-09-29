
import pytest

from nanobot.agent.turn_delivery import TurnDeliveryFactory
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.outbound_events import ContextCompactionEvent
from nanobot.bus.queue import MessageBus
from nanobot.bus.runtime_events import TurnCompleted
from nanobot.events import RetryStatusEvent
from nanobot.providers.base import LLMProvider, ProviderCallContext
from nanobot.session.keys import HEARTBEAT_SESSION_KEY
from nanobot.session.turn_metadata import (
    WEBUI_TURN_METADATA_KEY,
)


@pytest.mark.parametrize("channel", ["telegram", "discord", "websocket", "cli", "custom"])
@pytest.mark.parametrize("legacy_route", [False, True])
async def test_internal_compaction_never_reaches_its_result_destination(channel, legacy_route):
    key = HEARTBEAT_SESSION_KEY
    factory = TurnDeliveryFactory(MessageBus())
    msg = InboundMessage(channel=channel, sender_id="user", chat_id="chat", content="check")
    delivery = factory.create(msg, key)
    metadata = {"_compaction_route": {"channel": channel, "chat_id": "chat"}} if legacy_route else {}
    idle_events = factory.session_events(key, metadata)
    for sink in (delivery.events, idle_events):
        assert not sink.accepts(ContextCompactionEvent)
        for phase in ("started", "succeeded", "failed", "cancelled"):
            await sink.emit(ContextCompactionEvent("internal-compact", phase))
    assert factory.bus.outbound.empty()

    delivery.remember_session_route(metadata)
    assert "_compaction_route" not in metadata

    # The same destination still receives the user's own compaction lifecycle.
    user_delivery = factory.create(msg, f"{channel}:chat")
    user_delivery.remember_session_route(metadata)
    for sink in (user_delivery.events, factory.session_events(f"{channel}:chat", metadata)):
        await sink.emit(ContextCompactionEvent("user-compact", "succeeded"))
        assert factory.bus.outbound.get_nowait().event == ContextCompactionEvent(
            "user-compact", "succeeded",
        )


@pytest.mark.parametrize("unified", [False, True])
@pytest.mark.parametrize(("key", "channel", "chat_id", "metadata"), [
    ("slack:C123:1700000000.000100", "slack", "C123",
     {"slack": {"thread_ts": "1700000000.000100"}}),
    ("telegram:-100123:topic:42", "telegram", "-100123", {"message_thread_id": 42}),
    ("discord:456:thread:777", "discord", "777", {}),
    ("mattermost:channel:root", "mattermost", "channel", {"mattermost": {"root_id": "root"}}),
    ("matrix:!room:example.org:thread:$root", "matrix", "!room:example.org",
     {"thread_root_event_id": "$root", "thread_reply_to_event_id": "$reply"}),
    ("feishu:chat:thread:root", "feishu", "chat",
     {"message_id": "reply", "thread_id": "root", "chat_type": "group"}),
    ("dingtalk:group:conversation:user", "dingtalk", "group:conversation", {}),
])
async def test_idle_compaction_uses_the_session_delivery_route(
    key, channel, chat_id, metadata, unified,
) -> None:
    factory = TurnDeliveryFactory(MessageBus())
    event = ContextCompactionEvent(compaction_id="compact-1", phase="started")
    key = "unified:default" if unified else key
    msg = InboundMessage(
        channel=channel, sender_id="user", chat_id=chat_id, content="hello",
        metadata={
            "webui_turn_id": "turn-1", "sender_name": "User",
            "message_id": "received-1", "thread_id": "received-thread", **metadata,
        },
    )
    delivery = factory.create(msg, key)
    session_metadata = {}
    delivery.remember_session_route(session_metadata)

    sink = factory.session_events(key, session_metadata)
    assert sink.publish is not None
    await sink.emit(event)

    outbound = factory.bus.outbound.get_nowait()
    assert (outbound.channel, outbound.chat_id, outbound.metadata) == (channel, chat_id, metadata)
    assert outbound.event is event


async def test_idle_compaction_keeps_its_route_when_a_unified_session_moves() -> None:
    factory = TurnDeliveryFactory(MessageBus())
    key = "unified:default"
    session_metadata = {}
    original = InboundMessage(
        channel="slack", sender_id="user", chat_id="C123", content="hello",
        metadata={"slack": {"thread_ts": "1700000000.000100"}},
    )
    factory.create(original, key).remember_session_route(session_metadata)
    sink = factory.session_events(key, session_metadata)
    assert sink.publish is not None
    await sink.emit(ContextCompactionEvent("compact-1", "started"))

    latest = InboundMessage(
        channel="telegram", sender_id="user", chat_id="42", content="next question",
    )
    factory.create(latest, key).remember_session_route(session_metadata)
    await sink.emit(ContextCompactionEvent("compact-1", "succeeded"))

    events = [factory.bus.outbound.get_nowait() for _ in range(2)]
    assert [(msg.channel, msg.chat_id, msg.metadata) for msg in events] == [
        ("slack", "C123", {"slack": {"thread_ts": "1700000000.000100"}}),
    ] * 2


async def test_idle_compaction_can_deliver_to_a_legacy_websocket_session() -> None:
    factory = TurnDeliveryFactory(MessageBus())
    event = ContextCompactionEvent(compaction_id="compact-1", phase="succeeded")
    sink = factory.session_events("websocket:chat", {})
    assert sink.publish is not None
    await sink.emit(event)
    outbound = factory.bus.outbound.get_nowait()
    assert (outbound.channel, outbound.chat_id, outbound.event) == ("websocket", "chat", event)


@pytest.mark.asyncio
async def test_retry_event_uses_scoped_channel_delivery() -> None:
    bus = MessageBus()
    msg = InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id="chat-a",
        content="hello",
        metadata={WEBUI_TURN_METADATA_KEY: "turn-1"},
    )
    delivery = TurnDeliveryFactory(bus).create(msg, msg.session_key)

    await delivery.events.emit(RetryStatusEvent(
        state="waiting",
        attempt=1,
        max_attempts=4,
        error_kind="connection",
        next_retry_at=123.5,
    ))

    assert bus.outbound_size == 1
    outbound = bus.outbound.get_nowait()
    assert isinstance(outbound.event, RetryStatusEvent)
    assert outbound.event.error_kind == "connection"
    assert outbound.event.next_retry_at == 123.5
    assert outbound.metadata[WEBUI_TURN_METADATA_KEY] == "turn-1"


@pytest.mark.asyncio
async def test_delivery_maps_model_error_to_failed_turn_completion() -> None:
    bus = MessageBus()
    seen: list[TurnCompleted] = []
    bus.subscribe(seen.append, TurnCompleted)
    msg = InboundMessage(
        channel="websocket",
        sender_id="user",
        chat_id="chat-a",
        content="hello",
    )
    delivery = TurnDeliveryFactory(bus).create(msg, msg.session_key)
    delivery.record_stop_reason("error", failure_error_kind="billing")

    await delivery.complete(
        OutboundMessage(
            channel="websocket",
            chat_id="chat-a",
            content="Sorry, I encountered an error calling the AI model.",
        ),
        publish_completion=True,
    )

    assert len(seen) == 1
    assert seen[0].outcome == "failed"
    assert seen[0].failure_kind == "model"
    assert seen[0].failure_error_kind == "billing"
    assert bus.outbound_size == 0


@pytest.mark.asyncio
async def test_delivery_keeps_model_error_message_for_ordinary_channels() -> None:
    bus = MessageBus()
    msg = InboundMessage(
        channel="telegram",
        sender_id="user",
        chat_id="chat-a",
        content="hello",
    )
    delivery = TurnDeliveryFactory(bus).create(msg, msg.session_key)
    delivery.record_stop_reason("error")
    response = OutboundMessage(
        channel="telegram",
        chat_id="chat-a",
        content="Sorry, I encountered an error calling the AI model.",
    )

    await delivery.complete(response, publish_completion=True)

    assert await bus.consume_outbound() is response


@pytest.mark.parametrize("channel", ["telegram", "cli", "websocket"])
async def test_background_retry_status_is_quiet(channel) -> None:
    factory = TurnDeliveryFactory(MessageBus())
    delivery = factory.create(InboundMessage(
        channel="system", sender_id="job", chat_id=f"{channel}:chat", content="",
    ), f"{channel}:chat")
    assert not delivery.events.accepts(RetryStatusEvent)
    await delivery.events.emit(RetryStatusEvent("waiting", 1, 4, "connection"))
    assert factory.bus.outbound.empty()


async def test_retry_completion_is_isolated_between_turns_in_one_session() -> None:
    bus = MessageBus()
    seen: list[TurnCompleted] = []
    bus.subscribe(seen.append, TurnCompleted)
    factory = TurnDeliveryFactory(bus)
    deliveries = [factory.create(InboundMessage(
        channel="websocket", sender_id="user", chat_id="chat", content="",
        metadata={WEBUI_TURN_METADATA_KEY: turn},
    ), "websocket:chat") for turn in ("first", "second")]
    await deliveries[0].events.emit(RetryStatusEvent("exhausted", 4, 4, "connection"))
    for delivery in reversed(deliveries):
        delivery.record_stop_reason("error")
        await delivery.complete(None, publish_completion=True)
    assert [(event.context.metadata[WEBUI_TURN_METADATA_KEY], event.failure_attempts)
            for event in seen] == [("second", None), ("first", 4)]


async def test_next_model_request_clears_exhaustion_within_the_same_turn() -> None:
    from unittest.mock import AsyncMock, patch

    from nanobot.providers.base import LLMResponse

    class Provider(LLMProvider):
        async def chat(self, **kwargs):
            return LLMResponse(content="payment required", finish_reason="error", error_status_code=402)

        def get_default_model(self):
            return "test"

    bus = MessageBus()
    completed: list[TurnCompleted] = []
    bus.subscribe(completed.append, TurnCompleted)
    msg = InboundMessage(channel="websocket", sender_id="user", chat_id="chat", content="")
    delivery = TurnDeliveryFactory(bus).create(msg, msg.session_key)
    await delivery.events.emit(RetryStatusEvent("exhausted", 4, 4, "connection"))
    with patch("nanobot.providers.base.asyncio.sleep", new_callable=AsyncMock):
        response = await Provider(provider_name="test").chat_stream_with_retry(
            [{"role": "user", "content": "continue"}],
            provider_context=ProviderCallContext(events=delivery.events),
        )
    delivery.record_stop_reason("error", failure_error_kind=LLMProvider.public_error_kind(response))
    await delivery.complete(None, publish_completion=True)
    assert completed[0].failure_error_kind == "billing"
    assert completed[0].failure_attempts is None

