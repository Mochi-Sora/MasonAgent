"""Session-scoped model and settings-preset metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

# Session.metadata is public SDK data, so internal selectors use a reserved namespace.
SESSION_MODEL_PRESET_METADATA_KEY = "_mason_model_preset"
SESSION_MODEL_METADATA_KEY = "_mason_model"
SESSION_MODEL_PROVIDER_METADATA_KEY = "_mason_model_provider"


def model_preset_from_metadata(metadata: object) -> str | None:
    """Read the canonical session preset name from persisted metadata."""
    return _string_from_metadata(metadata, SESSION_MODEL_PRESET_METADATA_KEY, "model preset")


def model_selection_from_metadata(metadata: object) -> tuple[str | None, str | None]:
    """Read the session's model and provider override."""
    model = _string_from_metadata(metadata, SESSION_MODEL_METADATA_KEY, "model")
    provider = _string_from_metadata(
        metadata, SESSION_MODEL_PROVIDER_METADATA_KEY, "model provider"
    )
    return model, provider


def _string_from_metadata(metadata: object, key: str, label: str) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    typed_metadata = cast(Mapping[object, object], metadata)
    if key not in typed_metadata:
        return None
    value = typed_metadata[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"session {label} must be a non-empty string")
    return value.strip()
