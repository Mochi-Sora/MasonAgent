"""Actual invocation identity survives delivery and the display transcript."""

import asyncio
from copy import deepcopy
from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from agent.runner_helpers import failed_test_consolidator
from nanobot.agent.progress_hook import AgentProgressHook
from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.turn_delivery import TurnDeliveryFactory
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.outbound_events import StreamDeltaEvent, StreamEndEvent
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import Config, ModelPresetConfig
from nanobot.events import EventSink, ResponseSource, ResponseSourceEvent
from nanobot.providers.base import LLMProvider, LLMResponse, ProviderCallContext
from nanobot.providers.factory import make_provider, provider_signature
from nanobot.providers.fallback_provider import FallbackProvider
from nanobot.utils.llm_runtime import LLMRuntime


class AnswerProvider(LLMProvider):
    def __init__(self, name, text="answer", *, fail=False, chunks=()):
        super().__init__(provider_name=name)
        self.text, self.fail, self.chunks = text, fail, chunks

    def get_default_model(self):
        return "same-model"

    async def chat(self, **kwargs):
        return LLMResponse(
            content=self.text,
            finish_reason="error" if self.fail else "stop",
            error_status_code=401 if self.fail else None,
        )

    async def chat_stream(self, on_content_delta=None, **kwargs):
        if on_content_delta:
            for text in self.chunks:
                await asyncio.sleep(0)
                await on_content_delta(text)
        return await self.chat(**kwargs)


def delivery(chat="chat", *, streaming=True):
    bus = MessageBus()
    turn = TurnDeliveryFactory(bus).create(InboundMessage(
        channel="websocket", sender_id="user", chat_id=chat, content="hello",
        metadata={"_wants_stream": streaming, "webui_turn_id": f"turn-{chat}"},
    ), f"websocket:{chat}", enable_stream=streaming)
    return bus, turn



async def test_fallback_flag_is_call_scoped_even_with_identical_identity():
    seen = []

    async def publish(event):
        if isinstance(event, ResponseSourceEvent) and event.source is not None:
            seen.append(event.source)

    primary = AnswerProvider("openai", fail=True)
    provider = FallbackProvider(
        primary, [ModelPresetConfig(model="same-model")], lambda _: AnswerProvider("openai"),
        fallback_preset_names=["chosen"],
    )
    context = ProviderCallContext(events=EventSink(publish), response_preset="chosen")
    await provider.chat_with_retry(messages=[], provider_context=context)
    primary.fail = False
    await provider.chat_with_retry(messages=[], provider_context=context)
    assert seen == [
        ResponseSource("openai", "same-model", "chosen", fallback=True),
        ResponseSource("openai", "same-model", "chosen", fallback=False),
    ]


async def test_auxiliary_calls_do_not_publish_response_identity():
    seen = []

    async def publish(event):
        seen.append(event)

    await AnswerProvider("auxiliary").chat_stream_with_retry(
        messages=[], provider_context=ProviderCallContext(events=EventSink(publish)),
    )
    assert not any(isinstance(event, ResponseSourceEvent) for event in seen)


