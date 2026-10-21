"""Public resolution boundary for default and overridden LLM runtimes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import cast

from mason.agent import model_presets as preset_helpers
from mason.config.schema import Config, GenerationPresetConfig
from mason.providers.base import LLMProvider
from mason.providers.factory import ProviderSnapshot, build_provider_snapshot
from mason.utils.llm_runtime import LLMRuntime, runtime_from_provider_snapshot


class ModelRuntimeResolver:
    """Own model selection and resolve it to immutable execution values.

    The resolver is deliberately independent of ``AgentLoop``.  Command, SDK,
    and tool admission layers can depend on this public service without reading
    or mutating private loop state.
    """

    def __init__(
        self,
        initial_runtime: LLMRuntime,
        *,
        model_presets: Mapping[str, GenerationPresetConfig] | None = None,
        preset_catalog_loader: preset_helpers.PresetCatalogLoader | None = None,
        configured_default_preset: str | None = None,
        provider_snapshot_loader: Callable[[], ProviderSnapshot] | None = None,
        preset_snapshot_loader: preset_helpers.PresetSnapshotLoader | None = None,
        selection_snapshot_loader: preset_helpers.SelectionSnapshotLoader | None = None,
        provider_observer: Callable[[LLMProvider], None] | None = None,
    ) -> None:
        self._runtime = initial_runtime
        self._base_context_window = initial_runtime.context_window_tokens
        self._model_presets = dict(model_presets or {})
        self._preset_catalog_loader = preset_catalog_loader
        self._preset_catalog_refresh_required = False
        self._provider_snapshot_loader = provider_snapshot_loader
        self._preset_snapshot_loader = preset_snapshot_loader
        self._selection_snapshot_loader = selection_snapshot_loader
        self._provider_observer = provider_observer
        self._refresh_required = False
        self._resolved_presets: dict[str, LLMRuntime] = {}
        self._tracks_provider_generation = initial_runtime.model_preset is None
        self._default_selection_signature = preset_helpers.default_selection_signature(
            initial_runtime.snapshot_signature,
            configured_default_preset,
        )

    @property
    def runtime(self) -> LLMRuntime:
        """Return the current immutable default without refreshing configuration."""
        return self._runtime

    @property
    def model_presets(self) -> Mapping[str, GenerationPresetConfig]:
        self._refresh_preset_catalog()
        return MappingProxyType({
            name: preset.model_copy(deep=True)
            for name, preset in self._model_presets.items()
        })

    @property
    def model_preset(self) -> str | None:
        return self._runtime.model_preset

    @property
    def provider_signature(self) -> tuple[object, ...] | None:
        return self._runtime.snapshot_signature

    def current(self, *, refresh: bool = False) -> LLMRuntime:
        """Return the selected runtime, optionally refreshing the default source."""
        if refresh:
            self.refresh()
            self._refresh_provider_generation()
        return self._runtime

    @property
    def refresh_required(self) -> bool:
        return self._refresh_required

    def peek_default_model(self) -> str:
        """Read the configured default model without adopting the new runtime.

        Session-scoped resolution needs the post-refresh model, but adopting it
        here would consume the change before ``llm_runtime()`` can publish the
        canonical selection update.
        """
        if not self._refresh_required or self._provider_snapshot_loader is None:
            return self._runtime.model
        return self._provider_snapshot_loader().model

    def admit(self) -> LLMRuntime:
        """Resolve the immutable runtime for the next turn admission."""
        if self._refresh_required:
            self.refresh()
        self._refresh_provider_generation()
        return self._runtime

    def invalidate(self) -> None:
        """Refresh configured runtime state on the next admission."""
        self._refresh_required = True
        self._preset_catalog_refresh_required = True
        self._resolved_presets.clear()

    def _refresh_preset_catalog(self) -> None:
        if not self._preset_catalog_refresh_required:
            return
        if self._preset_catalog_loader is not None:
            self._model_presets = dict(self._preset_catalog_loader())
        self._preset_catalog_refresh_required = False

    def resolve_snapshot(
        self,
        snapshot: ProviderSnapshot,
    ) -> LLMRuntime:
        """Resolve a factory snapshot without changing the selected default."""
        if self._provider_observer is not None:
            self._provider_observer(snapshot.provider)
        return runtime_from_provider_snapshot(snapshot)

    def adopt_snapshot(
        self,
        snapshot: ProviderSnapshot,
    ) -> LLMRuntime:
        """Select a snapshot as the default for future turns."""
        runtime = self.resolve_snapshot(snapshot)
        self._runtime = runtime
        self._base_context_window = runtime.context_window_tokens
        self._tracks_provider_generation = runtime.model_preset is None
        self._default_selection_signature = preset_helpers.default_selection_signature(
            runtime.snapshot_signature,
            runtime.model_preset,
        )
        return runtime

    def _normalized_preset(self, name: str | None) -> str | None:
        self._refresh_preset_catalog()
        if name is None:
            return None
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("model_preset must be a non-empty string")
        if cleaned.casefold() == "default":
            return None
        return preset_helpers.normalize_preset_name(cleaned, self._model_presets)

    def _generation_preset(self, name: str | None) -> GenerationPresetConfig:
        self._refresh_preset_catalog()
        if name is None or name.casefold() == "default":
            return self._model_presets.get("default", GenerationPresetConfig())
        normalized = preset_helpers.normalize_preset_name(name, self._model_presets)
        return self._model_presets[normalized]

    def _provider_name(self) -> str | None:
        return self._provider_name_from(self._runtime)

    @staticmethod
    def _provider_name_from(runtime: LLMRuntime) -> str | None:
        name = getattr(runtime.provider, "provider_name", None)
        return name if isinstance(name, str) and name else None

    def _apply_preset_to(
        self, runtime: LLMRuntime, normalized: str | None
    ) -> LLMRuntime:
        if self._selection_snapshot_loader is not None:
            snapshot = self._selection_snapshot_loader(
                runtime.model,
                self._provider_name_from(runtime),
                normalized,
            )
            return self.resolve_snapshot(snapshot)
        generation = self._generation_preset(normalized)
        return replace(
            runtime,
            generation=generation.to_generation_settings(),
            context_window_tokens=(
                generation.context_window_tokens
                if generation.context_window_tokens is not None
                else self._base_context_window
            ),
            model_preset=normalized,
        )

    def resolve_preset(self, name: str | None) -> LLMRuntime:
        """Resolve the configured default model with a settings preset."""
        self._refresh_preset_catalog()
        normalized = (
            "default"
            if name is None or name.casefold() == "default"
            else preset_helpers.normalize_preset_name(name, self._model_presets)
        )
        cached = self._resolved_presets.get(normalized)
        if cached is not None:
            return cached
        snapshot = preset_helpers.build_runtime_preset_snapshot(
            name=normalized,
            presets=self._model_presets,
            provider=self._runtime.provider,
            loader=self._preset_snapshot_loader,
        )
        runtime = self.resolve_snapshot(snapshot)
        self._resolved_presets[normalized] = runtime
        return runtime

    def resolve_selection(
        self,
        model: str,
        *,
        provider: str | None = None,
        preset: str | None = None,
    ) -> LLMRuntime:
        """Resolve a model plus a settings preset without changing the default."""
        if not isinstance(cast(object, model), str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        normalized = self._normalized_preset(preset)
        cleaned = model.strip()
        if self._selection_snapshot_loader is not None:
            snapshot = self._selection_snapshot_loader(cleaned, provider, normalized)
            return self.resolve_snapshot(snapshot)
        generation = self._generation_preset(normalized)
        context_window = (
            generation.context_window_tokens
            if generation.context_window_tokens is not None
            else self._base_context_window
        )
        return replace(
            self._runtime,
            model=cleaned,
            generation=generation.to_generation_settings(),
            context_window_tokens=context_window,
            model_preset=normalized,
            snapshot_signature=("model_selection", cleaned, normalized),
        )

    def select_preset(self, name: str | None) -> LLMRuntime:
        """Apply a settings preset to the current model; never changes the model."""
        normalized = self._normalized_preset(name)
        runtime = self._apply_preset_to(self._runtime, normalized)
        self._runtime = runtime
        self._tracks_provider_generation = False
        return runtime

    def select_model(
        self,
        model: str,
        *,
        provider: str | None = None,
        context_window_tokens: int | None = None,
    ) -> LLMRuntime:
        """Change the default model while keeping the active settings preset."""
        runtime = self.resolve_selection(
            model,
            provider=provider,
            preset=self._runtime.model_preset,
        )
        if context_window_tokens is not None:
            runtime = replace(runtime, context_window_tokens=context_window_tokens)
            self._base_context_window = context_window_tokens
        self._runtime = runtime
        self._tracks_provider_generation = False
        return runtime

    def select_context_window(self, context_window_tokens: int) -> LLMRuntime:
        """Change the default context limit for future admissions."""
        raw_context_window = cast(object, context_window_tokens)
        if not isinstance(raw_context_window, int) or isinstance(
            raw_context_window,
            bool,
        ):
            raise TypeError("context_window_tokens must be an integer")
        self._runtime = replace(
            self._runtime,
            context_window_tokens=context_window_tokens,
        )
        self._base_context_window = context_window_tokens
        return self._runtime

    def _refresh_provider_generation(self) -> LLMRuntime | None:
        """Adopt direct provider-default changes only for provider-backed defaults."""
        if not self._tracks_provider_generation:
            return None
        runtime = self._runtime
        captured = LLMRuntime.capture(
            runtime.provider,
            runtime.model,
            context_window_tokens=runtime.context_window_tokens,
            model_preset=runtime.model_preset,
            snapshot_signature=runtime.snapshot_signature,
        )
        if captured.generation == runtime.generation:
            return None
        self._runtime = replace(runtime, generation=captured.generation)
        return self._runtime

    def refresh(self) -> LLMRuntime | None:
        """Refresh configured defaults and return the replacement when changed."""
        if self._provider_snapshot_loader is None:
            self._refresh_required = False
            return None

        self._resolved_presets.clear()
        snapshot = self._provider_snapshot_loader()
        default_selection = preset_helpers.default_selection_signature(
            snapshot.signature,
            snapshot.model_preset,
        )
        active_preset = self._runtime.model_preset
        if active_preset and self._default_selection_signature in (None, default_selection):
            runtime = self._apply_preset_to(
                self.resolve_snapshot(snapshot), active_preset
            )
        else:
            runtime = self.resolve_snapshot(snapshot)

        unchanged = (
            runtime.snapshot_signature == self._runtime.snapshot_signature
            and runtime.model_preset == self._runtime.model_preset
            and runtime.generation == self._runtime.generation
            and runtime.context_window_tokens == self._runtime.context_window_tokens
        )
        self._refresh_required = False
        if unchanged:
            self._default_selection_signature = default_selection
            return None
        (
            self._runtime,
            self._tracks_provider_generation,
            self._default_selection_signature,
        ) = (
            runtime,
            runtime.model_preset is None,
            default_selection,
        )
        self._base_context_window = runtime.context_window_tokens
        return runtime

    def resolve_override(
        self,
        *,
        model: str | None,
        model_preset: str | None,
        config: Config | None = None,
    ) -> LLMRuntime | None:
        """Resolve an SDK-style per-run override without mutating the default.

        ``model`` selects the model and ``model_preset`` selects settings; the
        two are independent and may be supplied together.
        """
        if model is None and model_preset is None:
            return None
        normalized = self._normalized_preset(model_preset)
        if model is not None:
            if self._selection_snapshot_loader is not None:
                return self.resolve_selection(model, preset=normalized)
            if config is not None:
                snapshot = build_provider_snapshot(
                    config,
                    model=model,
                    preset_name=normalized,
                )
                return self.resolve_snapshot(snapshot)
            generation = self._generation_preset(normalized)
            return LLMRuntime(
                provider=self._runtime.provider,
                model=model,
                generation=generation.to_generation_settings(),
                context_window_tokens=(
                    generation.context_window_tokens
                    if generation.context_window_tokens is not None
                    else self._runtime.context_window_tokens
                ),
                snapshot_signature=("model_override", model, normalized),
                model_preset=normalized,
            )
        return self.resolve_selection(
            self._runtime.model,
            provider=self._provider_name(),
            preset=normalized,
        )
