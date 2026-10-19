"""Non-blocking adapter around oauth-cli-kit's interactive Codex login."""

# oauth-cli-kit does not publish type stubs.
# pyright: reportMissingTypeStubs=false

from __future__ import annotations

import base64
import hmac
import json
import queue
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import httpx
from oauth_cli_kit import login_oauth_interactive
from oauth_cli_kit.models import OAuthToken
from oauth_cli_kit.providers import OPENAI_CODEX_PROVIDER
from oauth_cli_kit.storage import FileTokenStorage

_AUTHORIZATION_URL_TIMEOUT_S = 5.0
_CALLBACK = urlsplit(OPENAI_CODEX_PROVIDER.redirect_uri)
_CALLBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_TOKEN_EXCHANGE_STATUS = re.compile(r"Token exchange failed:\s*(\d{3})\b")

# Codex CLI device-code contract (opened with the same OAuth client as the
# browser flow, but a server-side callback and its own PKCE pair).
_DEVICE_ISSUER = "https://auth.openai.com"
_DEVICE_USERCODE_URL = f"{_DEVICE_ISSUER}/api/accounts/deviceauth/usercode"
_DEVICE_TOKEN_URL = f"{_DEVICE_ISSUER}/api/accounts/deviceauth/token"
_DEVICE_VERIFICATION_URL = f"{_DEVICE_ISSUER}/codex/device"
_DEVICE_REDIRECT_URI = f"{_DEVICE_ISSUER}/deviceauth/callback"
_DEVICE_LOGIN_TIMEOUT_S = 15 * 60.0
_DEVICE_MIN_POLL_INTERVAL_S = 1.0
_DEVICE_DEFAULT_POLL_INTERVAL_S = 5.0
_HTTP_TIMEOUT_S = 30.0


class OpenAICodexOAuthError(RuntimeError):
    """An actionable Codex OAuth failure that contains no credential material."""


class OpenAICodexOAuthInputError(OpenAICodexOAuthError):
    """A recoverable error in a callback URL pasted by the user."""


