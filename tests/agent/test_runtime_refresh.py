import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mason.agent.loop import AgentLoop
from mason.agent.model_runtime import ModelRuntimeResolver
from mason.bus.queue import MessageBus
from mason.bus.runtime_events import RuntimeModelChanged
from mason.config.errors import ConfigLoadError
from mason.config.schema import GenerationPresetConfig
from mason.providers.base import GenerationSettings
from mason.providers.factory import ProviderSnapshot, load_provider_snapshot
from mason.session.model_selection import (
    SESSION_MODEL_PRESET_METADATA_KEY,
    model_preset_from_metadata,
)
from mason.style import StyleSettings
from mason.utils.llm_runtime import LLMRuntime


def _provider(default_model: str, max_tokens: int = 123) -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = default_model
    provider.generation = SimpleNamespace(max_tokens=max_tokens)
    return provider


def test_refresh_adopts_generation_change_without_signature_change() -> None:
    """A style-only change must not be discarded when the signature is unchanged."""
    provider = _provider("model")
    initial = LLMRuntime(
        provider=provider,
        model="model",
        generation=GenerationSettings(style=StyleSettings()),
        context_window_tokens=1000,
        model_preset="primary",
        snapshot_signature=("model", "provider"),
    )
    resolver = ModelRuntimeResolver(
        initial,
        model_presets={
            "primary": GenerationPresetConfig(base_style="seductive"),
        },
        configured_default_preset="primary",
        provider_snapshot_loader=lambda: ProviderSnapshot(
            provider=provider,
            model="model",
            context_window_tokens=1000,
            signature=("model", "provider"),
            generation=initial.generation,
            model_preset="primary",
        ),
    )

    resolver.invalidate()
    runtime = resolver.admit()

    assert runtime.generation.style.base_style == "seductive"
    assert runtime.model_preset == "primary"


def test_provider_refresh_updates_runtime_resolver(tmp_path: Path) -> None:
    old_provider = _provider("old-model")
    new_provider = _provider("new-model", max_tokens=456)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=old_provider,
        workspace=tmp_path,
        model="old-model",
        context_window_tokens=1000,
        provider_snapshot_loader=lambda: ProviderSnapshot(
            provider=new_provider,
            model="new-model",
            context_window_tokens=2000,
            signature=("new-model",),
        ),
    )
    loop.runtime_resolver.invalidate()

    runtime = loop.llm_runtime()

    assert runtime is loop.runtime_resolver.runtime
    assert loop.provider is new_provider
    assert loop.model == "new-model"
    assert loop.context_window_tokens == 2000


def test_llm_runtime_refreshes_provider_snapshot(tmp_path: Path) -> None:
    old_provider = _provider("old-model")
    new_provider = _provider("new-model", max_tokens=456)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=old_provider,
        workspace=tmp_path,
        model="old-model",
        context_window_tokens=1000,
        provider_snapshot_loader=lambda: ProviderSnapshot(
            provider=new_provider,
            model="new-model",
            context_window_tokens=2000,
            signature=("new-model",),
        ),
    )
    loop.runtime_resolver.invalidate()

    runtime = loop.llm_runtime()

    assert runtime.provider is new_provider
    assert runtime.model == "new-model"
    assert loop.provider is new_provider


def test_llm_runtime_surfaces_invalidated_config_errors(tmp_path: Path) -> None:
    def fail_refresh() -> ProviderSnapshot:
        raise ValueError("invalid config")

    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider("old-model"),
        workspace=tmp_path,
        model="old-model",
        context_window_tokens=1000,
        provider_snapshot_loader=fail_refresh,
    )
    loop.runtime_resolver.invalidate()

    with pytest.raises(ValueError, match="invalid config"):
        loop.llm_runtime()


def test_provider_snapshot_missing_env_reports_explicit_config_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    name = "MASON_TEST_REFRESH_MISSING_KEY"
    monkeypatch.delenv(name, raising=False)
    config_path = tmp_path / "custom.json"
    config_path.write_text(
        json.dumps({"providers": {"openrouter": {"apiKey": f"${{{name}}}"}}}),
        encoding="utf-8",
    )

    with pytest.raises(ConfigLoadError) as exc_info:
        load_provider_snapshot(config_path)

    assert exc_info.value.path == config_path


def test_same_snapshot_default_clears_preset_and_publishes_update(tmp_path: Path) -> None:
    base_provider = _provider("base-model")
    fast_provider = _provider("fast-model")
    fast_snapshot = ProviderSnapshot(
        provider=fast_provider,
        model="fast-model",
        context_window_tokens=2000,
        signature=("fast-model", "auto", "same-runtime"),
    )
    published: list[tuple[str, str | None]] = []
    loop = AgentLoop(
        bus=MessageBus(),
        provider=base_provider,
        workspace=tmp_path,
        model="base-model",
        context_window_tokens=1000,
        provider_signature=("base-model", "auto", "initial"),
        provider_snapshot_loader=lambda: fast_snapshot,
        model_presets={"fast": GenerationPresetConfig(max_tokens=4096)},
        model_preset="fast",
        preset_snapshot_loader=lambda _name: fast_snapshot,
        runtime_model_publisher=lambda model, preset: published.append((model, preset)),
    )
    loop.runtime_resolver.invalidate()

    runtime = loop.llm_runtime()

    assert runtime.model_preset is None
    assert loop.model_preset is None
    assert published == [("fast-model", None)]


