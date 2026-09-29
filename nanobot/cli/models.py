"""Model discovery for the onboard wizard."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import cast

import httpx

from nanobot.config.schema import Config, ModelPresetConfig, ProviderConfig
from nanobot.providers.registry import ProviderSpec, create_dynamic_spec, find_by_name

_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class ModelInfo:
    id: str
    context_window: int | None


def _resolve_env_placeholders(value: str | None) -> str | None:
    """Resolve ``${VAR}`` references, returning None when nothing usable remains."""
    if not value:
        return None
    missing = False

    def replace(match: re.Match[str]) -> str:
        nonlocal missing
        env_value = os.environ.get(match.group(1))
        if env_value is None:
            missing = True
            return ""
        return env_value

    resolved = _ENV_REF_RE.sub(replace, value).strip()
    if missing and not resolved:
        return None
    return resolved or None


def _resolve_provider(
    config: Config, provider: str,
) -> tuple[ProviderSpec, ProviderConfig] | None:
    """Resolve a registry or dynamic custom provider plus its configured settings."""
    spec = find_by_name(provider)
    if spec is not None:
        provider_config = getattr(config.providers, spec.name, None)
        if isinstance(provider_config, ProviderConfig):
            return spec, provider_config
        return None

    normalized = provider.replace("-", "_")
    for name, provider_config in (config.providers.model_extra or {}).items():
        if not isinstance(provider_config, ProviderConfig):
            continue
        if provider == name or normalized == name.replace("-", "_"):
            return (
                create_dynamic_spec(
                    name,
                    display_name=provider_config.display_name or "",
                    thinking_style=provider_config.thinking_style or "",
                ),
                provider_config,
            )
    return None


def _builtin_models(spec: ProviderSpec) -> list[ModelInfo]:
    return [ModelInfo(model.id, model.context_window) for model in spec.builtin_models]


def _oauth_models(spec: ProviderSpec, provider_config: ProviderConfig) -> list[ModelInfo]:
    try:
        from nanobot.providers.oauth_model_catalog import get_oauth_model_catalog

        catalog = get_oauth_model_catalog(
            spec.name,
            proxy=_resolve_env_placeholders(provider_config.proxy),
        )
    except ImportError:
        # Optional OAuth client packages may be absent; keep the built-in list.
        return _builtin_models(spec)
    return [ModelInfo(model.id, model.context_window) for model in catalog.models]


def _requires_api_key(spec: ProviderSpec) -> bool:
    if spec.name == "azure_openai":
        return False
    return not (spec.is_oauth or spec.is_local or spec.is_direct)


def _row_context_window(row: dict[str, object]) -> int | None:
    for key in (
        "context_window",
        "context_length",
        "max_context_length",
        "max_model_len",
        "max_input_tokens",
    ):
        value = row.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, float) and value > 0:
            return int(value)
    return None


def _remote_models(spec: ProviderSpec, provider_config: ProviderConfig) -> list[ModelInfo]:
    """Read the provider's OpenAI-style ``/models`` list, falling back to builtins."""
    api_base = _resolve_env_placeholders(provider_config.api_base) or spec.default_api_base
    if spec.name == "openai" and not api_base:
        api_base = "https://api.openai.com/v1"
    if not api_base:
        return _builtin_models(spec)

    api_key = _resolve_env_placeholders(provider_config.api_key)
    if _requires_api_key(spec) and not api_key:
        return _builtin_models(spec)

    headers = {"Accept": "application/json"}
    if api_key:
        if spec.name == "minimax_anthropic":
            headers["X-Api-Key"] = api_key
        else:
            headers["Authorization"] = f"Bearer {api_key}"
    models_url = f"{api_base.rstrip('/')}/models"
    if spec.name == "minimax_anthropic" and not api_base.rstrip("/").endswith("/v1"):
        models_url = f"{api_base.rstrip('/')}/v1/models"

    try:
        response = httpx.get(
            models_url,
            headers=headers,
            timeout=10.0,
            follow_redirects=False,
        )
        response.raise_for_status()
        body: object = response.json()
    except (httpx.HTTPError, ValueError):
        return _builtin_models(spec)

    raw_rows: object
    if isinstance(body, dict):
        raw_rows = cast(dict[str, object], body).get("data")
    else:
        raw_rows = body
    if not isinstance(raw_rows, list):
        return _builtin_models(spec)

    models: list[ModelInfo] = []
    seen: set[str] = set()
    for raw_row in cast(list[object], raw_rows):
        model_id: str | None = None
        context_window: int | None = None
        if isinstance(raw_row, str):
            model_id = raw_row.strip() or None
        elif isinstance(raw_row, dict):
            row = cast(dict[str, object], raw_row)
            for key in ("id", "name", "model"):
                value = row.get(key)
                if isinstance(value, str) and value.strip():
                    model_id = value.strip()
                    break
            context_window = _row_context_window(row)
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append(ModelInfo(model_id, context_window))
    return models or _builtin_models(spec)


def get_model_catalog(
    config: Config, provider: str, model: str = "",
) -> list[ModelInfo]:
    """Read the selected provider's advisory catalog using the current wizard draft."""
    if provider == "auto":
        provider = config.get_provider_name(preset=ModelPresetConfig(model=model)) or ""
    if not provider:
        return []
    resolved = _resolve_provider(config, provider)
    if resolved is None:
        return []
    spec, provider_config = resolved
    if spec.model_catalog == "builtin":
        return _builtin_models(spec)
    if spec.model_catalog == "hybrid":
        return _oauth_models(spec, provider_config)
    if spec.model_catalog == "unsupported" or spec.is_transcription_only:
        return _builtin_models(spec)
    if spec.backend != "openai_compat" and spec.name != "minimax_anthropic":
        return _builtin_models(spec)
    return _remote_models(spec, provider_config)


def get_model_context_limit(
    model: str, provider: str, *, config: Config,
) -> int | None:
    if provider == "auto":
        provider = config.get_provider_name(preset=ModelPresetConfig(model=model)) or ""
    rows = get_model_catalog(config, provider, model)
    for row in rows:
        if row.id == model:
            return row.context_window
    # Explicit provider prefixes are accepted by the wizard as well as bare IDs.
    prefix, separator, bare_model = model.partition("/")
    if separator and prefix.replace("-", "_") == provider.replace("-", "_"):
        return next((row.context_window for row in rows if row.id == bare_model), None)
    return None


def format_token_count(tokens: int) -> str:
    """Format token count for display (e.g., 200000 -> '200,000')."""
    return f"{tokens:,}"
