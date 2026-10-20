from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mason.agent.goal_permission import goal_mutation_allowed
from mason.agent.loop import AgentLoop
from mason.bus.events import InboundMessage
from mason.bus.queue import MessageBus
from mason.command.builtin import (
    build_help_text,
    builtin_command_palette,
    cmd_goal,
    cmd_model,
    cmd_presets,
    register_builtin_commands,
)
from mason.command.router import CommandContext, CommandRouter
from mason.config.schema import GenerationPresetConfig
from mason.providers.factory import ProviderSnapshot
from mason.providers.model_catalog import ModelInfo, ProviderChoice
from mason.session.model_selection import (
    SESSION_MODEL_METADATA_KEY,
    SESSION_MODEL_PRESET_METADATA_KEY,
    SESSION_MODEL_PROVIDER_METADATA_KEY,
    model_preset_from_metadata,
)

_CATALOG = {
    "base": [
        ModelInfo("base-model", 1_000),
        ModelInfo("base-large", 8_000),
    ],
    "openai": [
        ModelInfo("gpt-4.1", 32_768),
        ModelInfo("gpt-4.1-mini", 16_000),
    ],
}
_CHOICES = (
    ProviderChoice("base", "Base", True),
    ProviderChoice("openai", "OpenAI", True),
)


def _provider(default_model: str, provider_name: str = "base", max_tokens: int = 123) -> MagicMock:
    provider = MagicMock()
    provider.provider_name = provider_name
    provider.get_default_model.return_value = default_model
    provider.generation = SimpleNamespace(
        max_tokens=max_tokens,
        temperature=0.1,
        reasoning_effort=None,
    )
    return provider


def _make_loop(tmp_path, *, model_presets=None, selection_snapshot_loader=None) -> AgentLoop:
    presets = model_presets or {
        "default": GenerationPresetConfig(max_tokens=123, context_window_tokens=1_000),
        "fast": GenerationPresetConfig(max_tokens=4_096, context_window_tokens=32_768),
    }

    def load_selection(model: str, provider: str | None, preset: str | None) -> ProviderSnapshot:
        provider_name = provider or ("openai" if model.startswith("gpt") else "base")
        context = next(
            (
                item.context_window
                for item in _CATALOG.get(provider_name, [])
                if item.id == model
            ),
            None,
        )
        generation = presets[preset or "default"].to_generation_settings()
        return ProviderSnapshot(
            provider=_provider(model, provider_name),
            model=model,
            context_window_tokens=context or 1_000,
            signature=(model, provider_name, preset),
            generation=generation,
            model_preset=preset,
        )

    return AgentLoop(
        bus=MessageBus(),
        provider=_provider("base-model", max_tokens=123),
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1_000,
        model_presets=presets,
        selection_snapshot_loader=selection_snapshot_loader or load_selection,
        model_catalog=lambda provider: _CATALOG.get(provider, []),
        provider_choices=lambda: list(_CHOICES),
    )


def _ctx(loop: AgentLoop, raw: str, args: str = "") -> CommandContext:
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=raw)
    return CommandContext(msg=msg, session=None, key=msg.session_key, raw=raw, args=args, loop=loop)


def _ctx_session(loop: AgentLoop, raw: str, args: str = "") -> CommandContext:
    msg = InboundMessage(channel="cli", sender_id="user", chat_id="direct", content=raw)
    return CommandContext(
        msg=msg, session=MagicMock(), key=msg.session_key, raw=raw, args=args, loop=loop,
        is_user_turn=True,
    )


def _saved_model_preset(loop: AgentLoop, session_key: str = "cli:direct") -> str | None:
    session = loop.sessions.get_or_create(session_key)
    return model_preset_from_metadata(session.metadata)


def _saved_model(loop: AgentLoop, session_key: str = "cli:direct") -> str | None:
    session = loop.sessions.get_or_create(session_key)
    value = session.metadata.get(SESSION_MODEL_METADATA_KEY)
    return value if isinstance(value, str) else None


