"""``mason pairing`` — approve or revoke devices from the command line.

Pairing was previously reachable only through the ``/pairing`` *chat* command,
which is a chicken-and-egg problem for a transport like the desktop channel:
the first device cannot be approved from a chat it cannot reach yet.

The subcommands here reuse :mod:`mason.pairing.store`, the same ledger the chat
command and the channels read, so an approval made here is visible everywhere.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from mason.pairing import store

pairing_app = typer.Typer(help="Manage device pairing approvals")

console = Console()


def _normalize_code(code: str) -> str:
    """Accept ``abcd-efgh``, ``ABCDEFGH`` and ``abcd efgh`` alike."""
    cleaned = "".join(character for character in code.strip().upper() if character.isalnum())
    if len(cleaned) == 8:
        return f"{cleaned[:4]}-{cleaned[4:]}"
    return code.strip().upper()


def _expiry_text(value: object) -> str:
    return store.format_expiry(value) if isinstance(value, (int, float)) else "?"


def _approved_channels() -> list[str]:
    # Imported lazily: the channel registry is a heavier import than this
    # command usually needs, and pairing must work even if a channel package
    # fails to import.
    from mason.channels.registry import discover_all

    return sorted(discover_all())


@pairing_app.command("list")
def pairing_list() -> None:
    """Show pending pairing codes and already-approved devices."""
    pending = store.list_pending()
    if pending:
        table = Table(title="Pending pairings")
        table.add_column("Code", style="cyan")
        table.add_column("Channel")
        table.add_column("Sender")
        table.add_column("Expires")
        for entry in pending:
            table.add_row(
                str(entry.get("code", "")),
                str(entry.get("channel", "")),
                str(entry.get("sender_id", "")),
                _expiry_text(entry.get("expires_at")),
            )
        console.print(table)
    else:
        console.print("[dim]No pending pairing requests.[/dim]")

    approved: list[tuple[str, str]] = []
    for channel in _approved_channels():
        approved.extend((channel, sender) for sender in store.get_approved(channel))
    if approved:
        table = Table(title="Approved devices")
        table.add_column("Channel", style="cyan")
        table.add_column("Sender")
        for channel, sender in approved:
            table.add_row(channel, sender)
        console.print(table)
    else:
        console.print("[dim]No approved devices.[/dim]")


@pairing_app.command("approve")
def pairing_approve(
    code: str = typer.Argument(..., help="Pairing code, e.g. ABCD-EFGH"),
) -> None:
    """Approve a pending pairing code."""
    result = store.approve_code(_normalize_code(code))
    if result is None:
        console.print(f"[red]No pending pairing for {code!r} — it may have expired.[/red]")
        raise typer.Exit(code=1)
    channel, sender = result
    console.print(f"[green]Approved[/green] {sender} on [cyan]{channel}[/cyan]")


@pairing_app.command("deny")
def pairing_deny(
    code: str = typer.Argument(..., help="Pairing code, e.g. ABCD-EFGH"),
) -> None:
    """Reject a pending pairing code."""
    if not store.deny_code(_normalize_code(code)):
        console.print(f"[red]No pending pairing for {code!r}.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[yellow]Denied[/yellow] {code}")


@pairing_app.command("revoke")
def pairing_revoke(
    sender: str | None = typer.Argument(None, help="Sender ID to revoke"),
    channel: str = typer.Option("desktop", "--channel", "-C", help="Channel name"),
    all_senders: bool = typer.Option(
        False, "--all", help="Revoke every approved sender on the channel"
    ),
) -> None:
    """Revoke an approved device, or every device on one channel."""
    if all_senders:
        removed = store.revoke_channel(channel)
        console.print(f"[yellow]Revoked[/yellow] {removed} device(s) on [cyan]{channel}[/cyan]")
        return
    if not sender:
        console.print("[red]Provide a sender ID, or pass --all.[/red]")
        raise typer.Exit(code=1)
    if not store.revoke(channel, sender):
        console.print(f"[red]{sender} is not approved on [cyan]{channel}[/cyan].[/red]")
        raise typer.Exit(code=1)
    console.print(f"[yellow]Revoked[/yellow] {sender} on [cyan]{channel}[/cyan]")
