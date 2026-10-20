from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mason.agent.loop import AgentLoop
from mason.agent.tools.context import RequestContext, request_context
from mason.agent.tools.registry import ToolRegistry
from mason.agent.tools.runtime_control import AgentRuntimeControl
from mason.agent.tools.self import MyTool
from mason.bus.queue import MessageBus
from mason.config.schema import GenerationPresetConfig
from mason.providers.factory import ProviderSnapshot
from mason.session.model_selection import model_preset_from_metadata


def _provider(default_model: str, max_tokens: int = 123) -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = default_model
    provider.generation = SimpleNamespace(
        max_tokens=max_tokens, temperature=0.1, reasoning_effort=None
    )
    return provider


def _make_loop(tmp_path, presets=None, active_preset=None):
    provider = _provider("base-model")
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1000,
        model_presets=presets or {},
        model_preset=active_preset,
    )


def _my_tool(loop: AgentLoop) -> MyTool:
    return MyTool(
        runtime_control=AgentRuntimeControl(loop),
        modify_allowed=True,
    )


def test_model_preset_getter_none_when_not_set(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    assert loop.model_preset is None


def test_model_preset_setter_updates_state(tmp_path) -> None:
    presets = {
        "fast": GenerationPresetConfig(
            max_tokens=4096,
            context_window_tokens=32_768,
            temperature=0.5,
            reasoning_effort="low",
        )
    }
    loop = _make_loop(tmp_path, presets=presets)
    loop.model_preset = "fast"

    assert loop.model_preset == "fast"
    assert loop.model == "base-model"
    assert loop.context_window_tokens == 32_768
    runtime = loop.llm_runtime()
    assert runtime.generation.temperature == 0.5
    assert runtime.generation.max_tokens == 4096
    assert runtime.generation.reasoning_effort == "low"
    assert loop.llm_runtime().model == "base-model"
    assert loop.llm_runtime().context_window_tokens == 32_768
    assert loop.llm_runtime().generation.max_tokens == 4096


def test_model_preset_setter_calls_runtime_model_publisher(tmp_path) -> None:
    published: list[tuple[str, str | None]] = []
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider("base-model", max_tokens=123),
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1000,
        model_presets={"fast": GenerationPresetConfig(max_tokens=4096)},
        runtime_model_publisher=lambda model, preset: published.append((model, preset)),
    )

    loop.set_model_preset("fast")

    assert published == [("base-model", "fast")]


def test_model_preset_setter_keeps_provider_and_model(tmp_path) -> None:
    old_provider = _provider("base-model", max_tokens=123)
    preset = GenerationPresetConfig(
        max_tokens=2048,
        context_window_tokens=200_000,
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=old_provider,
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1000,
        model_presets={"deep": preset},
    )

    loop.set_model_preset("deep")

    assert loop.provider is old_provider
    assert loop.model == "base-model"
    assert loop.context_window_tokens == 200_000
    assert loop.llm_runtime().generation.max_tokens == 2048


def test_model_preset_setter_failure_leaves_old_state(tmp_path) -> None:
    preset = GenerationPresetConfig(max_tokens=4096)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider("base-model", max_tokens=123),
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1000,
        model_presets={"fast": preset},
        selection_snapshot_loader=lambda *_args: (_ for _ in ()).throw(
            RuntimeError("provider unavailable")
        ),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        loop.set_model_preset("fast")

    assert loop.model_preset is None
    assert loop.model == "base-model"
    assert loop.context_window_tokens == 1000
    assert loop.llm_runtime().generation.max_tokens == 123