@pytest.mark.asyncio
async def test_model_command_lists_current_model_and_providers(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    out = await cmd_model(_ctx(loop, "/model"))

    assert "Current model: `base-model`" in out.content
    assert "Provider: `base`" in out.content
    assert "Settings preset: `default`" in out.content
    assert "Available providers: `base`, `openai`" in out.content
    assert "`/presets`" in out.content
    assert out.metadata == {"render_as": "text"}


@pytest.mark.asyncio
async def test_model_command_lists_a_provider_catalog(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    out = await cmd_model(_ctx(loop, "/model openai", args="openai"))

    assert "## Models: `openai`" in out.content
    assert "`gpt-4.1`" in out.content
    assert "32,768 tokens" in out.content
    assert "`gpt-4.1-mini`" in out.content
    assert "Switch with `/model openai <model>`." in out.content


@pytest.mark.asyncio
async def test_model_command_switches_provider_and_model(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    out = await cmd_model(_ctx(loop, "/model openai gpt-4.1", args="openai gpt-4.1"))

    assert "Switched model for this session to `gpt-4.1`." in out.content
    assert "Provider: `openai`" in out.content
    assert "Settings preset: `default`" in out.content
    assert _saved_model(loop) == "gpt-4.1"
    session = loop.sessions.get_or_create("cli:direct")
    assert session.metadata[SESSION_MODEL_PROVIDER_METADATA_KEY] == "openai"
    # The default runtime is untouched by a session-scoped selection.
    assert loop.model == "base-model"


@pytest.mark.asyncio
async def test_model_command_accepts_provider_slash_model(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    out = await cmd_model(_ctx(loop, "/model openai/gpt-4.1-mini", args="openai/gpt-4.1-mini"))

    assert "Switched model for this session to `gpt-4.1-mini`." in out.content
    assert _saved_model(loop) == "gpt-4.1-mini"
    session = loop.sessions.get_or_create("cli:direct")
    assert session.metadata[SESSION_MODEL_PROVIDER_METADATA_KEY] == "openai"


@pytest.mark.asyncio
async def test_model_command_uses_current_provider_for_a_bare_model(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    out = await cmd_model(_ctx(loop, "/model base-large", args="base-large"))

    assert "Switched model for this session to `base-large`." in out.content
    assert "Provider: `base`" in out.content
    assert _saved_model(loop) == "base-large"


@pytest.mark.asyncio
async def test_model_command_does_not_change_another_session(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    await cmd_model(_ctx(loop, "/model openai gpt-4.1", args="openai gpt-4.1"))
    other = InboundMessage(channel="cli", sender_id="user", chat_id="other", content="/model")
    out = await cmd_model(
        CommandContext(msg=other, session=None, key=other.session_key, raw="/model", loop=loop)
    )

    assert "Current model: `base-model`" in out.content
    assert _saved_model(loop) == "gpt-4.1"


@pytest.mark.asyncio
async def test_model_command_reports_selection_errors(tmp_path) -> None:
    def fail_selection(_model: str, _provider: str | None, _preset: str | None):
        raise ValueError("No API key configured for provider 'openai'.")

    loop = _make_loop(tmp_path, selection_snapshot_loader=fail_selection)

    switched = await cmd_model(_ctx(loop, "/model openai gpt-4.1", args="openai gpt-4.1"))
    session = loop.sessions.get_or_create("cli:direct")
    session.metadata[SESSION_MODEL_METADATA_KEY] = "gpt-4.1"
    session.metadata[SESSION_MODEL_PROVIDER_METADATA_KEY] = "openai"
    loop.sessions.save(session)
    status = await cmd_model(_ctx(loop, "/model"))

    assert "Could not switch model" in switched.content
    assert "No API key configured for provider 'openai'." in switched.content
    assert "Current selection error" in status.content
    assert "No API key configured for provider 'openai'." in status.content


@pytest.mark.asyncio
async def test_model_command_does_not_depend_on_my_allow_set(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    assert loop.tools_config.my.allow_set is False

    await cmd_model(_ctx(loop, "/model openai gpt-4.1", args="openai gpt-4.1"))

    assert _saved_model(loop) == "gpt-4.1"


@pytest.mark.asyncio
async def test_model_command_registered_as_exact_and_prefix(tmp_path) -> None:
    router = CommandRouter()
    register_builtin_commands(router)
    loop = _make_loop(tmp_path)

    out = await router.dispatch(_ctx(loop, "/model openai gpt-4.1"))

    assert out is not None
    assert out.channel == "cli"
    assert out.chat_id == "direct"
    assert out.metadata == {"render_as": "text"}
    assert "Switched model for this session to `gpt-4.1`." in out.content
    assert _saved_model(loop) == "gpt-4.1"


@pytest.mark.asyncio
async def test_model_command_recovers_a_removed_settings_preset(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    session = loop.sessions.get_or_create("cli:direct")
    session.metadata[SESSION_MODEL_PRESET_METADATA_KEY] = "removed"
    loop.sessions.save(session)

    status = await loop.process_direct("/model", session_key="cli:direct")
    switched = await loop.process_direct(
        "/model openai gpt-4.1", session_key="cli:direct"
    )

    assert status is not None
    assert "model_preset 'removed' not found" in status.content
    assert switched is not None
    assert "Switched model for this session to `gpt-4.1`." in switched.content
    assert _saved_model(loop) == "gpt-4.1"


@pytest.mark.asyncio
async def test_presets_command_lists_settings_without_models(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    out = await cmd_presets(_ctx(loop, "/presets"))

    assert "## Presets" in out.content
    assert "Active: `default`" in out.content
    assert "Model stays: `base-model`" in out.content
    assert "`fast`: max_tokens=4,096" in out.content
    assert "model is never changed" in out.content


@pytest.mark.asyncio
async def test_presets_command_applies_settings_to_current_model(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    await cmd_model(_ctx(loop, "/model openai gpt-4.1", args="openai gpt-4.1"))

    out = await cmd_presets(_ctx(loop, "/presets fast", args="fast"))

    assert "Applied settings preset `fast`." in out.content
    assert "Model unchanged: `gpt-4.1`" in out.content
    assert _saved_model_preset(loop) == "fast"
    assert _saved_model(loop) == "gpt-4.1"
    session = loop.sessions.get_or_create("cli:direct")
    runtime = loop.runtime_for_session(session)
    assert runtime.generation.max_tokens == 4_096
    assert runtime.context_window_tokens == 32_768


@pytest.mark.asyncio
async def test_presets_command_default_restores_default_settings(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    await cmd_presets(_ctx(loop, "/presets fast", args="fast"))

    out = await cmd_presets(_ctx(loop, "/presets default", args="default"))

    assert "Applied settings preset `default`." in out.content
    assert _saved_model_preset(loop) == "default"
    session = loop.sessions.get_or_create("cli:direct")
    runtime = loop.runtime_for_session(session)
    assert runtime.generation.max_tokens == 123
    assert runtime.context_window_tokens == 1_000


@pytest.mark.asyncio
async def test_presets_command_unknown_lists_available(tmp_path) -> None:
    loop = _make_loop(tmp_path)

    out = await cmd_presets(_ctx(loop, "/presets missing", args="missing"))

    assert "Could not apply settings preset" in out.content
    assert "`default`" in out.content
    assert "`fast`" in out.content
    assert _saved_model_preset(loop) is None


def test_model_and_presets_commands_in_help_and_palette() -> None:
    palette = builtin_command_palette()

    model = next(item for item in palette if item["command"] == "/model")
    assert model["arg_hint"] == "[provider] [model]"
    assert model["lifecycle"] == "side_channel"
    assert model["accepts_args"] is True

    presets = next(item for item in palette if item["command"] == "/presets")
    assert presets["arg_hint"] == "[preset]"
    assert presets["lifecycle"] == "side_channel"
    assert presets["accepts_args"] is True

    help_text = build_help_text()
    assert "/model [provider] [model]" in help_text
    assert "/presets [preset]" in help_text


@pytest.mark.asyncio
async def test_goal_command_shows_usage_without_args(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    out = await cmd_goal(_ctx(loop, "/goal"))
    assert out is not None
    assert out.channel == "cli"
    assert out.chat_id == "direct"
    assert out.metadata == {"render_as": "text"}
    assert out.content == "Usage: /goal <long-running task description>"


@pytest.mark.asyncio
async def test_goal_command_rejects_mid_turn_without_session(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    out = await cmd_goal(_ctx(loop, "/goal do work", args="do work"))
    assert out is not None
    assert out.channel == "cli"
    assert out.chat_id == "direct"
    assert out.metadata == {"render_as": "text"}
    assert out.content == (
        "A task is already running for this chat. "
        "Use `/stop` first, then send `/goal <long-running task description>` again."
    )


@pytest.mark.asyncio
async def test_goal_command_marks_turn_and_preserves_explicit_request(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    ctx = _ctx_session(loop, "/goal audit the repo", args="audit the repo")
    out = await cmd_goal(ctx)
    assert out is None
    assert ctx.msg.content == "/goal audit the repo"
    assert ctx.msg.metadata.get("original_command") == "/goal"
    assert ctx.msg.metadata.get("original_content") == "/goal audit the repo"
    assert ctx.msg.metadata.get("goal_requested") is True
    assert isinstance(ctx.msg.metadata.get("goal_started_at"), int | float)
    assert len(ctx.turn_scopes) == 1
    with ctx.turn_scopes[0]:
        assert goal_mutation_allowed() is True
    assert goal_mutation_allowed() is False


@pytest.mark.asyncio
async def test_goal_command_registered_on_router(tmp_path) -> None:
    router = CommandRouter()
    register_builtin_commands(router)
    loop = _make_loop(tmp_path)
    ctx = _ctx_session(loop, "/goal ship it", args="ship it")
    out = await router.dispatch(ctx)
    assert out is None
    assert "ship it" in ctx.msg.content
    assert len(ctx.turn_scopes) == 1
    with ctx.turn_scopes[0]:
        assert goal_mutation_allowed() is True
    assert goal_mutation_allowed() is False


@pytest.mark.asyncio
async def test_goal_command_does_not_allow_internal_turn(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    ctx = CommandContext(
        msg=InboundMessage(
            channel="cli",
            sender_id="system",
            chat_id="direct",
            content="/goal internal work",
        ),
        session=MagicMock(),
        key="cli:direct",
        raw="/goal internal work",
        args="internal work",
        loop=loop,
        is_user_turn=False,
    )

    out = await cmd_goal(ctx)

    assert out is not None
    assert "only be started by a user" in out.content
    assert ctx.turn_scopes == []


def test_goal_command_in_help_and_palette() -> None:
    palette = builtin_command_palette()
    goal = next(item for item in palette if item["command"] == "/goal")
    assert goal["arg_hint"] == "<goal>"
    assert goal["lifecycle"] == "agent_turn_with_args"
    assert goal["accepts_args"] is True
    assert "/goal <goal>" in build_help_text()
