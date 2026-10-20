"""Model discovery for the onboard wizard.

The implementation lives in :mod:`mason.providers.model_catalog` so the agent
command layer can use the same catalog without importing CLI code.
"""

from __future__ import annotations

from mason.providers.model_catalog import (
    ModelInfo,
    ProviderChoice,
    available_providers,
    get_model_catalog,
    get_model_context_limit,
)

__all__ = [
    "ModelInfo",
    "ProviderChoice",
    "available_providers",
    "get_model_catalog",
    "get_model_context_limit",
]


def format_token_count(tokens: int) -> str:
    """Format token count for display (e.g., 200000 -> '200,000')."""
    return f"{tokens:,}"