def test_named_default_refresh_is_used_by_sessions_without_override(tmp_path: Path) -> None:
    provider = _provider("shared-model")
    shared_signature = ("shared-model", "auto", "same-settings")
    snapshots = {
        "fast": ProviderSnapshot(
            provider=provider,
            model="shared-model",
            context_window_tokens=16_000,
            signature=shared_signature,
            model_preset="fast",
        ),
        "deep": ProviderSnapshot(
            provider=provider,
            model="shared-model",
            context_window_tokens=16_000,
            signature=shared_signature,
            model_preset="deep",
        ),
    }
    default_snapshot = snapshots["deep"]
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="shared-model",
        context_window_tokens=16_000,
        provider_signature=shared_signature,
        provider_snapshot_loader=lambda: default_snapshot,
        model_presets={name: GenerationPresetConfig() for name in snapshots},
        model_preset="fast",
        preset_snapshot_loader=snapshots.__getitem__,
    )

    loop.runtime_resolver.invalidate()
    runtime = loop.llm_runtime()
    session = loop.sessions.get_or_create("sdk:new-after-refresh")

    assert runtime.model_preset == "deep"
    assert loop.runtime_for_session(session).model_preset == "deep"
    assert model_preset_from_metadata(session.metadata) is None


@pytest.mark.asyncio
async def test_config_invalidation_defers_canonical_notification_until_default_refresh(
    tmp_path: Path,
) -> None:
    provider = _provider("model-a")
    catalog = {"fast": GenerationPresetConfig(max_tokens=4096)}
    current_model = "model-a"
    published: list[RuntimeModelChanged] = []

    def load_selection(model: str, _provider: str | None, preset: str | None) -> ProviderSnapshot:
        return ProviderSnapshot(
            provider=provider,
            model=model,
            context_window_tokens=16_000,
            signature=(model, preset),
            generation=catalog.get(
                preset or "default", GenerationPresetConfig()
            ).to_generation_settings(),
            model_preset=preset,
        )

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="model-a",
        context_window_tokens=16_000,
        provider_signature=("model-a", "auto"),
        provider_snapshot_loader=lambda: load_selection(current_model, None, "fast"),
        model_presets=catalog,
        preset_catalog_loader=lambda: catalog,
        model_preset="fast",
        selection_snapshot_loader=load_selection,
    )
    loop.bus.subscribe(published.append, RuntimeModelChanged)
    session = loop.sessions.get_or_create("websocket:chat")
    session.metadata[SESSION_MODEL_PRESET_METADATA_KEY] = "fast"
    current_model = "model-b"

    loop.invalidate_runtime_config()
    runtime = loop.runtime_for_session(session)
    await asyncio.sleep(0)

    assert published == []
    assert runtime.model == "model-b"
    assert loop.model_presets["fast"].max_tokens == 4096

    assert loop.llm_runtime().model == "model-b"
    await asyncio.sleep(0)

    assert [(event.model, event.model_preset) for event in published] == [
        ("model-b", "fast"),
    ]


@pytest.mark.asyncio
async def test_config_refresh_publishes_renamed_canonical_preset(tmp_path: Path) -> None:
    provider = _provider("model-a")
    catalog = {"fast": GenerationPresetConfig(max_tokens=4096)}
    default_name = "fast"
    published: list[RuntimeModelChanged] = []

    def load_selection(model: str, _provider: str | None, preset: str | None) -> ProviderSnapshot:
        return ProviderSnapshot(
            provider=provider,
            model=model,
            context_window_tokens=16_000,
            signature=(model, preset),
            generation=catalog.get(
                preset or "default", GenerationPresetConfig()
            ).to_generation_settings(),
            model_preset=preset,
        )

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="model-a",
        context_window_tokens=16_000,
        provider_signature=("model-a", "fast"),
        provider_snapshot_loader=lambda: load_selection("model-a", None, default_name),
        model_presets=catalog,
        preset_catalog_loader=lambda: catalog,
        model_preset="fast",
        selection_snapshot_loader=load_selection,
    )
    loop.bus.subscribe(published.append, RuntimeModelChanged)
    catalog["Codex"] = catalog.pop("fast")
    default_name = "Codex"

    runtime = loop.refresh_runtime_config()
    await asyncio.sleep(0)

    assert (runtime.model, runtime.model_preset) == ("model-a", "Codex")
    assert [(event.model, event.model_preset) for event in published] == [
        ("model-a", "Codex"),
    ]


def test_next_turn_captures_generation_changed_after_previous_admission(
    tmp_path: Path,
) -> None:
    provider = _provider("test-model")
    provider.generation = GenerationSettings(temperature=0.2, max_tokens=1024)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=16_384,
    )

    first = loop.llm_runtime()
    provider.generation = GenerationSettings(temperature=0.8, max_tokens=512)
    second = loop.llm_runtime()

    assert first.generation.temperature == 0.2
    assert first.generation.max_tokens == 1024
    assert second.generation.temperature == 0.8
    assert second.generation.max_tokens == 512
