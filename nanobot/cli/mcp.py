"""Typer commands for remote MCP servers."""

from __future__ import annotations

import asyncio
import webbrowser
from contextlib import suppress
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import typer
from rich.console import Console

from nanobot import __logo__

if TYPE_CHECKING:
    from nanobot.config.schema import MCPServerConfig

console = Console()
mcp_app = typer.Typer(help="Manage MCP servers")


@mcp_app.command("login")
def mcp_login(
    server: str = typer.Argument(..., help="MCP server name from tools.mcpServers"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Authorize an OAuth-protected remote MCP server and store its tokens."""
    from nanobot.cli.runtime_config import _load_inspection_config
    from nanobot.config.loader import resolve_config_env_vars

    _, loaded = _load_inspection_config(config=config)
    resolved = resolve_config_env_vars(loaded)

    cfg = resolved.tools.mcp_servers.get(server)
    if cfg is None:
        console.print(
            f"[red]Unknown MCP server '{server}'.[/red] "
            "Add it under tools.mcpServers in the config first."
        )
        raise typer.Exit(1)
    if cfg.auth != "oauth":
        console.print(
            f"[red]MCP server '{server}' is not configured for OAuth.[/red] "
            'Set "auth": "oauth" on the server entry first.'
        )
        raise typer.Exit(1)
    if not cfg.url:
        console.print(f"[red]MCP server '{server}' has no url configured.[/red]")
        raise typer.Exit(1)

    console.print(f"{__logo__} MCP Login - {server}\n")
    try:
        authenticated = asyncio.run(_login(server, cfg))
    except KeyboardInterrupt:
        console.print("\n[dim]Login cancelled.[/dim]")
        raise typer.Exit(1) from None

    if not authenticated:
        console.print(
            "[red]✗ Authorization did not complete.[/red] "
            "Check the server URL and run the command again."
        )
        raise typer.Exit(1)
    console.print(f"[green]✓ Authorized MCP server '{server}'[/green]")
    console.print("[dim]Stored outside config.json; the gateway reuses it automatically.[/dim]")


@mcp_app.command("logout")
def mcp_logout(
    server: str = typer.Argument(..., help="MCP server name from tools.mcpServers"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config file"),
):
    """Remove stored OAuth credentials for one MCP server."""
    from nanobot.agent.tools.mcp_oauth import delete_mcp_oauth_credentials
    from nanobot.cli.runtime_config import _load_inspection_config

    _load_inspection_config(config=config)
    if delete_mcp_oauth_credentials(server):
        console.print(f"[green]✓ Removed stored credentials for MCP server '{server}'[/green]")
        return
    console.print(f"[yellow]! No stored credentials found for MCP server '{server}'[/yellow]")


def _split_pasted_callback(value: str) -> tuple[str, str | None]:
    """Extract the authorization code and state from a pasted callback URL."""
    raw = value.strip()
    if not raw:
        return "", None
    parsed = urlsplit(raw)
    query = parse_qs(parsed.query) if parsed.query else parse_qs(raw.lstrip("?"))
    code = (query.get("code") or [""])[0]
    state = (query.get("state") or [None])[0]
    return code, state


async def _login(server: str, cfg: MCPServerConfig) -> bool:
    from nanobot.agent.tools.mcp import connect_mcp_servers
    from nanobot.agent.tools.mcp_oauth import (
        MCP_OAUTH_CALLBACK_PATH,
        MCPOAuthHandlers,
        mcp_oauth_has_credentials,
    )
    from nanobot.agent.tools.registry import ToolRegistry

    async def redirect_handler(url: str) -> None:
        with suppress(Exception):
            webbrowser.open(url)
        console.print("Open this URL in a browser and approve access:\n")
        console.print(url, markup=False)
        console.print()

    async def callback_handler() -> tuple[str, str | None]:
        try:
            pasted = await asyncio.to_thread(
                console.input,
                "[cyan]Paste the full callback URL from the browser address bar:[/cyan] ",
            )
        except (EOFError, KeyboardInterrupt):
            return "", None
        code, state = _split_pasted_callback(pasted)
        if not code or state is None:
            console.print(
                "[yellow]That does not look like a callback URL. "
                "The URL must include both code and state parameters.[/yellow]"
            )
        return code, state

    handlers = MCPOAuthHandlers(
        redirect_uri=f"http://127.0.0.1{MCP_OAUTH_CALLBACK_PATH}",
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )
    connections = await connect_mcp_servers(
        {server: cfg},
        ToolRegistry(),
        oauth_handlers={server: handlers},
    )
    try:
        if server in connections:
            return True
        return mcp_oauth_has_credentials(server, cfg.url)
    finally:
        for connection in connections.values():
            await connection.aclose()