class OpenAICodexOAuthLoginFlow:
    """Expose oauth-cli-kit's blocking prompt as a two-stage interactive flow."""

    def __init__(
        self,
        *,
        proxy: str | None,
        timeout_s: float,
        open_browser: bool,
    ) -> None:
        self.authorization_url = ""
        self._expected_state = ""
        self._proxy = proxy
        self._open_browser = open_browser
        self._expires_at = time.monotonic() + timeout_s
        self._callback_input: queue.Queue[str] = queue.Queue(maxsize=1)
        self._result: Future[OAuthToken] = Future()
        self._ready = threading.Event()
        self._submission_lock = threading.Lock()
        self._submitted = False
        self._thread = threading.Thread(
            target=self._run,
            name="mason-openai-codex-oauth",
            daemon=True,
        )

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self._expires_at

    @property
    def remaining_seconds(self) -> int:
        return max(0, int(self._expires_at - time.monotonic()))

    def start(self) -> OpenAICodexOAuthLoginFlow:
        self._thread.start()
        wait_s = min(
            _AUTHORIZATION_URL_TIMEOUT_S,
            max(0.0, self._expires_at - time.monotonic()),
        )
        if not self._ready.wait(wait_s):
            error = OpenAICodexOAuthError(
                "OpenAI Codex sign-in could not create an authorization URL."
            )
            self._fail(error)
            raise error
        if self._result.done():
            self._result.result()
        if self.authorization_url:
            return self
        error = OpenAICodexOAuthError(
            "OpenAI Codex sign-in returned no authorization URL."
        )
        self._fail(error)
        raise error

    def complete(self, callback_url: str | None = None) -> OAuthToken | None:
        """Submit a full callback URL, or return ``None`` while waiting for one."""
        if self._result.done():
            return self._result.result()
        if self.expired:
            error = OpenAICodexOAuthError(
                "OpenAI Codex sign-in expired. Start a new sign-in flow."
            )
            self._fail(error)
            raise error
        if callback_url is None:
            return None

        callback_state, authorization_failed = _validate_callback_url(callback_url)
        if not hmac.compare_digest(callback_state, self._expected_state):
            raise OpenAICodexOAuthInputError(
                "The callback URL does not belong to this sign-in flow. Copy the latest URL."
            )
        if authorization_failed:
            error = OpenAICodexOAuthError(
                "OpenAI Codex sign-in was not completed by the authorization server."
            )
            self._fail(error)
            raise error

        with self._submission_lock:
            if self._submitted:
                return None
            self._submitted = True
            try:
                self._callback_input.put_nowait(callback_url.strip())
            except queue.Full:
                return None
        return self._result.result() if self._result.done() else None

    def cancel(self) -> None:
        """Unblock an abandoned interactive login."""
        self._fail(OpenAICodexOAuthError("OpenAI Codex sign-in was cancelled."))
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=0.5)

    def _run(self) -> None:
        try:
            token = login_oauth_interactive(
                print_fn=self._capture_output,
                prompt_fn=self._prompt_for_callback,
                provider=OPENAI_CODEX_PROVIDER,
                proxy=self._proxy,
                open_browser=self._open_browser,
            )
        except Exception as exc:
            with suppress(Exception):
                self._result.set_exception(_safe_login_error(exc))
        else:
            with suppress(Exception):
                self._result.set_result(token)
        finally:
            self._ready.set()

    def _capture_output(self, message: str) -> None:
        raw = str(message)
        start = raw.find(OPENAI_CODEX_PROVIDER.authorize_url)
        if start < 0:
            return
        candidate = raw[start:].split(maxsplit=1)[0]
        state = _first(parse_qs(urlsplit(candidate).query), "state")
        if not state:
            return
        self.authorization_url = candidate
        self._expected_state = state
        self._ready.set()

    def _prompt_for_callback(self, _prompt: str) -> str:
        remaining = max(0.0, self._expires_at - time.monotonic())
        try:
            value = self._callback_input.get(timeout=remaining)
        except queue.Empty as exc:
            raise OpenAICodexOAuthError(
                "OpenAI Codex sign-in expired. Start a new sign-in flow."
            ) from exc
        if not value:
            error = self._result.exception() if self._result.done() else None
            if error is not None:
                raise error
            raise OpenAICodexOAuthError("OpenAI Codex sign-in was cancelled.")
        return value

    def _fail(self, error: OpenAICodexOAuthError) -> None:
        try:
            self._result.set_exception(error)
        except Exception:
            pass
        else:
            with suppress(queue.Full):
                self._callback_input.put_nowait("")
        self._ready.set()


def start_openai_codex_oauth_login(
    *,
    proxy: str | None = None,
    timeout_s: float = 600,
    open_browser: bool = True,
) -> OpenAICodexOAuthLoginFlow:
    """Start a non-blocking wrapper around oauth-cli-kit's Codex login."""
    return OpenAICodexOAuthLoginFlow(
        proxy=proxy,
        timeout_s=timeout_s,
        open_browser=open_browser,
    ).start()


def complete_openai_codex_oauth_login(
    flow: OpenAICodexOAuthLoginFlow,
    callback_url: str | None = None,
) -> OAuthToken | None:
    """Complete a pending Codex login from a full callback URL."""
    return flow.complete(callback_url)


@dataclass(frozen=True)
class OpenAICodexDeviceCode:
    """One pending device-code authorization."""

    device_auth_id: str
    user_code: str
    interval_s: float
    verification_url: str = _DEVICE_VERIFICATION_URL


def _device_client(proxy: str | None) -> httpx.Client:
    if proxy:
        return httpx.Client(timeout=_HTTP_TIMEOUT_S, proxy=proxy, trust_env=False)
    return httpx.Client(timeout=_HTTP_TIMEOUT_S)


def _poll_interval(value: object) -> float:
    """Parse the server's poll interval, which may be a JSON string."""
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return _DEVICE_DEFAULT_POLL_INTERVAL_S
    if seconds <= 0:
        return _DEVICE_DEFAULT_POLL_INTERVAL_S
    return max(_DEVICE_MIN_POLL_INTERVAL_S, seconds)