@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.parametrize("with_images", [False, True])
@pytest.mark.parametrize("fallback", [False, True])
async def test_attribution_does_not_change_provider_requests(with_tools, with_images, fallback):
    """Display metadata must not modify the cacheable prompt or generation payload."""
    requests = []

    class CapturingProvider(AnswerProvider):
        async def chat(self, **kwargs):
            requests.append((self.provider_name, deepcopy({
                key: value for key, value in kwargs.items() if not key.startswith("on_")
            })))
            if with_images and len(requests) == 1:
                return LLMResponse(content="Images unsupported", finish_reason="error",
                                   error_status_code=400)
            return await super().chat(**kwargs)

    messages = [{"role": "system", "content": "Stable system prompt"},
                {"role": "user", "content": "Hello"}]
    if with_images:
        messages[-1]["content"] = [
            {"type": "text", "text": "Hello"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ]
    tools = [{"type": "function", "function": {"name": "test_tool", "parameters": {
        "type": "object", "properties": {},
    }}}] if with_tools else None
    original = deepcopy(messages)
    results = []
    for preset in (None, "primary"):
        requests.clear()
        provider = FallbackProvider(
            CapturingProvider("primary", fail=fallback),
            [ModelPresetConfig(model="same-model")], lambda _: CapturingProvider("backup"),
            fallback_preset_names=["backup"],
        )
        result = await provider.chat_stream_with_retry(
            messages=deepcopy(messages), tools=tools, model="same-model",
            provider_context=ProviderCallContext(events=EventSink(AsyncMock()), response_preset=preset),
        )
        assert result.content == "answer"
        results.append(deepcopy(requests))
    assert results[0] == results[1]
    assert messages == original


@pytest.mark.parametrize("streaming", [False, True])
async def test_synthetic_final_text_does_not_inherit_an_earlier_model(streaming):
    bus, turn = delivery(streaming=streaming)
    await turn.events.emit(ResponseSourceEvent(ResponseSource("openai", "gpt", "codex"), "Working"))
    if streaming:
        await turn.events.emit(StreamDeltaEvent("Reached the iteration limit"))
        await turn.events.emit(StreamEndEvent())
    else:
        await turn.complete(OutboundMessage(channel="websocket", chat_id="chat", content="Reached the iteration limit"), publish_completion=False)
    while not bus.outbound.empty():
        assert not bus.outbound.get_nowait().metadata.get("response_sources")


async def test_unnamed_fallback_does_not_borrow_the_primary_preset():
    bus, turn = delivery()
    provider = FallbackProvider(AnswerProvider("openai", fail=True), [ModelPresetConfig(model="grok")],
                                lambda _: AnswerProvider("xai", chunks=("answer",)))

    async def delta(text):
        await turn.events.emit(StreamDeltaEvent(text))

    await provider.chat_stream_with_retry(messages=[], on_content_delta=delta,
        provider_context=ProviderCallContext(events=turn.events, response_preset="primary"))
    await turn.events.emit(StreamEndEvent())
    while not bus.outbound.empty():
        assert not bus.outbound.get_nowait().metadata.get("response_sources")


async def test_concurrent_calls_and_segments_have_independent_snapshots():
    async def run(chat, preset):
        bus, turn = delivery(chat)

        async def delta(text):
            await turn.events.emit(StreamDeltaEvent(text))

        await AnswerProvider("openai", chunks=("one", "two")).chat_stream_with_retry(
            messages=[], on_content_delta=delta,
            provider_context=ProviderCallContext(events=turn.events, response_preset=preset),
        )
        await turn.events.emit(StreamEndEvent())
        return [bus.outbound.get_nowait() for _ in range(bus.outbound.qsize())]

    left, right = await asyncio.gather(run("left", "writer"), run("right", "reviewer"))
    assert {m.metadata["response_sources"][0]["preset"] for m in left if isinstance(m.event, StreamDeltaEvent | StreamEndEvent)} == {"writer"}
    assert {m.metadata["response_sources"][0]["preset"] for m in right if isinstance(m.event, StreamDeltaEvent | StreamEndEvent)} == {"reviewer"}


async def test_merged_segments_keep_all_actual_sources_and_unknown_text_is_not_mislabeled():
    bus, turn = delivery()
    a = ResponseSource("openai", "a", "writer")
    b = ResponseSource("xai", "b", "reviewer", fallback=True)
    for source in (a, b):
        await turn.events.emit(ResponseSourceEvent(source))
        await turn.events.emit(StreamDeltaEvent("text"))
        await turn.events.emit(StreamEndEvent(resuming=True, merge_next=True))
    assert bus.outbound.get_nowait().metadata["response_sources"] == [asdict(a)]
    bus.outbound.get_nowait()
    assert bus.outbound.get_nowait().metadata["response_sources"] == [asdict(a), asdict(b)]
    bus.outbound.get_nowait()
    await turn.events.emit(ResponseSourceEvent(None))
    await turn.events.emit(StreamDeltaEvent("unattributed text"))
    await turn.events.emit(StreamEndEvent())
    assert bus.outbound.get_nowait().metadata["response_sources"] == []
    assert bus.outbound.get_nowait().metadata["response_sources"] == []
    await turn.events.emit(ResponseSourceEvent(b))
    await turn.events.emit(StreamDeltaEvent("new segment"))
    assert bus.outbound.get_nowait().metadata["response_sources"] == [asdict(b)]


def test_factory_captures_names_even_when_models_and_settings_are_identical():
    config = Config.model_validate({
        "agents": {"defaults": {"modelPreset": "primary", "fallbackModels": ["backup"]}},
        "modelPresets": {name: {"provider": "openai", "model": "same-model"}
                         for name in ("primary", "backup", "renamed")},
        "providers": {"openai": {"apiKey": "test-only"}},
    })
    before = provider_signature(config)
    provider = make_provider(config)
    config.agents.defaults.fallback_models = ["renamed"]
    assert provider_signature(config) != before
    assert provider._fallback_preset_names == ("backup",)
    assert make_provider(config)._fallback_preset_names == ("renamed",)



async def test_runner_records_each_provider_when_streaming_times_out_and_recovers():
    class StalledProvider(AnswerProvider):
        _CHAT_RETRY_DELAYS = ()

        async def chat(self, **kwargs):
            return LLMResponse(content="stalled", finish_reason="error", error_kind="timeout")

    bus, turn = delivery()
    primary = StalledProvider("openai_codex", chunks=("Partial answer",))
    backup = AnswerProvider("xai", text="Recovered answer", chunks=("Recovered answer",))
    provider = FallbackProvider(primary, [ModelPresetConfig(model="grok")], lambda _: backup,
                                fallback_preset_names=["grok"])
    runtime = LLMRuntime.capture(provider, "gpt", context_window_tokens=32_000, model_preset="codex")
    result = await AgentRunner().run(AgentRunSpec(
        initial_messages=[{"role": "user", "content": "hello"}], tools=ToolRegistry(),
        runtime=runtime, max_iterations=2, max_tool_result_chars=4096,
        session_key="websocket:chat", events=turn.events,
        hook=AgentProgressHook(turn.events, streaming=True),
        consolidate_history=failed_test_consolidator,
    ))
    assert result.final_content == "Recovered answer"
    outgoing = [bus.outbound.get_nowait() for _ in range(bus.outbound.qsize())]
    ends = [msg for msg in outgoing if isinstance(msg.event, StreamEndEvent)]
    assert [m.metadata["response_sources"][0]["preset"] for m in ends if m.metadata["response_sources"]] == ["codex", "grok"]
    assert [m.metadata["response_sources"][0]["fallback"] for m in ends if m.metadata["response_sources"]] == [False, True]
    assert len({m.event.stream_id for m in ends}) == len(ends)
    # Display provenance stays out of canonical model input/history.
    assert all("response_sources" not in message for message in result.messages)


