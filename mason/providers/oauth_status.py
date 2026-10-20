"""Local login-state checks for OAuth providers.

These checks read only local credential files. They never refresh a token or
make a network request, so they are safe to use in status output and provider
lists.
"""

# pyright: reportMissingTypeStubs=false

from __future__ import annotations

from typing import Any


def oauth_login_status(provider: str) -> bool:
    """Return whether *provider* has usable local OAuth credentials.

    Missing optional client packages and unreadable credential files both mean
    "not signed in"; callers display this as status, not as an error.
    """
    try:
        token = _load_token(provider)
    except Exception:
        return False
    return token is not None and bool(getattr(token, "access", None))


def _load_token(provider: str) -> Any:
    if provider == "openai_codex":
        from oauth_cli_kit.providers import OPENAI_CODEX_PROVIDER
        from oauth_cli_kit.storage import FileTokenStorage

        return FileTokenStorage(
            token_filename=OPENAI_CODEX_PROVIDER.token_filename,
        ).load()
    if provider == "github_copilot":
        from mason.providers.github_copilot_provider import get_storage

        return get_storage().load()
    if provider == "xai_grok":
        from mason.providers.xai_oauth import get_xai_oauth_login_status

        return get_xai_oauth_login_status()
    return None