def request_openai_codex_device_code(
    *,
    proxy: str | None = None,
) -> OpenAICodexDeviceCode:
    """Ask the Codex auth server for a one-time device code."""
    try:
        with _device_client(proxy) as client:
            response = client.post(
                _DEVICE_USERCODE_URL,
                json={"client_id": OPENAI_CODEX_PROVIDER.client_id},
            )
    except httpx.HTTPError as exc:
        raise OpenAICodexOAuthError(
            "OpenAI Codex device sign-in could not reach the authorization server."
        ) from exc
    if response.status_code == 404:
        raise OpenAICodexOAuthError(
            "Device-code sign-in is not enabled for this Codex server. "
            "Use the browser login instead."
        )
    if response.status_code != 200:
        raise OpenAICodexOAuthError(
            f"OpenAI Codex device sign-in failed with HTTP {response.status_code}."
        )
    try:
        payload = cast(dict[str, Any], response.json())
    except ValueError as exc:
        raise OpenAICodexOAuthError(
            "OpenAI Codex device sign-in returned an invalid response."
        ) from exc
    device_auth_id = payload.get("device_auth_id")
    user_code = payload.get("user_code") or payload.get("usercode")
    if not isinstance(device_auth_id, str) or not device_auth_id:
        raise OpenAICodexOAuthError(
            "OpenAI Codex device sign-in returned no device authorization id."
        )
    if not isinstance(user_code, str) or not user_code:
        raise OpenAICodexOAuthError(
            "OpenAI Codex device sign-in returned no one-time code."
        )
    return OpenAICodexDeviceCode(
        device_auth_id=device_auth_id,
        user_code=user_code,
        interval_s=_poll_interval(payload.get("interval")),
    )


