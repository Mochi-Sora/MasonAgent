"""Codex device-code login: request, poll, exchange, and failure modes."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from mason.providers import openai_codex_oauth as oauth


def _jwt(account_id: str | None = "acct-test") -> str:
    def encode(value: dict[str, Any]) -> str:
        raw = json.dumps(value).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    auth: dict[str, Any] = {}
    if account_id is not None:
        auth["chatgpt_account_id"] = account_id
    payload = {"https://api.openai.com/auth": auth} if auth else {}
    return f"{encode({'alg': 'none'})}.{encode(payload)}.signature"


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    respond: Any,
) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return respond(request)

    real_client = httpx.Client

    def client_factory(**_kwargs: Any) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(record))

    monkeypatch.setattr(oauth.httpx, "Client", client_factory)

    saved: list[Any] = []

    class _Storage:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def save(self, token: Any) -> None:
            saved.append(token)

    monkeypatch.setattr(oauth, "FileTokenStorage", _Storage)
    return requests


def _json_body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode("utf-8"))


def _form_body(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode("utf-8"))


def test_device_login_requests_polls_exchanges_and_saves(monkeypatch: pytest.MonkeyPatch) -> None:
    access = _jwt()
    polls = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal polls
        if request.url.path.endswith("/deviceauth/usercode"):
            return httpx.Response(200, json={
                "device_auth_id": "device-1",
                "user_code": "ABCD-EFGH",
                "interval": "2",
            })
        if request.url.path.endswith("/deviceauth/token"):
            polls += 1
            if polls == 1:
                return httpx.Response(403)
            return httpx.Response(200, json={
                "authorization_code": "auth-code",
                "code_challenge": "challenge",
                "code_verifier": "verifier",
            })
        if request.url.path.endswith("/oauth/token"):
            return httpx.Response(200, json={
                "access_token": access,
                "refresh_token": "refresh-1",
                "expires_in": 3600,
            })
        return httpx.Response(404)

    requests = _install_transport(monkeypatch, respond)
    device = oauth.request_openai_codex_device_code()

    assert device.device_auth_id == "device-1"
    assert device.user_code == "ABCD-EFGH"
    assert device.interval_s == 2.0
    assert device.verification_url.endswith("/codex/device")
    assert _json_body(requests[0]) == {"client_id": oauth.OPENAI_CODEX_PROVIDER.client_id}

    sleeps: list[float] = []
    token = oauth.complete_openai_codex_device_login(
        device, sleep=sleeps.append
    )

    assert sleeps == [2.0]
    assert token.access == access
    assert token.refresh == "refresh-1"
    assert token.account_id == "acct-test"
    assert token.expires > 0

    exchange = next(r for r in requests if r.url.path.endswith("/oauth/token"))
    form = _form_body(exchange)
    assert form["grant_type"] == ["authorization_code"]
    assert form["client_id"] == [oauth.OPENAI_CODEX_PROVIDER.client_id]
    assert form["code"] == ["auth-code"]
    assert form["code_verifier"] == ["verifier"]
    assert form["redirect_uri"] == ["https://auth.openai.com/deviceauth/callback"]


def test_device_login_reports_disabled_server(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_transport(monkeypatch, lambda request: httpx.Response(404))

    with pytest.raises(oauth.OpenAICodexOAuthError, match="not enabled"):
        oauth.request_openai_codex_device_code()


def test_device_login_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deviceauth/usercode"):
            return httpx.Response(200, json={
                "device_auth_id": "device-1",
                "user_code": "ABCD-EFGH",
                "interval": "1",
            })
        return httpx.Response(403)

    _install_transport(monkeypatch, respond)
    device = oauth.request_openai_codex_device_code()

    with pytest.raises(oauth.OpenAICodexOAuthError, match="timed out"):
        oauth.complete_openai_codex_device_login(device, timeout_s=0)


def test_device_login_rejects_unexpected_poll_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deviceauth/usercode"):
            return httpx.Response(200, json={
                "device_auth_id": "device-1",
                "user_code": "ABCD-EFGH",
                "interval": "1",
            })
        return httpx.Response(500)

    _install_transport(monkeypatch, respond)
    device = oauth.request_openai_codex_device_code()

    with pytest.raises(oauth.OpenAICodexOAuthError, match="HTTP 500"):
        oauth.complete_openai_codex_device_login(device)


def test_device_login_rejects_failed_token_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deviceauth/usercode"):
            return httpx.Response(200, json={
                "device_auth_id": "device-1",
                "user_code": "ABCD-EFGH",
                "interval": "1",
            })
        if request.url.path.endswith("/deviceauth/token"):
            return httpx.Response(200, json={
                "authorization_code": "auth-code",
                "code_challenge": "challenge",
                "code_verifier": "verifier",
            })
        return httpx.Response(400)

    _install_transport(monkeypatch, respond)
    device = oauth.request_openai_codex_device_code()

    with pytest.raises(oauth.OpenAICodexOAuthError, match="token exchange failed"):
        oauth.complete_openai_codex_device_login(device)


def test_device_login_rejects_incomplete_authorization(monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/deviceauth/usercode"):
            return httpx.Response(200, json={
                "device_auth_id": "device-1",
                "user_code": "ABCD-EFGH",
                "interval": "1",
            })
        return httpx.Response(200, json={"authorization_code": "auth-code"})

    _install_transport(monkeypatch, respond)
    device = oauth.request_openai_codex_device_code()

    with pytest.raises(oauth.OpenAICodexOAuthError, match="incomplete"):
        oauth.complete_openai_codex_device_login(device)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2", 2.0),
        ("2.5", 2.5),
        ("0", oauth._DEVICE_DEFAULT_POLL_INTERVAL_S),
        (None, oauth._DEVICE_DEFAULT_POLL_INTERVAL_S),
        ("bogus", oauth._DEVICE_DEFAULT_POLL_INTERVAL_S),
    ],
)
def test_device_poll_interval_parsing(raw: object, expected: float) -> None:
    assert oauth._poll_interval(raw) == expected


def test_device_account_id_is_optional_in_the_token() -> None:
    assert oauth._account_id_from_access_token(_jwt("acct-1")) == "acct-1"
    assert oauth._account_id_from_access_token(_jwt(None)) is None
    assert oauth._account_id_from_access_token("not-a-jwt") is None