def test_active_model_preset_survives_unchanged_config_refresh(tmp_path) -> None:
    base_provider = _provider("base-model", max_tokens=123)
    default_snapshot = ProviderSnapshot(
        provider=base_provider,
        model="base-model",
        context_window_tokens=1000,
        signature=("base-model", "auto", "openai", "sk-old"),
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=base_provider,
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1000,
        provider_signature=default_snapshot.signature,
        model_presets={"fast": GenerationPresetConfig(max_tokens=4096)},
        provider_snapshot_loader=lambda: default_snapshot,
    )

    loop.set_model_preset("fast")
    loop.runtime_resolver.invalidate()
    loop.llm_runtime()

    assert loop.model_preset == "fast"
    assert loop.provider is base_provider
    assert loop.model == "base-model"
    assert loop.llm_runtime().generation.max_tokens == 4096


def test_config_model_refresh_clears_active_model_preset(tmp_path) -> None:
    base_provider = _provider("base-model", max_tokens=123)
    webui_provider = _provider("anthropic/claude-opus-4-5", max_tokens=2048)
    webui_snapshot = ProviderSnapshot(
        provider=webui_provider,
        model="anthropic/claude-opus-4-5",
        context_window_tokens=200_000,
        signature=("anthropic/claude-opus-4-5", "anthropic", "anthropic", "sk-old"),
    )
    loop = AgentLoop(
        bus=MessageBus(),
        provider=base_provider,
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1000,
        provider_snapshot_loader=lambda: webui_snapshot,
        provider_signature=("base-model", "auto", "openai", "sk-old"),
        model_presets={"fast": GenerationPresetConfig(max_tokens=4096)},
    )

    loop.set_model_preset("fast")
    loop.runtime_resolver.invalidate()
    loop.llm_runtime()

    assert loop.model_preset is None
    assert loop.provider is webui_provider
    assert loop.model == "anthropic/claude-opus-4-5"
    assert loop.context_window_tokens == 200_000


def test_model_preset_setter_raises_on_unknown(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    with pytest.raises(KeyError, match="model_preset 'missing' not found"):
        loop.model_preset = "missing"


def test_model_preset_setter_raises_on_empty_string(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    with pytest.raises(ValueError, match="model_preset must be a non-empty string"):
        loop.model_preset = ""


def test_self_tool_inspect_shows_model_preset(tmp_path) -> None:
    presets = {
        "fast": GenerationPresetConfig(max_tokens=4096),
    }
    loop = _make_loop(tmp_path, presets=presets, active_preset="fast")
    tool = _my_tool(loop)
    output = tool._inspect_all()
    assert "model_preset: 'fast'" in output


def test_self_tool_set_model_preset_via_modify(tmp_path) -> None:
    presets = {
        "fast": GenerationPresetConfig(max_tokens=4096),
    }
    loop = _make_loop(tmp_path, presets=presets)
    tool = _my_tool(loop)
    result = tool._modify("model_preset", "fast")
    assert "Error" not in result
    assert loop.model_preset == "fast"
    assert loop.model == "base-model"


def test_self_tool_set_model_preset_switches_back_to_default(tmp_path) -> None:
    presets = {
        "default": GenerationPresetConfig(context_window_tokens=1000),
        "fast": GenerationPresetConfig(context_window_tokens=32_768),
    }
    loop = _make_loop(tmp_path, presets=presets, active_preset="fast")
    tool = _my_tool(loop)

    result = tool._modify("model_preset", "default")

    assert "Error" not in result
    assert "model stays 'base-model'" in result
    assert loop.model_preset is None
    assert loop.model == "base-model"
    assert loop.context_window_tokens == 1000


def test_self_tool_set_model_preset_unknown_lists_available(tmp_path) -> None:
    presets = {
        "default": GenerationPresetConfig(),
        "fast": GenerationPresetConfig(max_tokens=4096),
    }
    loop = _make_loop(tmp_path, presets=presets)
    tool = _my_tool(loop)

    result = tool._modify("model_preset", "missing")

    assert result == "Error: model_preset 'missing' not found. Available: default, fast."
    assert loop.model_preset is None
    assert loop.model == "base-model"


def test_self_tool_sets_model_preset_for_current_session(tmp_path) -> None:
    presets = {
        "default": GenerationPresetConfig(),
        "fast": GenerationPresetConfig(max_tokens=4096),
    }
    loop = _make_loop(tmp_path, presets=presets)
    tool = _my_tool(loop)

    with request_context(RequestContext(
        channel="cli",
        chat_id="one",
        session_key="cli:one",
        metadata={"source": "self-tool"},
    )):
        result = tool._modify("model_preset", "fast")

    assert "for the next turn" in result
    assert model_preset_from_metadata(
        loop.sessions.get_or_create("cli:one").metadata
    ) == "fast"
    assert loop.model_preset is None
    assert loop.model == "base-model"


def test_self_tool_reports_session_preset_provider_configuration_error(tmp_path) -> None:
    loop = _make_loop(tmp_path)
    loop.set_session_model_preset = MagicMock(
        side_effect=ValueError("No API key configured for provider 'openai'.")
    )
    tool = _my_tool(loop)

    with request_context(RequestContext(
        channel="cli",
        chat_id="one",
        session_key="cli:one",
    )):
        result = tool._modify("model_preset", "broken")

    assert result == "Error: No API key configured for provider 'openai'."


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("model", "other-model"),
        ("context_window_tokens", 8_192),
    ],
)
def test_self_tool_rejects_instance_runtime_changes_in_session(
    tmp_path,
    key: str,
    value: object,
) -> None:
    loop = _make_loop(tmp_path)
    tool = _my_tool(loop)
    session = loop.sessions.get_or_create("cli:one")

    with request_context(RequestContext(
        channel="cli",
        chat_id="one",
        session_key=session.key,
        runtime=loop.runtime_for_session(session),
    )):
        result = tool._modify(key, value)

    other_runtime = loop.runtime_for_session(loop.sessions.get_or_create("cli:two"))
    assert "instance-wide and disabled" in result
    assert "model_preset" in result
    assert other_runtime.model == "base-model"
    assert other_runtime.context_window_tokens == 1000


