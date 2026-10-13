"""``mason pairing`` — first-run device approval without a chat channel.

Regression guard for the chicken-and-egg case: when the desktop channel is the
only enabled channel, a device code must still be approvable from the CLI.
"""

from __future__ import annotations

from typer.testing import CliRunner

from mason.cli.commands import app
from mason.pairing.store import generate_code, get_approved, is_approved, list_pending

runner = CliRunner()


def test_list_shows_a_pending_code() -> None:
    code = generate_code("desktop", "device-1")

    result = runner.invoke(app, ["pairing", "list"])

    assert result.exit_code == 0
    assert code in result.stdout
    assert "device-1" in result.stdout


def test_approve_admits_the_device_and_list_shows_it() -> None:
    code = generate_code("desktop", "device-1")

    approved = runner.invoke(app, ["pairing", "approve", code])
    assert approved.exit_code == 0
    assert is_approved("desktop", "device-1") is True

    listed = runner.invoke(app, ["pairing", "list"])
    assert listed.exit_code == 0
    assert "device-1" in listed.stdout


def test_approve_accepts_a_lowercase_or_hyphenless_code() -> None:
    code = generate_code("desktop", "device-2")

    result = runner.invoke(app, ["pairing", "approve", code.replace("-", "").lower()])

    assert result.exit_code == 0
    assert is_approved("desktop", "device-2") is True


def test_approve_rejects_an_unknown_code() -> None:
    assert runner.invoke(app, ["pairing", "approve", "ZZZZ-ZZZZ"]).exit_code == 1


def test_deny_discards_a_pending_code() -> None:
    code = generate_code("desktop", "device-3")

    assert runner.invoke(app, ["pairing", "deny", code]).exit_code == 0
    assert list_pending() == []
    assert is_approved("desktop", "device-3") is False


def test_revoke_removes_one_device() -> None:
    runner.invoke(app, ["pairing", "approve", generate_code("desktop", "device-4")])
    assert get_approved("desktop") == ["device-4"]

    assert runner.invoke(app, ["pairing", "revoke", "device-4"]).exit_code == 0
    assert get_approved("desktop") == []


def test_revoke_all_clears_the_channel() -> None:
    for sender in ("device-a", "device-b"):
        runner.invoke(app, ["pairing", "approve", generate_code("desktop", sender)])

    result = runner.invoke(app, ["pairing", "revoke", "--all"])

    assert result.exit_code == 0
    assert get_approved("desktop") == []


def test_revoke_requires_a_sender_or_all() -> None:
    assert runner.invoke(app, ["pairing", "revoke"]).exit_code == 1