def complete_openai_codex_device_login(
    device_code: OpenAICodexDeviceCode,
    *,
    proxy: str | None = None,
    timeout_s: float = _DEVICE_LOGIN_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> OAuthToken:
    """Poll for approval, exchange the code, persist and return the token."""
    deadline = time.monotonic() + max(0.0, timeout_s)
    try:
        with _device_client(proxy) as client:
            approval = _poll_device_authorization(client, device_code, deadline, sleep)
            token = _exchange_device_authorization(client, approval)
    except httpx.HTTPError as exc:
        raise OpenAICodexOAuthError(
            "OpenAI Codex device sign-in could not reach the authorization server."
        ) from exc
    FileTokenStorage(token_filename=OPENAI_CODEX_PROVIDER.token_filename).save(token)
    return token


def login_openai_codex_device(
    *,
    print_fn: Callable[[str], None] = print,
    proxy: str | None = None,
    timeout_s: float = _DEVICE_LOGIN_TIMEOUT_S,
) -> OAuthToken:
    """Run the Codex device-code sign-in end to end (blocking)."""
    device_code = request_openai_codex_device_code(proxy=proxy)
    print_fn("Open this URL in a browser and sign in with ChatGPT:")
    print_fn(f"  {device_code.verification_url}")
    print_fn(f"Enter this one-time code: {device_code.user_code}")
    print_fn("Waiting for authorization...")
    return complete_openai_codex_device_login(
        device_code,
        proxy=proxy,
        timeout_s=timeout_s,
    )


def _poll_device_authorization(
    client: httpx.Client,
    device_code: OpenAICodexDeviceCode,
    deadline: float,
    sleep: Callable[[float], None],
) -> dict[str, Any]:
    while True:
        response = client.post(
            _DEVICE_TOKEN_URL,
            json={
                "device_auth_id": device_code.device_auth_id,
                "user_code": device_code.user_code,
            },
        )
        if response.status_code == 200:
            try:
                payload = cast(dict[str, Any], response.json())
            except ValueError as exc:
                raise OpenAICodexOAuthError(
                    "OpenAI Codex device sign-in returned an invalid response."
                ) from exc
            if not isinstance(payload.get("authorization_code"), str) or not payload.get(
                "code_verifier"
            ):
                raise OpenAICodexOAuthError(
                    "OpenAI Codex device sign-in returned an incomplete authorization."
                )
            return payload
        if response.status_code in (403, 404):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OpenAICodexOAuthError(
                    "OpenAI Codex device sign-in timed out. Start a new sign-in flow."
                )
            sleep(min(device_code.interval_s, remaining))
            continue
        raise OpenAICodexOAuthError(
            f"OpenAI Codex device sign-in failed with HTTP {response.status_code}."
        )


def _exchange_device_authorization(
    client: httpx.Client,
    approval: dict[str, Any],
) -> OAuthToken:
    response = client.post(
        OPENAI_CODEX_PROVIDER.token_url,
        data={
            "grant_type": "authorization_code",
            "client_id": OPENAI_CODEX_PROVIDER.client_id,
            "code": approval["authorization_code"],
            "code_verifier": approval["code_verifier"],
            "redirect_uri": _DEVICE_REDIRECT_URI,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if response.status_code != 200:
        raise OpenAICodexOAuthError(
            "OpenAI Codex device token exchange failed with "
            f"HTTP {response.status_code}."
        )
    try:
        payload = cast(dict[str, Any], response.json())
    except ValueError as exc:
        raise OpenAICodexOAuthError(
            "OpenAI Codex device token exchange returned an invalid response."
        ) from exc
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise OpenAICodexOAuthError("OpenAI Codex device token response has no access token.")
    if not isinstance(refresh, str) or not refresh:
        raise OpenAICodexOAuthError("OpenAI Codex device token response has no refresh token.")
    expires_value = payload.get("expires_in")
    if isinstance(expires_value, bool) or not isinstance(expires_value, (int, float, str)):
        raise OpenAICodexOAuthError(
            "OpenAI Codex device token response has no valid expiry."
        )
    try:
        expires_seconds = int(expires_value)
    except (TypeError, ValueError) as exc:
        raise OpenAICodexOAuthError(
            "OpenAI Codex device token response has no valid expiry."
        ) from exc
    return OAuthToken(
        access=access,
        refresh=refresh,
        expires=int(time.time() * 1000 + expires_seconds * 1000),
        account_id=_account_id_from_access_token(access),
    )


def _account_id_from_access_token(access_token: str) -> str | None:
    parts = access_token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(_base64url_decode(parts[1]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    claim_path = OPENAI_CODEX_PROVIDER.jwt_claim_path
    account_claim = OPENAI_CODEX_PROVIDER.account_id_claim
    if not claim_path or not account_claim:
        return None
    auth = cast(dict[str, Any], payload).get(claim_path)
    if not isinstance(auth, dict):
        return None
    account_id = cast(dict[str, Any], auth).get(account_claim)
    return str(account_id) if account_id else None


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _validate_callback_url(raw: str) -> tuple[str, bool]:
    value = raw.strip()
    if not value:
        raise OpenAICodexOAuthInputError("Paste the full callback URL from your browser.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise OpenAICodexOAuthInputError(
            "The callback URL is invalid. Copy the full URL from your browser's address bar."
        ) from exc
    if (
        parsed.scheme != _CALLBACK.scheme
        or parsed.hostname not in _CALLBACK_HOSTS
        or port != _CALLBACK.port
        or parsed.path != _CALLBACK.path
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise OpenAICodexOAuthInputError(
            f"Paste the full callback URL from your browser ({OPENAI_CODEX_PROVIDER.redirect_uri}?...)."
        )
    params = parse_qs(parsed.query)
    code = _first(params, "code")
    state = _first(params, "state")
    error = _first(params, "error")
    if not state:
        raise OpenAICodexOAuthInputError(
            "The callback URL is missing OAuth state. Copy the entire browser address."
        )
    if not code and not error:
        raise OpenAICodexOAuthInputError(
            "The callback URL has no authorization result. Finish signing in, then copy it again."
        )
    return state, error is not None


def _safe_login_error(exc: Exception) -> OpenAICodexOAuthError:
    if isinstance(exc, OpenAICodexOAuthError):
        return exc
    message = str(exc).strip()
    if message == "State validation failed.":
        return OpenAICodexOAuthError(
            "OpenAI Codex sign-in failed because the OAuth state did not match."
        )
    if message == "Authorization code not found.":
        return OpenAICodexOAuthError(
            "OpenAI Codex sign-in returned no authorization code."
        )
    status = _TOKEN_EXCHANGE_STATUS.search(message)
    if status:
        return OpenAICodexOAuthError(
            f"OpenAI Codex OAuth token exchange failed with HTTP {status.group(1)}."
        )
    return OpenAICodexOAuthError(
        f"OpenAI Codex sign-in failed ({type(exc).__name__})."
    )


def _first(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None