def test_self_tool_set_model_keeps_active_preset(tmp_path) -> None:
    presets = {
        "fast": GenerationPresetConfig(max_tokens=4096),
    }
    loop = _make_loop(tmp_path, presets=presets, active_preset="fast")
    tool = _my_tool(loop)
    result = tool._modify("model", "anthropic/claude-opus-4-5")
    assert "Error" not in result
    assert loop.model_preset == "fast"
    assert loop.model == "anthropic/claude-opus-4-5"


def test_from_config_injects_default_preset(tmp_path) -> None:
    from unittest.mock import patch

    from mason.config.schema import Config
    config = Config.model_validate({
        "agents": {"defaults": {"model": "openai/gpt-4.1", "workspace": str(tmp_path)}},
    })
    fake_provider = _provider("openai/gpt-4.1")
    with patch("mason.providers.factory.make_provider", return_value=fake_provider):
        loop = AgentLoop.from_config(config, tool_registry=ToolRegistry())
    assert loop.model == "openai/gpt-4.1"
    assert loop.model_preset is None
    assert "default" in loop.model_presets
    assert loop.model_presets["default"].model is None
    assert loop.model_presets["default"].max_tokens == 8192


def test_from_config_static_preset_loader_does_not_enable_hot_reload(tmp_path) -> None:
    from unittest.mock import patch

    from mason.config.schema import Config
    config = Config.model_validate({
        "agents": {"defaults": {"model": "openai/gpt-4.1", "workspace": str(tmp_path)}},
        "model_presets": {"fast": {"max_tokens": 4096}},
    })
    fake_provider = _provider("openai/gpt-4.1")
    with patch("mason.providers.factory.make_provider", return_value=fake_provider):
        loop = AgentLoop.from_config(config, tool_registry=ToolRegistry())
        default_runtime = loop.runtime_resolver.runtime
        resolved = loop.runtime_resolver.resolve_preset("fast")
    # A preset never changes the model; it only carries settings.
    assert resolved.model == "openai/gpt-4.1"
    assert resolved.generation.max_tokens == 4096
    assert loop.runtime_resolver.runtime is default_runtime
