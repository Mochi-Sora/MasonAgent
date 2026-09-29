"""Foreground gateway runtime and lifecycle helpers."""

import asyncio
import signal
from collections.abc import Callable, Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import typer
from loguru import logger
from rich.console import Console

from nanobot import __logo__, __version__
from nanobot.agent.hook import AgentHook, AgentRunHookContext
from nanobot.agent.hooks import create_file_edit_activity_hook
from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.mcp import MCPProvider
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.cli import terminal as cli_terminal
from nanobot.cli.runtime_config import _migrate_cron_store
from nanobot.config.paths import is_default_workspace
from nanobot.config.schema import Config
from nanobot.gateway.runtime import GatewayInstance
from nanobot.memory.maintenance import maybe_rollover
from nanobot.security.network import is_loopback_host
from nanobot.session.keys import (
    HEARTBEAT_SESSION_KEY,
    UNIFIED_SESSION_KEY,
    last_channel_from_metadata,
)
from nanobot.utils.evaluator import evaluate_response, resolve_evaluator_prompt
from nanobot.utils.helpers import sync_workspace_templates

__all__ = ["_run_gateway"]

console = Console()


class _MCPReadinessHook(AgentHook):
    """Retry application-owned MCP connections before the runner reads tools."""

    def __init__(self, provider: MCPProvider) -> None:
        super().__init__()
        self._provider = provider

    async def before_run(self, context: AgentRunHookContext) -> None:
        await self._provider.connect()


def _host_for_local_browser(host: str) -> str:
    """Map bind hosts to a browser-openable local host."""
    if host in {"0.0.0.0", ""}:
        return "127.0.0.1"
    if host == "::":
        return "[::1]"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def _gateway_health_url(host: str, port: int) -> str:
    """Return a health URL that can be opened from this device."""
    return f"http://{_host_for_local_browser(host)}:{port}/health"


def _gateway_health_bind_note(host: str) -> str:
    """Describe a non-local bind without presenting it as a usable URL."""
    return "" if is_loopback_host(host) else f" [dim](listening on {host})[/dim]"


def _tcp_endpoint_reachable(host: str, port: int, *, timeout_s: float = 0.25) -> bool:
    """Return whether a local TCP endpoint accepts connections."""
    import socket

    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True
    except OSError:
        return False


def _gateway_health_ready(host: str, port: int, *, timeout_s: float = 0.4) -> bool:
    """Return whether the nanobot gateway health endpoint responds OK."""
    import json
    import urllib.error
    import urllib.request

    browser_host = _host_for_local_browser(host)
    try:
        with urllib.request.urlopen(
            f"http://{browser_host}:{port}/health",
            timeout=timeout_s,
        ) as response:
            if response.status != 200:
                return False
            body = response.read(1024)
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return False

    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return payload.get("status") == "ok"


def _print_foreground_port_conflict(*, gateway_host: str, gateway_port: int) -> None:
    """Explain why a foreground gateway cannot bind its management port."""
    if _gateway_health_ready(gateway_host, gateway_port):
        console.print(
            "[yellow]A nanobot gateway is already running for this local instance.[/yellow]"
        )
    else:
        console.print(
            "[red]Error: nanobot cannot start because its gateway port "
            "is already in use.[/red]"
        )
    console.print(f"  Health: [cyan]{_gateway_health_url(gateway_host, gateway_port)}[/cyan]")


def _signal_name(signum: int) -> str:
    with suppress(ValueError):
        return signal.Signals(signum).name
    return f"signal {signum}"


def _install_gateway_shutdown_handlers(
    loop: asyncio.AbstractEventLoop,
    shutdown_event: asyncio.Event,
    tasks: list[asyncio.Task[Any]],
    print_status: Callable[[str], None],
) -> Callable[[], None]:
    """Install foreground gateway signal handlers and return a restore callback."""
    loop_signals: list[int] = []
    previous_handlers: list[tuple[int, Any]] = []
    shutdown_requested = False

    def request_shutdown(signum: int) -> None:
        nonlocal shutdown_requested
        sig_name = _signal_name(signum)
        if shutdown_requested:
            logger.warning("Forcing gateway shutdown after repeated {}", sig_name)
            for task in tasks:
                if not task.done():
                    task.cancel()
            return
        shutdown_requested = True
        logger.info("Gateway shutdown requested by {}", sig_name)
        print_status("\nShutting down... Press Ctrl+C again to force.")
        shutdown_event.set()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, request_shutdown, signum)
        except (NotImplementedError, RuntimeError, ValueError):
            try:
                previous = signal.getsignal(signum)
                signal.signal(signum, lambda sig, _frame: request_shutdown(sig))
            except (RuntimeError, ValueError):
                logger.debug("Could not install gateway handler for {}", _signal_name(signum))
                continue
            previous_handlers.append((signum, previous))
        else:
            loop_signals.append(signum)

    def restore() -> None:
        for signum in loop_signals:
            with suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(signum)
        for signum, handler in previous_handlers:
            with suppress(RuntimeError, ValueError):
                signal.signal(signum, handler)

    return restore


_HEARTBEAT_PREAMBLE = (
    "[Your response will be delivered directly to the user's messaging app. "
    "Output ONLY the final user-facing message. Never reference internal "
    "files (HEARTBEAT.md, AWARENESS.md, etc.), your instructions, or your "
    "decision process. If nothing needs reporting, respond with just "
    "'All clear.' and nothing else.]\n\n"
)


def _heartbeat_has_active_tasks(content: str) -> bool:
    """True if HEARTBEAT.md has task lines, ignoring headers, blanks and comments."""
    in_comment = False
    in_active_section: bool = False
    for line in content.splitlines():
        stripped = line.strip()
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if not stripped or stripped.startswith("#"):
            if stripped.startswith("##") and not stripped.startswith("###"):
                heading = stripped.lstrip("#").strip().lower()
                in_active_section = heading.startswith("active tasks")
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped[4:]:
                in_comment = True
            continue
        if in_active_section is False:
            continue
        return True
    return False


def _pick_heartbeat_target_from_sessions(
    *,
    enabled_channels: Iterable[str],
    sessions: Iterable[dict[str, Any]],
    unified_session_metadata: dict[str, Any] | None = None,
) -> tuple[str, str]:
    enabled = set(enabled_channels)
    for item in sessions:
        key = item.get("key") or ""
        if key == UNIFIED_SESSION_KEY:
            route = last_channel_from_metadata(unified_session_metadata)
            if route is not None:
                channel, chat_id = route
                if channel not in {"cli", "system"} and channel in enabled:
                    return channel, chat_id
            continue
        if ":" not in key:
            continue
        channel, chat_id = key.split(":", 1)
        if channel in {"cli", "system"}:
            continue
        if channel in enabled and chat_id:
            return channel, chat_id
    return "cli", "direct"


_GATEWAY_HEALTH_MAX_CONNECTIONS = 64
_GATEWAY_HEALTH_READ_TIMEOUT_SECONDS = 2.0


def _print_gateway_health_endpoint(host: str, port: int) -> None:
    """Print a usable health URL and make non-loopback binds explicit."""
    console.print(
        f"[green]✓[/green] Health endpoint: {_gateway_health_url(host, port)}"
        f"{_gateway_health_bind_note(host)}"
    )
    if is_loopback_host(host):
        return

    console.print(
        "[yellow]Warning: the unauthenticated health endpoint is listening beyond loopback "
        "and may be reachable from other devices. "
        f"Keep port {port} private or protect it with a firewall or reverse proxy.[/yellow]"
    )


def _gateway_readiness_payload() -> tuple[bool, dict[str, object]]:
    """The gateway is ready once its process and listeners are alive."""
    return True, {"status": "ok", "process": "alive", "ready": True}


async def _close_gateway_runtime(
    agent: AgentLoop,
    mcp_provider: MCPProvider,
    channels: Any,
    tasks: list[asyncio.Task[Any]],
    runtime_tasks: asyncio.Future[list[Any]] | None,
    *,
    task_wait_timeout: float = 15.0,
    close_timeout: float = 15.0,
) -> None:
    """Cancel runtime tasks, then deterministically close application resources.

    Order matters: runtime tasks (including the agent loop and any in-flight
    turn) are cancelled and awaited -- bounded -- before the loop-owned resources
    and the application-owned MCP provider are torn down. The final close is
    bounded and idempotent, so it also covers a cancelled or incomplete loop
    cleanup without leaving subprocess transports alive past ``loop.close()``.
    """
    # Some SDKs swallow task cancellation while attempting to reconnect.
    # Close channel transports before waiting for their runners to exit.
    await channels.stop_all()
    for task in tasks:
        if not task.done():
            task.cancel()
    pending: set[asyncio.Task[Any]] = set()
    if tasks:
        # Bounded: a coroutine that swallows cancellation (e.g. an SDK reconnect
        # loop) must not hold the stop open until systemd's timeout kills the
        # cgroup. Anything still pending is abandoned and closed underneath.
        _done, pending = await asyncio.wait(tasks, timeout=task_wait_timeout)
        # A task can swallow the first cancellation while unwinding. Re-cancel
        # timed-out tasks so an agent loop stuck draining background work reaches
        # its resource-cleanup phase before the explicit final close below.
        for task in pending:
            task.cancel()
    if runtime_tasks is not None and not runtime_tasks.done():
        runtime_tasks.cancel()
    for label, close in (
        ("agent", agent.aclose),
        ("MCP provider", mcp_provider.aclose),
    ):
        try:
            await asyncio.wait_for(close(), timeout=close_timeout)
        except BaseException as exc:  # noqa: BLE001 - shutdown must proceed
            logger.warning("Gateway shutdown: {} cleanup incomplete: {}", label, exc)
    # Retrieving an already-finished gather prevents noisy unhandled exceptions,
    # but never wait for it here: its children were bounded individually above.
    if runtime_tasks is not None and runtime_tasks.done():
        with suppress(asyncio.CancelledError, Exception):
            await runtime_tasks


def _run_gateway(
    config: Config,
    *,
    port: int | None = None,
    health_server_enabled: bool = True,
    gateway_instance: GatewayInstance | None = None,
) -> None:
    """Shared foreground gateway runtime: channels, cron, health, and shutdown."""
    from nanobot.agent.model_presets import load_model_preset_catalog
    from nanobot.agent.tools.message import MessageTool
    from nanobot.bus.queue import MessageBus
    from nanobot.channels.manager import ChannelManager
    from nanobot.config.watcher import watch_config_file
    from nanobot.cron.bound_runner import run_bound_cron_job
    from nanobot.cron.service import CronJobSkippedError, CronService
    from nanobot.cron.session_turns import is_bound_cron_job
    from nanobot.cron.types import CronJob, CronRunResult
    from nanobot.llm_usage import record_llm_call
    from nanobot.llm_usage.context import llm_usage_source
    from nanobot.providers.factory import (
        ProviderSnapshot,
        build_provider_snapshot,
        load_provider_snapshot,
    )
    from nanobot.providers.image_generation import image_gen_provider_configs
    from nanobot.session.manager import SessionManager
    from nanobot.session.recovery import RecoveryCoordinator
    from nanobot.triggers.local_runner import run_local_trigger_queue
    from nanobot.triggers.local_store import LocalTriggerStore

    port = port if port is not None else config.gateway.port
    gateway_host_for_browser = _host_for_local_browser(config.gateway.host)
    if health_server_enabled and _tcp_endpoint_reachable(gateway_host_for_browser, port):
        _print_foreground_port_conflict(
            gateway_host=config.gateway.host,
            gateway_port=port,
        )
        raise typer.Exit(1)

    console.print(f"{__logo__} Starting nanobot gateway version {__version__} on port {port}...")
    sync_workspace_templates(config.workspace_path)
    bus = MessageBus()

    def _observe_provider(snapshot: ProviderSnapshot) -> ProviderSnapshot:
        snapshot.provider.set_llm_call_observer(record_llm_call)
        return snapshot

    def _load_gateway_provider_snapshot(
        *args: Any,
        **kwargs: Any,
    ) -> ProviderSnapshot:
        return _observe_provider(load_provider_snapshot(*args, **kwargs))

    try:
        provider_snapshot = _observe_provider(build_provider_snapshot(config))
    except ValueError as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1) from exc
    session_manager = SessionManager(config.workspace_path)

    # Use the same runtime identity for foreground and managed gateway processes.
    from nanobot.config.loader import get_config_path
    from nanobot.gateway.runtime import (
        GatewayClientLease,
        GatewayRuntime,
        monitor_gateway_clients,
    )

    instance = gateway_instance or GatewayInstance.resolve(
        config_path=get_config_path(),
    )
    config_path = str(instance.config_path)
    gateway_runtime = GatewayRuntime(paths=instance.paths)
    gateway_start_options = instance.start_options(port=port)

    # Preserve existing single-workspace installs, but keep custom workspaces clean.
    if is_default_workspace(config.workspace_path):
        _migrate_cron_store(config)

    # Create cron service with workspace-scoped store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path)
    trigger_store = LocalTriggerStore(config.workspace_path)

    tools = ToolRegistry()
    mcp_provider = MCPProvider.from_config(config, tools)

    recovery = RecoveryCoordinator(
        sessions=session_manager,
        bus=bus,
        unified_session=config.agents.defaults.unified_session,
    )

    # Create agent with cron service
    agent = AgentLoop.from_config(
        config, bus,
        provider=provider_snapshot.provider,
        model=provider_snapshot.model,
        context_window_tokens=provider_snapshot.context_window_tokens,
        cron_service=cron,
        session_manager=session_manager,
        image_generation_provider_configs=image_gen_provider_configs(config),
        provider_snapshot_loader=_load_gateway_provider_snapshot,
        preset_catalog_loader=load_model_preset_catalog,
        provider_signature=provider_snapshot.signature,
        local_trigger_store=trigger_store,
        hooks=[_MCPReadinessHook(mcp_provider)],
        hook_factories=[create_file_edit_activity_hook],
        tool_registry=tools,
        recovery_admission=recovery,
    )
    from nanobot.bus.events import OutboundMessage
    from nanobot.session.keys import session_key_for_channel

    def _channel_session_key(channel: str, chat_id: str) -> str:
        return session_key_for_channel(
            channel,
            chat_id,
            unified_session=config.agents.defaults.unified_session,
        )

    async def _deliver_to_channel(
        msg: OutboundMessage, *, record: bool = False, session_key: str | None = None,
    ) -> None:
        """Publish a user-visible message and mirror it into that channel's session."""
        metadata = dict(msg.metadata or {})
        record = record or bool(metadata.pop("_record_channel_delivery", False))
        if metadata != (msg.metadata or {}):
            msg = OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=msg.content,
                reply_to=msg.reply_to,
                media=msg.media,
                metadata=metadata,
                buttons=msg.buttons,
            )
        if (
            record
            and msg.channel != "cli"
            and msg.content.strip()
            and hasattr(session_manager, "get_or_create")
            and hasattr(session_manager, "save")
        ):
            key = session_key or _channel_session_key(msg.channel, msg.chat_id)
            session = session_manager.get_or_create(key)
            extra: dict[str, Any] = {"_channel_delivery": True}
            if msg.media:
                extra["media"] = list(msg.media)
            session.add_message("assistant", msg.content, **extra)
            session_manager.save(session)
        await bus.publish_outbound(msg)

    message_tool = agent.tools.get("message")
    if isinstance(message_tool, MessageTool):
        message_tool.set_send_callback(_deliver_to_channel)

    # Set cron callback (needs agent)
    memory_cfg = config.agents.defaults.memory
    memory_consolidation_enabled = memory_cfg.enabled and memory_cfg.consolidation.enabled

    async def _run_memory_consolidation() -> None:
        """One consolidation pass; failures are logged, never raised into cron."""
        if not memory_consolidation_enabled:
            return
        from nanobot.memory.consolidation import MemoryConsolidator

        runtime = agent.consolidation_runtime() or agent.llm_runtime()
        consolidator = MemoryConsolidator(agent.context.workspace, agent.context.memory_db)
        try:
            result = await consolidator.run(runtime)
            if not result.ok:
                logger.warning("Memory consolidation: {}", result.summary())
        except Exception:
            logger.exception("Memory consolidation failed")

    async def on_cron_job(job: CronJob) -> str | CronRunResult | None:
        """Execute a cron job through the agent."""
        async def _silent(*_args: Any, **_kwargs: Any) -> None:
            pass

        # Consolidation curates long-term memory from the backup tier.
        if job.name == "consolidation":
            await _run_memory_consolidation()
            return None

        # Memory rollover discards yesterday's backup and empties the working
        # state. Consolidation gets a last chance at the outgoing day first.
        if job.name == "memory_rollover":
            await _run_memory_consolidation()
            memory_db = agent.context.memory_db
            if memory_db.enabled:
                maybe_rollover(
                    memory_db,
                    agent.context.memory_state,
                    consolidation_enabled=memory_consolidation_enabled,
                )
            return None

        # Heartbeat is a system job that checks HEARTBEAT.md for active tasks.
        if job.name == "heartbeat":
            heartbeat_file = config.workspace_path / "HEARTBEAT.md"
            try:
                content = heartbeat_file.read_text(encoding="utf-8")
            except OSError:
                logger.debug("Heartbeat: HEARTBEAT.md missing")
                return None
            if not _heartbeat_has_active_tasks(content):
                logger.debug("Heartbeat: HEARTBEAT.md has no active tasks")
                return None

            channel, chat_id = _pick_heartbeat_target()
            if channel == "cli":
                return None

            prompt = (
                _HEARTBEAT_PREAMBLE
                + f"You are executing periodic heartbeat tasks. Read the active tasks below, perform each one, and report what you did:\n\n{content}"
            )

            # Internal check: funnel all output through the post-run gate so the
            # turn can't deliver directly via the message tool and skip it.
            suppress_token = None
            if isinstance(message_tool, MessageTool):
                suppress_token = message_tool.set_suppress_delivery(True)
            try:
                await mcp_provider.connect()
                resp = await agent.process_direct(
                    prompt,
                    session_key=HEARTBEAT_SESSION_KEY,
                    channel=channel,
                    chat_id=chat_id,
                    on_progress=_silent,
                )
            finally:
                if isinstance(message_tool, MessageTool) and suppress_token is not None:
                    message_tool.reset_suppress_delivery(suppress_token)

            if not resp or not resp.content:
                return

            response = resp.content

            evaluator_prompt = resolve_evaluator_prompt(config.workspace_path)

            # Fail closed: stay silent on evaluator failure instead of notifying.
            with llm_usage_source("cron"):
                should_notify = await evaluate_response(
                    response=response,
                    task_context=prompt,
                    provider=agent.provider,
                    model=agent.model,
                    evaluator_prompt=evaluator_prompt,
                    default_notify=False,
                )

            if should_notify:
                logger.info("Heartbeat: completed, delivering response")
                await _deliver_to_channel(
                    OutboundMessage(channel=channel, chat_id=chat_id, content=response),
                    record=True,
                )
            else:
                logger.info("Heartbeat: silenced by post-run evaluation")
            return response

        if is_bound_cron_job(job):
            return await run_bound_cron_job(job, agent=agent, cron=cron)

        reason = "unbound agent cron job must be recreated from a chat session"
        logger.warning(
            "Cron: skipped unbound agent job '{}' ({}): {}",
            job.name,
            job.id,
            reason,
        )
        raise CronJobSkippedError(reason)

    cron.on_job = on_cron_job

    # Create the channel manager with the shared session and cron services.
    channels = ChannelManager(
        config,
        bus,
        session_manager=session_manager,
        cron_service=cron,
        local_trigger_store=trigger_store,
        config_path=Path(config_path),
    )

    def _pick_heartbeat_target() -> tuple[str, str]:
        """Pick a routable channel/chat target for heartbeat-triggered messages."""
        unified_metadata = None
        if config.agents.defaults.unified_session:
            record = session_manager.read_session_metadata(UNIFIED_SESSION_KEY)
            if isinstance(record, dict) and isinstance(record.get("metadata"), dict):
                unified_metadata = record["metadata"]
        return _pick_heartbeat_target_from_sessions(
            enabled_channels=channels.enabled_channels,
            sessions=session_manager.list_sessions(),
            unified_session_metadata=unified_metadata,
        )

    if channels.enabled_channels:
        console.print(f"[green]✓[/green] Channels enabled: {', '.join(channels.enabled_channels)}")
    else:
        console.print("[yellow]Warning: No channels enabled[/yellow]")

    hb_cfg = config.gateway.heartbeat
    if hb_cfg.enabled:
        console.print(f"[green]✓[/green] Heartbeat: every {hb_cfg.interval_s}s")
    else:
        console.print("[yellow]✗[/yellow] Heartbeat: disabled")

    async def _health_server(host: str, health_port: int) -> None:
        """Lightweight HTTP health endpoint on the gateway port."""
        import json as _json

        connection_slots = asyncio.Semaphore(_GATEWAY_HEALTH_MAX_CONNECTIONS)

        async def handle(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            if connection_slots.locked():
                writer.close()
                return

            async with connection_slots:
                try:
                    data = await asyncio.wait_for(
                        reader.read(4096),
                        timeout=_GATEWAY_HEALTH_READ_TIMEOUT_SECONDS,
                    )
                    request_line = data.split(b"\r\n", 1)[0].decode(
                        "utf-8", errors="replace",
                    )
                    method, path = "", ""
                    parts = request_line.split(" ")
                    if len(parts) >= 2:
                        method, path = parts[0], parts[1]

                    if method == "GET" and path == "/health":
                        ready, payload = _gateway_readiness_payload()
                        body = _json.dumps(payload)
                        status = "200 OK" if ready else "503 Service Unavailable"
                        content_type = "application/json"
                    else:
                        body = "Not Found"
                        status = "404 Not Found"
                        content_type = "text/plain"

                    resp = (
                        f"HTTP/1.0 {status}\r\n"
                        f"Content-Type: {content_type}\r\n"
                        f"Content-Length: {len(body)}\r\n"
                        "Connection: close\r\n"
                        f"\r\n{body}"
                    )
                    writer.write(resp.encode())
                    await writer.drain()
                except (asyncio.TimeoutError, ConnectionError):
                    pass
                finally:
                    writer.close()

        server = await asyncio.start_server(handle, host, health_port)
        _print_gateway_health_endpoint(host, health_port)
        async with server:
            await server.serve_forever()
    # Dream was removed from nanobot. Retire its persisted system job, cursor,
    # and per-run sessions once so upgraded installs cannot keep scheduling it.
    from nanobot.cron.types import CronJob, CronPayload, CronSchedule
    cron.remove_system_job("dream")
    with suppress(OSError):
        (agent.context.memory.memory_dir / ".dream_cursor").unlink(missing_ok=True)
    for legacy_session in agent.sessions.list_sessions():
        legacy_key = legacy_session.get("key", "")
        if isinstance(legacy_key, str) and legacy_key.startswith("dream:"):
            agent.sessions.delete_session(legacy_key)

    # Register Heartbeat system job (idempotent on restart)
    if hb_cfg.enabled:
        cron.register_system_job(CronJob(
            id="heartbeat",
            name="heartbeat",
            schedule=CronSchedule(
                kind="every",
                every_ms=hb_cfg.interval_s * 1000,
                tz=config.agents.defaults.timezone,
            ),
            payload=CronPayload(kind="system_event"),
        ))
    else:
        cron.remove_system_job("heartbeat")

    # Register the memory rollover job (idempotent on restart). Startup
    # maintenance in ContextBuilder already covers a gateway that was offline
    # over midnight; this job covers one that stays up across it.
    if config.agents.defaults.memory.enabled:
        cron.register_system_job(CronJob(
            id="memory_rollover",
            name="memory_rollover",
            schedule=CronSchedule(
                kind="cron",
                expr="0 0 * * *",
                tz=config.agents.defaults.timezone,
            ),
            payload=CronPayload(kind="system_event"),
        ))
    else:
        cron.remove_system_job("memory_rollover")

    # Register the consolidation job (idempotent on restart).
    consolidation_cfg = memory_cfg.consolidation
    if memory_consolidation_enabled:
        cron.register_system_job(CronJob(
            id="consolidation",
            name="consolidation",
            schedule=consolidation_cfg.build_schedule(config.agents.defaults.timezone),
            payload=CronPayload(kind="system_event"),
        ))
        console.print(
            f"[green]✓[/green] Memory consolidation: {consolidation_cfg.describe_schedule()}"
        )
    else:
        cron.remove_system_job("consolidation")

    cron_status = cron.status()
    cron_job_count = cast(int, cron_status["jobs"])
    if cron_job_count > 0:
        console.print(f"[green]✓[/green] Cron: {cron_job_count} scheduled jobs")

    async def run() -> None:
        tasks: list[asyncio.Task[Any]] = []
        shutdown_task: asyncio.Task[Any] | None = None
        runtime_tasks: asyncio.Future[list[Any]] | None = None
        startup_complete = False
        shutdown_event = asyncio.Event()
        cli_terminal._ensure_interactive_tty_mode()
        restore_shutdown_handlers = _install_gateway_shutdown_handlers(
            asyncio.get_running_loop(),
            shutdown_event,
            tasks,
            console.print,
        )
        try:
            await cron.start()
            # Re-read once on first admission to close the watcher subscription window.
            agent.runtime_resolver.invalidate()
            # Recovery must finish before channels begin accepting new input.  That
            # makes a new user message reliably supersede an old recoverable turn
            # instead of racing its queue.
            await recovery.scan()
            async def _run_agent() -> None:
                try:
                    await mcp_provider.connect()
                    await agent.run()
                finally:
                    await mcp_provider.aclose()

            async def _monitor_local_clients() -> None:
                orphaned = await monitor_gateway_clients(
                    GatewayClientLease(gateway_runtime, kind="gateway-monitor"),
                    shutdown_event,
                )
                if orphaned:
                    logger.info("Last local client disappeared; stopping on-demand gateway")

            tasks = [
                asyncio.create_task(
                    watch_config_file(
                        Path(config_path),
                        lambda: agent.invalidate_runtime_config(),
                    ),
                    name="nanobot-config-watcher",
                ),
                asyncio.create_task(_run_agent(), name="nanobot-agent-loop"),
                asyncio.create_task(channels.start_all(), name="nanobot-channels"),
                asyncio.create_task(
                    run_local_trigger_queue(
                        store=trigger_store,
                        submit_turn=agent.submit_local_trigger_turn,
                        is_channel_enabled=lambda name: channels.get_channel(name) is not None,
                    ),
                    name="nanobot-local-triggers",
                ),
                asyncio.create_task(
                    _monitor_local_clients(),
                    name="nanobot-gateway-client-monitor",
                ),
            ]
            if health_server_enabled:
                tasks.append(asyncio.create_task(
                    _health_server(config.gateway.host, port),
                    name="nanobot-health-server",
                ))
            runtime_tasks = asyncio.gather(*tasks)
            startup_complete = True
            shutdown_task = asyncio.create_task(
                shutdown_event.wait(),
                name="nanobot-gateway-shutdown",
            )
            done, _pending = await asyncio.wait(
                {runtime_tasks, shutdown_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if runtime_tasks in done:
                await runtime_tasks
            else:
                runtime_tasks.cancel()
        except KeyboardInterrupt:
            console.print("\nShutting down...")
        except Exception:
            import traceback

            console.print("\n[red]Error: Gateway crashed unexpectedly[/red]")
            console.print(traceback.format_exc())
            if not startup_complete:
                # Do not report a successful gateway command when startup
                # failed before any runtime task or listener was created.
                raise typer.Exit(1)
        finally:
            try:
                if shutdown_task and not shutdown_task.done():
                    shutdown_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await shutdown_task
                cron.stop()
                # A gateway exit interrupts ownership of active turns; it is
                # not the same as the user stopping a turn.  Keep checkpoints
                # so the next gateway can offer an explicit Continue action.
                agent.preserve_inflight_turns_on_shutdown()
                agent.stop()
                # Cancel runtime tasks first, then deterministically close
                # exec/MCP resources while the event loop is still alive.
                await _close_gateway_runtime(
                    agent,
                    mcp_provider,
                    channels,
                    tasks,
                    runtime_tasks,
                )
                await bus.drain()
                # Flush all cached sessions to durable storage before exit.
                # This prevents data loss on filesystems with write-back
                # caching (rclone VFS, NFS, FUSE mounts, etc.).
                flushed = agent.sessions.flush_all()
                if flushed:
                    logger.info("Shutdown: flushed {} session(s) to disk", flushed)
            finally:
                restore_shutdown_handlers()

    with gateway_runtime.foreground_instance(gateway_start_options):
        if health_server_enabled:
            gateway_runtime.publish_health_host(config.gateway.host)
        asyncio.run(run())
