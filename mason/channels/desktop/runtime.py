"""Desktop channel: a WebSocket server for the MasonDesk client (protocol v1).

Design notes
------------

* One multiplexed WebSocket per client; JSON text frames.  The frame contract
  lives in the MasonDesk repository (docs/protocol.md).
* Authentication: the first frame must be 'hello'.  A missing or unknown
  device token starts the pairing flow -- the client shows a code, the owner
  approves it ('/pairing approve CODE' or the 'mason pairing' command surface)
  and the channel pushes 'pairing.approved' with the device token.  Device
  tokens are sender IDs in the pairing store, so revocation and the
  'allowFrom' allowlist behave exactly like every other channel.
* 'trustLocalhost' (default off) auto-approves loopback connects, which makes
  a purely local gateway plug and play.
* Session ids are framework session keys.  'chat.send' without a session mints
  'desktop:<id>'; outbound messages are routed back to the connection that
  last sent into that session.
* Streaming: 'send_delta' / 'send_reasoning_delta' mirror the channel hooks,
  and the accumulated text is committed as one final 'message' event per
  segment.  'turn.end' comes from the ordered 'TurnEndEvent' outbound event,
  so it always follows the turn's messages; the idle transition only releases
  the session's busy state and commits pending stream text.  A discarded
  stream segment (recovery boundary) closes with a 'message' event carrying
  'replaced: true' so clients drop the text they already rendered.
* Live session lists: 'session.updated' is pushed for the owning connection
  whenever a turn is persisted ('SessionTurnPersisted'), carrying the key,
  title, updated_at, and message_count of the stored session record.
* 'turn.interrupt' publishes the built-in '/stop' command for the session; the
  agent cancels the active turn and the channel reports
  'turn.end {status: "interrupted"}'.
* Query frames (sessions.list/history, memory.stats, cron.list) use read-only
  services attached by the channel manager ('attach_services'); they are
  optional so a standalone channel still serves chat.
* Tool approvals: an 'approval' event carries the pending call (tool, summary,
  args preview, deadline); 'approve' / 'deny' requests resolve it through the
  framework broker.  Unknown or expired ids answer 'no_approval'.
* Attachments: 'media.upload' decodes a base64 payload into the instance media
  directory under the connection's device token and returns an opaque 'att-…'
  id; 'chat.send' resolves ids owned by the sending connection into the turn's
  media.  Unused uploads expire after UPLOAD_TTL_S and their files are deleted
  as the registry is pruned.
* Steering: 'chat.inject' publishes text into the turn already running for a
  session (the agent loop drains it at its next model boundary) without
  starting a new turn, and is advertised via 'hello.features'.
* Media fetch: 'media.fetch' returns the stored bytes of an attachment
  referenced by a session transcript, so clients can rebuild previews after a
  reload.  Resolution is transcript-scoped and the fetched file must live in
  the instance media directory; fetch is whole-file (no range/seek).
* Retry / recovery / response-source notices are 'interactive' notifications
  (a channel capability, see mason.channels.capabilities); protocol v1 has no
  frames for them yet, so this channel does not declare the capability.
* Non-loopback binds are plaintext ws://; put a TLS terminator in front and
  rely on device tokens (the protocol forbids plaintext off-loopback).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import mimetypes
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from pydantic import Field
from websockets.asyncio.server import serve as ws_serve
from websockets.exceptions import ConnectionClosed

from mason import __version__
from mason.agent.tools.approvals import pending_tool_approvals, resolve_tool_approval
from mason.bus.events import OutboundMessage
from mason.bus.outbound_events import ApprovalRequestEvent, ProgressEvent, TurnEndEvent
from mason.bus.queue import MessageBus
from mason.bus.runtime_events import (
    SessionTurnPersisted,
    TurnRunStatusChanged,
)
from mason.channels.base import BaseChannel
from mason.channels.capabilities import ORDERED_TURN_END, TOOL_APPROVALS
from mason.channels.desktop.devices import DeviceLedger
from mason.channels.desktop.protocol import (
    CLOSE_HELLO_TIMEOUT,
    CLOSE_PROTOCOL_ERROR,
    CLOSE_UNAUTHORIZED,
    ERROR_BAD_REQUEST,
    ERROR_BUSY,
    ERROR_INTERNAL,
    ERROR_MEDIA_GONE,
    ERROR_NO_APPROVAL,
    ERROR_NO_ATTACHMENT,
    ERROR_NO_SESSION,
    ERROR_NO_TURN,
    ERROR_NOT_PAIRED,
    ERROR_PROTOCOL_MISMATCH,
    ERROR_TOO_LARGE,
    ERROR_UNAUTHORIZED,
    ERROR_UNSUPPORTED,
    FRAME_MAX_BYTES,
    PROTOCOL_VERSION,
    TRUSTED_LOOPBACK_DEVICE,
    RequestFrame,
    diff_preview,
    epoch_ms,
    error_response,
    event_frame,
    ok_response,
    optional_ms,
    optional_text,
    parse_request,
    preview_text,
    schedule_text,
    tool_summary,
)
from mason.config.paths import get_media_dir
from mason.config.schema import Base
from mason.pairing import PAIRING_CODE_META_KEY, approve_code, generate_code, is_approved
from mason.session.access import SessionAccess
from mason.session.attachments import (
    ATTACHMENT_PATH_KEY,
    ATTACHMENTS_METADATA_KEY,
    public_attachment,
)
from mason.session.turn_metadata import TURN_ID_METADATA_KEY
from mason.utils.helpers import safe_filename

if TYPE_CHECKING:
    from websockets.asyncio.server import Server, ServerConnection

    from mason.cron.service import CronService
    from mason.session.manager import SessionManager

HELLO_TIMEOUT_S = 10.0
PAIRING_CODE_TTL_S = 600
PAIRING_POLL_INTERVAL_S = 0.5
# How often a paired connection re-checks its device token, so a revocation is
# noticed while the client is idle instead of on its next request.
REVALIDATE_INTERVAL_S = 60.0
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
DEFAULT_HISTORY_LIMIT = 100
# Pending uploads expire after this TTL; consumed ids are released by chat.send.
UPLOAD_TTL_S = 15 * 60.0
# Slack for the JSON envelope (ids, name, mime) around the base64 payload.
UPLOAD_FRAME_HEADROOM_BYTES = 64 * 1024
UPLOAD_KINDS = frozenset({"image", "audio", "video", "file"})

RequestHandler = Callable[["_Connection", RequestFrame], Awaitable[None]]


class DesktopConfig(Base):
    """Desktop (MasonDesk) WebSocket channel configuration."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    allow_from: list[str] = Field(default_factory=list)
    streaming: bool = True
    show_reasoning: bool = True
    send_progress: bool = True
    send_tool_hints: bool = True
    trust_localhost: bool = False
    max_upload_mb: int = 25
    max_fetch_mb: int = 25


@dataclass
class DesktopServices:
    """Read-only framework services attached by the channel manager."""

    session_manager: SessionManager | None = None
    cron_service: CronService | None = None
    memory_stats: Callable[[], dict[str, object] | None] | None = None


@dataclass
class _StreamState:
    """Accumulated text for one in-flight assistant message."""

    message_id: str
    text: str = ""


class _Connection:
    """State for one connected desktop client."""

    def __init__(self, ws: ServerConnection, remote: str | None) -> None:
        self.ws = ws
        self.remote = remote
        self.send_lock = asyncio.Lock()
        self.hello_seen = False
        self.paired = False
        self.device_token: str | None = None
        self.hello_task: asyncio.Task[None] | None = None
        self.pairing_task: asyncio.Task[None] | None = None
        self.revalidation_task: asyncio.Task[None] | None = None

    async def send_frame(self, frame: Mapping[str, Any]) -> None:
        async with self.send_lock:
            await self.ws.send(json.dumps(dict(frame)))

    async def close(self, code: int, reason: str) -> None:
        with contextlib.suppress(Exception):
            await self.ws.close(code, reason)


@dataclass
class _UploadRecord:
    """One stored attachment, owned by the connection that uploaded it."""

    attachment_id: str
    path: Path
    name: str
    mime: str
    kind: str
    size: int
    owner: _Connection
    created_at: float
    duration_ms: int | None = None


class DesktopChannel(BaseChannel):
    """WebSocket transport for the MasonDesk desktop client."""

    name = "desktop"
    display_name = "MasonDesk"
    capabilities: ClassVar[frozenset[str]] = frozenset({TOOL_APPROVALS, ORDERED_TURN_END})

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return DesktopConfig().model_dump(by_alias=True)

    def __init__(
        self,
        config: Any,
        bus: MessageBus,
        *,
        device_ledger_path: Path | None = None,
    ) -> None:
        if isinstance(config, dict):
            config = DesktopConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: DesktopConfig = config
        self._ledger = DeviceLedger(device_ledger_path)
        self._services = DesktopServices()
        self._server: Server | None = None
        self._stopped = asyncio.Event()
        self._connections: set[_Connection] = set()
        self._session_owners: dict[str, _Connection] = {}
        self._active_turns: dict[str, str] = {}
        self._outbound_streams: dict[str, _StreamState] = {}
        self._pending_approvals: dict[str, str] = {}
        self._uploads: dict[str, _UploadRecord] = {}
        self._unsubscribe: list[Callable[[], None]] = []
        self._handlers: dict[str, RequestHandler] = {
            "hello": self._handle_hello,
            "ping": self._handle_ping,
            "chat.send": self._handle_chat_send,
            "chat.inject": self._handle_chat_inject,
            "media.upload": self._handle_media_upload,
            "media.fetch": self._handle_media_fetch,
            "turn.interrupt": self._handle_turn_interrupt,
            "sessions.list": self._handle_sessions_list,
            "sessions.history": self._handle_sessions_history,
            "memory.stats": self._handle_memory_stats,
            "cron.list": self._handle_cron_list,
            "approve": self._handle_approve,
            "deny": self._handle_deny,
        }

    # -- lifecycle ---------------------------------------------------------

    def attach_services(self, **services: Any) -> None:
        """Receive read-only framework services from the channel manager."""
        self._services = DesktopServices(
            session_manager=cast("SessionManager | None", services.get("session_manager")),
            cron_service=cast("CronService | None", services.get("cron_service")),
            memory_stats=cast(
                "Callable[[], dict[str, object] | None] | None",
                services.get("memory_stats"),
            ),
        )

    def start_error_message(self, error: Exception) -> str | None:
        text = str(error)
        return text if text.startswith("desktop:") else None

    @property
    def bound_port(self) -> int | None:
        """The port the server is actually listening on (useful with port 0)."""
        server = self._server
        if server is None:
            return None
        sockets: Any = server.sockets
        for sock in sockets or ():
            # A TCP socket name is (host, port).
            name: tuple[Any, ...] = sock.getsockname()
            port: Any = name[1] if len(name) >= 2 else None
            if isinstance(port, int):
                return int(port)
        return None

    async def start(self) -> None:
        """Bind the WebSocket server and serve until stopped."""
        self._running = True
        self._stopped = asyncio.Event()
        host = self.config.host
        port = int(self.config.port)
        if not _is_loopback_host(host):
            self.logger.warning(
                "desktop: binding to {} exposes plaintext ws:// beyond loopback; terminate "
                "TLS in front of it and rely on device pairing",
                host,
            )
        try:
            self._server = await ws_serve(
                self._handle_connection,
                host,
                port,
                max_size=self._upload_frame_max_bytes(),
            )
        except OSError as exc:
            self._running = False
            raise RuntimeError(
                f"desktop: cannot listen on {host}:{port} ({exc}); change channels.desktop.port"
            ) from exc
        self._unsubscribe = [
            self.bus.subscribe(self._on_turn_status_changed, TurnRunStatusChanged),
            self.bus.subscribe(self._on_session_turn_persisted, SessionTurnPersisted),
        ]
        self.logger.info("desktop: listening on ws://{}:{}", host, port)
        if self.config.trust_localhost:
            self.logger.warning(
                "desktop: trustLocalhost is enabled — any local process is auto-approved "
                "as the '{}' device without pairing. Development use only.",
                TRUSTED_LOOPBACK_DEVICE,
            )
        try:
            await self._stopped.wait()
        finally:
            self._running = False

    async def stop(self) -> None:
        """Close the server, all connections, and the bus subscriptions."""
        self._running = False
        self._stopped.set()
        for unsubscribe in self._unsubscribe:
            with contextlib.suppress(Exception):
                unsubscribe()
        self._unsubscribe = []
        server, self._server = self._server, None
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await server.wait_closed()
        for connection in list(self._connections):
            await connection.close(1001, "channel stopping")
        self._connections.clear()
        self._session_owners.clear()
        self._active_turns.clear()
        self._outbound_streams.clear()
        self._pending_approvals.clear()
        self._release_uploads()

    # -- connection handling ------------------------------------------------

    async def _handle_connection(self, ws: ServerConnection) -> None:
        connection = _Connection(ws, _remote_host(ws))
        self._connections.add(connection)
        connection.hello_task = asyncio.create_task(self._enforce_hello_timeout(connection))
        self.logger.info("desktop: client connected from {}", connection.remote or "unknown")
        try:
            async for raw in ws:
                if not isinstance(raw, str):
                    await connection.close(CLOSE_PROTOCOL_ERROR, "binary frames are not supported")
                    return
                request = parse_request(raw)
                if request is None:
                    await connection.close(CLOSE_PROTOCOL_ERROR, "malformed frame")
                    return
                await self._dispatch(connection, request)
        except ConnectionClosed:
            pass
        finally:
            await self._cleanup_connection(connection)

    async def _cleanup_connection(self, connection: _Connection) -> None:
        for task in (
            connection.hello_task,
            connection.pairing_task,
            connection.revalidation_task,
        ):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self._connections.discard(connection)
        self._release_uploads(connection)
        for session_key, owner in list(self._session_owners.items()):
            if owner is connection:
                self._session_owners.pop(session_key, None)
                self._outbound_streams.pop(session_key, None)
        self.logger.info("desktop: client disconnected from {}", connection.remote or "unknown")

    async def _enforce_hello_timeout(self, connection: _Connection) -> None:
        await asyncio.sleep(HELLO_TIMEOUT_S)
        if not connection.hello_seen:
            await connection.close(CLOSE_HELLO_TIMEOUT, "hello timeout")

    async def _dispatch(self, connection: _Connection, request: RequestFrame) -> None:
        if request.type == "hello":
            await self._handle_hello(connection, request)
            return
        # Everything except ping requires a successful hello.
        if not connection.paired and request.type != "ping":
            await connection.send_frame(
                error_response(request.id, ERROR_NOT_PAIRED, "send a successful hello first")
            )
            return
        handler = self._handlers.get(request.type)
        if handler is None:
            await connection.send_frame(
                error_response(request.id, ERROR_UNSUPPORTED, f"unknown request type: {request.type}")
            )
            return
        await handler(connection, request)

    # -- hello and pairing ---------------------------------------------------

    async def _handle_hello(self, connection: _Connection, request: RequestFrame) -> None:
        connection.hello_seen = True
        if connection.hello_task is not None:
            connection.hello_task.cancel()
        version = request.version
        if version is not None and version != PROTOCOL_VERSION:
            await connection.send_frame(
                error_response(
                    request.id,
                    ERROR_PROTOCOL_MISMATCH,
                    f"this gateway speaks protocol v{PROTOCOL_VERSION}",
                )
            )
            await connection.close(CLOSE_PROTOCOL_ERROR, "protocol mismatch")
            return
        self._log_client_info(connection, request)

        token = optional_text(request.body.get("device_token"))
        if token is not None and is_approved(self.name, token):
            self._ledger.record(token)
            await self._complete_hello(connection, request, token)
            return

        if token is None and self.config.trust_localhost and _is_loopback_host(connection.remote):
            token = TRUSTED_LOOPBACK_DEVICE
            self._approve_token(token)
            self._ledger.record(token)
            await self._complete_hello(connection, request, token)
            return

        reason = "missing"
        if token is not None:
            reason = "revoked" if self._ledger.is_known(token) else "unknown"
        else:
            token = f"device-{uuid.uuid4().hex[:12]}"
        try:
            code = generate_code(self.name, token, ttl=PAIRING_CODE_TTL_S)
        except OSError as exc:
            await connection.send_frame(
                error_response(request.id, ERROR_INTERNAL, f"pairing store unavailable: {exc}")
            )
            return
        await connection.send_frame(
            ok_response(
                request.id,
                {
                    "protocol": PROTOCOL_VERSION,
                    "paired": False,
                    "pairing": {
                        "code": code,
                        "expires_at": _now_ms() + PAIRING_CODE_TTL_S * 1000,
                    },
                    "reason": reason,
                    "limits": self._hello_limits(),
                    "features": self._hello_features(),
                },
            )
        )
        self.logger.info("desktop: pairing code {} issued for {} ({})", code, token, reason)
        self._start_pairing_watch(connection, token)

    def _log_client_info(self, connection: _Connection, request: RequestFrame) -> None:
        client = request.body.get("client")
        if not isinstance(client, dict):
            return
        client_info = cast(dict[str, Any], client)
        name = optional_text(client_info.get("name"))
        version = optional_text(client_info.get("version"))
        if name:
            self.logger.debug(
                "desktop: hello from {} {} ({})",
                name,
                version or "?",
                connection.remote or "unknown",
            )

    async def _complete_hello(
        self, connection: _Connection, request: RequestFrame, token: str
    ) -> None:
        connection.paired = True
        connection.device_token = token
        await connection.send_frame(
            ok_response(
                request.id,
                {
                    "protocol": PROTOCOL_VERSION,
                    "paired": True,
                    "device_token": token,
                    "gateway": {"version": __version__},
                    "limits": self._hello_limits(),
                    "features": self._hello_features(),
                },
            )
        )
        self.logger.info("desktop: device {} paired", token)
        self._start_revalidation(connection, token)

    def _approve_token(self, token: str) -> None:
        """Approve a token in the pairing store (used by loopback trust)."""
        with contextlib.suppress(OSError):
            approve_code(generate_code(self.name, token))

    def _start_pairing_watch(self, connection: _Connection, sender_id: str) -> None:
        task = connection.pairing_task
        if task is not None and not task.done():
            task.cancel()
        connection.pairing_task = asyncio.create_task(self._watch_pairing(connection, sender_id))

    async def _watch_pairing(self, connection: _Connection, sender_id: str) -> None:
        deadline = time.monotonic() + PAIRING_CODE_TTL_S
        while connection in self._connections and not connection.paired:
            if time.monotonic() > deadline:
                return
            if is_approved(self.name, sender_id):
                self._ledger.record(sender_id)
                await connection.send_frame(
                    event_frame("pairing.approved", {"device_token": sender_id})
                )
                self.logger.info("desktop: device {} approved", sender_id)
                return
            await asyncio.sleep(PAIRING_POLL_INTERVAL_S)

    def _start_revalidation(self, connection: _Connection, token: str) -> None:
        task = connection.revalidation_task
        if task is not None and not task.done():
            task.cancel()
        connection.revalidation_task = asyncio.create_task(
            self._watch_revocation(connection, token)
        )

    async def _watch_revocation(self, connection: _Connection, token: str) -> None:
        """Close a paired connection once its device token stops being approved.

        Without this an idle client stays connected until it happens to send a
        frame, so ``mason pairing revoke`` would only take effect on its next
        request. Protocol v1: revoked while connected -> ``unauthorized`` error
        and close 4401.
        """
        while connection in self._connections and connection.paired:
            await asyncio.sleep(REVALIDATE_INTERVAL_S)
            if connection not in self._connections:
                return
            if is_approved(self.name, token):
                continue
            await connection.send_frame(
                error_response(
                    "revocation",
                    ERROR_UNAUTHORIZED,
                    "device token is no longer approved",
                )
            )
            await connection.close(CLOSE_UNAUTHORIZED, "unauthorized")
            self.logger.info("desktop: device {} revoked while connected", token)
            return

    # -- chat ---------------------------------------------------------------

    async def _handle_ping(self, connection: _Connection, request: RequestFrame) -> None:
        await connection.send_frame(
            ok_response(
                request.id,
                {"ts": request.body.get("ts"), "server_time": _now_ms()},
            )
        )

    # -- media uploads -------------------------------------------------------

    def _hello_limits(self) -> dict[str, int]:
        """Advertised client limits, so uploads and fetches can be pre-checked."""
        return {
            "max_upload_bytes": self._upload_limit_bytes(),
            "max_fetch_bytes": self._fetch_limit_bytes(),
        }

    def _hello_features(self) -> list[str]:
        """Additive request frames this channel understands.

        Clients feature-detect: without ``chat.inject`` a gateway only supports
        one turn at a time and no mid-turn input, so clients queue instead.
        """
        return ["chat.inject"]

    async def _handle_media_upload(
        self, connection: _Connection, request: RequestFrame
    ) -> None:
        token = connection.device_token
        if token is None or not is_approved(self.name, token):
            await connection.send_frame(
                error_response(request.id, ERROR_UNAUTHORIZED, "device token is not approved")
            )
            await connection.close(CLOSE_UNAUTHORIZED, "unauthorized")
            return
        self._prune_uploads()
        kind = optional_text(request.body.get("kind")) or ""
        if kind not in UPLOAD_KINDS:
            await connection.send_frame(
                error_response(
                    request.id,
                    ERROR_BAD_REQUEST,
                    "kind must be one of image, audio, video, file",
                )
            )
            return
        data = request.body.get("data")
        if not isinstance(data, str) or not data:
            await connection.send_frame(
                error_response(request.id, ERROR_BAD_REQUEST, "data is required")
            )
            return
        limit = self._upload_limit_bytes()
        try:
            raw = base64.b64decode(data, validate=True)
        except ValueError:
            await connection.send_frame(
                error_response(request.id, ERROR_BAD_REQUEST, "data is not valid base64")
            )
            return
        if len(raw) > limit:
            await connection.send_frame(
                error_response(
                    request.id,
                    ERROR_TOO_LARGE,
                    f"upload exceeds the {self.config.max_upload_mb} MB limit",
                    {"limit_bytes": limit},
                )
            )
            return
        name = _upload_name(request.body.get("name"))
        mime = optional_text(request.body.get("mime")) or "application/octet-stream"
        path = get_media_dir(self.name) / f"{uuid.uuid4().hex}{_upload_extension(name, mime)}"
        try:
            path.write_bytes(raw)
        except OSError:
            self.logger.exception("desktop: failed to store an upload")
            await connection.send_frame(
                error_response(request.id, ERROR_INTERNAL, "failed to store the upload")
            )
            return
        record = _UploadRecord(
            attachment_id=f"att-{uuid.uuid4().hex[:12]}",
            path=path,
            name=name,
            mime=mime,
            kind=kind,
            size=len(raw),
            owner=connection,
            created_at=time.monotonic(),
            duration_ms=_upload_duration_ms(request.body.get("duration_ms")),
        )
        self._uploads[record.attachment_id] = record
        self.logger.debug(
            "desktop: stored {} ({} bytes, kind={}) for {}",
            record.attachment_id,
            record.size,
            record.kind,
            token,
        )
        await connection.send_frame(ok_response(request.id, _attachment_payload(record)))

    async def _handle_media_fetch(
        self, connection: _Connection, request: RequestFrame
    ) -> None:
        """Serve the stored bytes of an attachment referenced by a session.

        Resolution is transcript-scoped: the id must appear on a record
        persisted with the session, so a client can never point the server at
        an arbitrary path.  Fetch is whole-file; players cannot seek.
        """
        token = connection.device_token
        if token is None or not is_approved(self.name, token):
            await connection.send_frame(
                error_response(request.id, ERROR_UNAUTHORIZED, "device token is not approved")
            )
            await connection.close(CLOSE_UNAUTHORIZED, "unauthorized")
            return
        session_id = optional_text(request.body.get("session_id"))
        attachment_id = optional_text(request.body.get("attachment_id"))
        if session_id is None or attachment_id is None:
            await connection.send_frame(
                error_response(
                    request.id,
                    ERROR_BAD_REQUEST,
                    "session_id and attachment_id are required",
                )
            )
            return
        manager = self._services.session_manager
        if manager is None:
            await connection.send_frame(
                error_response(request.id, ERROR_INTERNAL, "session services are unavailable")
            )
            return
        try:
            payload = manager.read_session_file(session_id)
        except Exception:
            self.logger.exception("desktop: failed to read session {}", session_id)
            await connection.send_frame(
                error_response(request.id, ERROR_INTERNAL, "failed to read the session")
            )
            return
        if payload is None:
            await connection.send_frame(
                error_response(request.id, ERROR_NO_SESSION, f"unknown session {session_id}")
            )
            return
        stored = _stored_attachment(payload.get("messages"), attachment_id)
        if stored is None:
            await connection.send_frame(
                error_response(
                    request.id,
                    ERROR_NO_ATTACHMENT,
                    f"attachment {attachment_id} is not referenced by this session",
                )
            )
            return
        media_root = get_media_dir(self.name).resolve()
        stored_path = _stored_attachment_path(stored)
        if (
            stored_path is None
            or not stored_path.is_relative_to(media_root)
            or not stored_path.is_file()
        ):
            await connection.send_frame(
                error_response(
                    request.id,
                    ERROR_MEDIA_GONE,
                    "the stored media file is no longer available",
                )
            )
            return
        limit = self._fetch_limit_bytes()
        try:
            if stored_path.stat().st_size > limit:
                await connection.send_frame(
                    error_response(
                        request.id,
                        ERROR_TOO_LARGE,
                        f"stored file exceeds the {self.config.max_fetch_mb} MB fetch limit",
                        {"limit_bytes": limit},
                    )
                )
                return
            raw = stored_path.read_bytes()
        except OSError:
            await connection.send_frame(
                error_response(
                    request.id,
                    ERROR_MEDIA_GONE,
                    "the stored media file is no longer available",
                )
            )
            return
        result = _stored_attachment_response(stored, base64.b64encode(raw).decode("ascii"))
        await connection.send_frame(ok_response(request.id, result))

    async def _handle_chat_send(self, connection: _Connection, request: RequestFrame) -> None:
        token = connection.device_token
        if token is None or not is_approved(self.name, token):
            await connection.send_frame(
                error_response(request.id, ERROR_UNAUTHORIZED, "device token is not approved")
            )
            await connection.close(CLOSE_UNAUTHORIZED, "unauthorized")
            return
        text = optional_text(request.body.get("text"))
        attachment_ids = _parse_attachment_ids(request.body.get("attachments"))
        if attachment_ids is None:
            await connection.send_frame(
                error_response(
                    request.id, ERROR_BAD_REQUEST, "attachments must be a list of ids"
                )
            )
            return
        if text is None and not attachment_ids:
            await connection.send_frame(
                error_response(request.id, ERROR_BAD_REQUEST, "text is required")
            )
            return
        text = text or ""
        self._prune_uploads()
        attachments: list[_UploadRecord] = []
        for attachment_id in attachment_ids:
            record = self._uploads.get(attachment_id)
            if record is None or record.owner is not connection:
                await connection.send_frame(
                    error_response(
                        request.id,
                        ERROR_NO_ATTACHMENT,
                        f"unknown attachment {attachment_id}",
                    )
                )
                return
            attachments.append(record)
        session_id = optional_text(request.body.get("session_id"))
        if session_id is None:
            session_id = f"{self.name}:{uuid.uuid4().hex[:12]}"
        elif not self._session_exists(session_id):
            await connection.send_frame(
                error_response(request.id, ERROR_NO_SESSION, f"unknown session {session_id}")
            )
            return
        if session_id in self._active_turns:
            await connection.send_frame(
                error_response(
                    request.id,
                    ERROR_BUSY,
                    "a turn is already running for this session",
                )
            )
            return

        # Every check passed: consume the uploads and start the turn.
        for record in attachments:
            self._uploads.pop(record.attachment_id, None)
        turn_id = f"turn-{uuid.uuid4().hex[:8]}"
        self._active_turns[session_id] = turn_id
        self._session_owners[session_id] = connection
        await connection.send_frame(
            ok_response(request.id, {"session_id": session_id, "turn_id": turn_id})
        )
        echo_payload: dict[str, Any] = {
            "session_id": session_id,
            "turn_id": turn_id,
            "message_id": f"msg-{uuid.uuid4().hex[:12]}",
            "role": "user",
            "text": text,
            "ts": _now_ms(),
        }
        if attachments:
            echo_payload["attachments"] = [
                _attachment_payload(record) for record in attachments
            ]
        await connection.send_frame(event_frame("message", echo_payload))
        metadata: dict[str, Any] = {TURN_ID_METADATA_KEY: turn_id}
        if attachments:
            # Persisted with the user turn so sessions.history can return the
            # same records after a reload, and media.fetch can resolve the
            # stored bytes; the private path never leaves the transcript.
            metadata[ATTACHMENTS_METADATA_KEY] = [
                _persisted_attachment_payload(record) for record in attachments
            ]
        try:
            await self._handle_message(
                sender_id=token,
                chat_id=session_id,
                content=await self._attachment_content(text, attachments),
                media=[str(record.path) for record in attachments],
                metadata=metadata,
                session_key=session_id,
            )
        except Exception:
            self._active_turns.pop(session_id, None)
            self.logger.exception("desktop: failed to publish an inbound message")

    async def _handle_chat_inject(
        self, connection: _Connection, request: RequestFrame
    ) -> None:
        """Deliver user input into the turn already running for one session.

        Unlike ``chat.send`` this never starts a turn: the text is published to
        the session inbox, where the agent loop drains it at its next model
        boundary (mid-turn steering).  If the turn finishes first, the same
        session worker handles it as the next turn - the client's queueing
        fallback.  Only the connection that owns the running turn may inject.
        """
        token = connection.device_token
        if token is None or not is_approved(self.name, token):
            await connection.send_frame(
                error_response(request.id, ERROR_UNAUTHORIZED, "device token is not approved")
            )
            await connection.close(CLOSE_UNAUTHORIZED, "unauthorized")
            return
        text = optional_text(request.body.get("text"))
        if text is None:
            await connection.send_frame(
                error_response(request.id, ERROR_BAD_REQUEST, "text is required")
            )
            return
        session_id = optional_text(request.body.get("session_id"))
        if session_id is None:
            await connection.send_frame(
                error_response(request.id, ERROR_BAD_REQUEST, "session_id is required")
            )
            return
        if not self._session_exists(session_id):
            await connection.send_frame(
                error_response(request.id, ERROR_NO_SESSION, f"unknown session {session_id}")
            )
            return
        turn_id = self._active_turns.get(session_id)
        if turn_id is None or self._session_owners.get(session_id) is not connection:
            await connection.send_frame(
                error_response(
                    request.id,
                    ERROR_NO_TURN,
                    "no turn owned by this connection is running for that session",
                )
            )
            return
        await connection.send_frame(
            ok_response(request.id, {"session_id": session_id, "turn_id": turn_id})
        )
        await connection.send_frame(
            event_frame(
                "message",
                {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "message_id": f"msg-{uuid.uuid4().hex[:12]}",
                    "role": "user",
                    "text": text,
                    "ts": _now_ms(),
                },
            )
        )
        try:
            await self._handle_message(
                sender_id=token,
                chat_id=session_id,
                content=text,
                metadata={TURN_ID_METADATA_KEY: turn_id},
                session_key=session_id,
            )
        except Exception:
            self.logger.exception("desktop: failed to publish an injected message")

    async def _handle_turn_interrupt(
        self, connection: _Connection, request: RequestFrame
    ) -> None:
        session_id = optional_text(request.body.get("session_id")) or ""
        turn_id = optional_text(request.body.get("turn_id")) or ""
        if not session_id or self._active_turns.get(session_id) != turn_id:
            await connection.send_frame(
                error_response(request.id, ERROR_NO_TURN, "no matching running turn")
            )
            return
        token = connection.device_token
        if token is None:
            await connection.send_frame(
                error_response(request.id, ERROR_UNAUTHORIZED, "device token is not approved")
            )
            return
        await connection.send_frame(ok_response(request.id, {"interrupted": True}))
        try:
            await self._handle_message(
                sender_id=token,
                chat_id=session_id,
                content="/stop",
                metadata={},
                session_key=session_id,
            )
        except Exception:
            self.logger.exception("desktop: failed to publish the stop command")

    # -- query frames ---------------------------------------------------------

    async def _handle_sessions_list(self, connection: _Connection, request: RequestFrame) -> None:
        manager = self._services.session_manager
        if manager is None:
            await connection.send_frame(
                error_response(request.id, ERROR_INTERNAL, "session services are unavailable")
            )
            return
        limit = _clamp_int(request.body.get("limit"), DEFAULT_LIST_LIMIT, 1, MAX_LIST_LIMIT)
        cursor = optional_text(request.body.get("cursor"))
        try:
            page = manager.list_session_page(limit=limit, cursor=cursor)
        except Exception:
            self.logger.exception("desktop: failed to list sessions")
            await connection.send_frame(
                error_response(request.id, ERROR_INTERNAL, "failed to list sessions")
            )
            return
        sessions = [
            self._session_meta(manager, row, row["key"])
            for row in page.sessions
        ]
        await connection.send_frame(
            ok_response(
                request.id,
                {"sessions": sessions, "next_cursor": page.next_cursor},
            )
        )

    def _session_meta(
        self, manager: SessionManager, row: Mapping[str, Any], key: str
    ) -> dict[str, Any]:
        stored_count = row.get("message_count")
        if isinstance(stored_count, int) and not isinstance(stored_count, bool) and stored_count >= 0:
            message_count = stored_count
        else:
            # Older stores may not persist a count yet; keep the transcript
            # fallback so listings stay correct during migration.
            message_count = 0
            try:
                payload = manager.read_session_file(key)
            except Exception:
                payload = None
            if isinstance(payload, Mapping):
                messages = payload.get("messages")
                message_count = len(cast(list[object], messages)) if isinstance(messages, list) else 0
        title = row.get("title") or row.get("preview") or key
        return {
            "id": key,
            "title": str(title),
            "created_at": epoch_ms(row.get("created_at")),
            "updated_at": epoch_ms(row.get("updated_at")),
            "message_count": message_count,
            "source": key.split(":", 1)[0] if ":" in key else None,
        }

    async def _handle_sessions_history(
        self, connection: _Connection, request: RequestFrame
    ) -> None:
        manager = self._services.session_manager
        if manager is None:
            await connection.send_frame(
                error_response(request.id, ERROR_INTERNAL, "session services are unavailable")
            )
            return
        session_id = optional_text(request.body.get("session_id"))
        if session_id is None:
            await connection.send_frame(
                error_response(request.id, ERROR_BAD_REQUEST, "session_id is required")
            )
            return
        limit = _clamp_int(
            request.body.get("limit"), DEFAULT_HISTORY_LIMIT, 1, MAX_LIST_LIMIT * 10
        )
        try:
            match = SessionAccess(manager).read(session_id, query="", limit=limit)
        except Exception:
            self.logger.exception("desktop: failed to read session {}", session_id)
            await connection.send_frame(
                error_response(request.id, ERROR_INTERNAL, "failed to read the session")
            )
            return
        if match is None:
            await connection.send_frame(
                error_response(request.id, ERROR_NO_SESSION, f"unknown session {session_id}")
            )
            return
        fallback_ts = epoch_ms(match.get("updated_at"))
        messages: list[dict[str, Any]] = []
        for message in match["messages"]:
            payload: dict[str, Any] = {
                "message_id": f"{session_id}:{message['message_index']}",
                "role": message["role"],
                "text": message["content"],
                "ts": epoch_ms(message["timestamp"], fallback_ts),
            }
            if message["continued"]:
                payload["continued"] = True
            if message["attachments"]:
                payload["attachments"] = [
                    {
                        key: value
                        for key, value in attachment.items()
                        if key != "duration_ms" or value is not None
                    }
                    for attachment in message["attachments"]
                ]
            if message["reasoning"]:
                payload["reasoning"] = [
                    {"text": segment} for segment in message["reasoning"]
                ]
            if message["tools"]:
                payload["tools"] = [
                    {
                        "name": tool["name"],
                        "ok": tool["ok"],
                        **({"summary": tool["summary"]} if tool["summary"] else {}),
                    }
                    for tool in message["tools"]
                ]
            if message["file_edits"]:
                payload["file_edits"] = [
                    {"path": edit["path"], "ok": edit["ok"]}
                    for edit in message["file_edits"]
                ]
            messages.append(payload)
        await connection.send_frame(
            ok_response(
                request.id,
                {"session_id": session_id, "messages": messages, "next_cursor": None},
            )
        )

    async def _handle_memory_stats(self, connection: _Connection, request: RequestFrame) -> None:
        provider = self._services.memory_stats
        if provider is None:
            await connection.send_frame(
                error_response(request.id, ERROR_INTERNAL, "memory services are unavailable")
            )
            return
        try:
            stats = provider()
        except Exception:
            self.logger.exception("desktop: failed to read memory stats")
            stats = None
        if not isinstance(stats, dict):
            await connection.send_frame(
                error_response(request.id, ERROR_INTERNAL, "memory stats are unavailable")
            )
            return
        await connection.send_frame(ok_response(request.id, stats))

    async def _handle_cron_list(self, connection: _Connection, request: RequestFrame) -> None:
        service = self._services.cron_service
        if service is None:
            await connection.send_frame(
                error_response(request.id, ERROR_INTERNAL, "cron services are unavailable")
            )
            return
        try:
            jobs = service.list_jobs(include_disabled=True)
        except Exception:
            self.logger.exception("desktop: failed to list cron jobs")
            await connection.send_frame(
                error_response(request.id, ERROR_INTERNAL, "failed to list cron jobs")
            )
            return
        await connection.send_frame(
            ok_response(request.id, {"jobs": [_cron_job(job) for job in jobs]})
        )

    # -- tool approvals --------------------------------------------------------

    async def _send_approval(self, session_key: str, event: ApprovalRequestEvent) -> None:
        """Ask the session owner to approve or deny a pending tool call."""
        connection = self._session_owners.get(session_key)
        if connection is None:
            self.logger.debug(
                "desktop: no client owns session {}; dropping approval {}",
                session_key,
                event.approval_id,
            )
            return
        self._prune_approvals()
        self._pending_approvals[event.approval_id] = session_key
        payload: dict[str, Any] = {
            "session_id": session_key,
            "turn_id": event.turn_id or self._active_turns.get(session_key),
            "approval_id": event.approval_id,
            "tool": event.tool,
            "summary": event.summary,
        }
        if event.args_preview:
            payload["args_preview"] = event.args_preview
        if event.deadline is not None:
            payload["deadline"] = epoch_ms(event.deadline)
        await connection.send_frame(event_frame("approval", payload))

    def _prune_approvals(self) -> None:
        if not self._pending_approvals:
            return
        live = pending_tool_approvals()
        self._pending_approvals = {
            approval_id: session_key
            for approval_id, session_key in self._pending_approvals.items()
            if approval_id in live
        }

    async def _handle_approve(self, connection: _Connection, request: RequestFrame) -> None:
        await self._resolve_approval(connection, request, approved=True)

    async def _handle_deny(self, connection: _Connection, request: RequestFrame) -> None:
        await self._resolve_approval(connection, request, approved=False)

    async def _resolve_approval(
        self,
        connection: _Connection,
        request: RequestFrame,
        *,
        approved: bool,
    ) -> None:
        token = connection.device_token
        if token is None or not is_approved(self.name, token):
            await connection.send_frame(
                error_response(request.id, ERROR_UNAUTHORIZED, "device token is not approved")
            )
            return
        approval_id = optional_text(request.body.get("approval_id"))
        session_key = (
            self._pending_approvals.pop(approval_id, None) if approval_id else None
        )
        if (
            approval_id is None
            or session_key is None
            or self._session_owners.get(session_key) is not connection
        ):
            await connection.send_frame(
                error_response(
                    request.id,
                    ERROR_NO_APPROVAL,
                    f"unknown approval {approval_id or '?'}",
                )
            )
            return
        if not resolve_tool_approval(approval_id, approved=approved):
            await connection.send_frame(
                error_response(
                    request.id,
                    ERROR_NO_APPROVAL,
                    f"approval {approval_id} is no longer pending",
                )
            )
            return
        await connection.send_frame(
            ok_response(request.id, {"approval_id": approval_id, "approved": approved})
        )

    # -- outbound hooks --------------------------------------------------------

    async def send(self, msg: OutboundMessage) -> None:
        """Deliver a complete message (or a plain progress event) to its client."""
        if PAIRING_CODE_META_KEY in msg.metadata:
            self.logger.debug("desktop: ignoring a pairing reply for {}", msg.chat_id)
            return
        event = msg.event
        if isinstance(event, ProgressEvent):
            if event.file_edit_events:
                await self.send_file_edit_events(
                    msg.chat_id, event.file_edit_events, msg.metadata, turn_id=event.turn_id,
                )
                return
            if event.tool_events:
                await self._send_tool_events(
                    msg.chat_id, event.tool_events, turn_id=event.turn_id,
                )
            if event.content:
                await self._send_progress_line(
                    msg.chat_id, event.content, event.tool_hint, turn_id=event.turn_id,
                )
            return
        if event is not None:
            if isinstance(event, ApprovalRequestEvent):
                await self._send_approval(msg.chat_id, event)
                return
            if isinstance(event, TurnEndEvent):
                await self._on_turn_end_event(msg.chat_id, event)
                return
            self.logger.debug(
                "desktop: ignoring outbound event {} for {}",
                type(event).__name__,
                msg.chat_id,
            )
            return
        if not msg.content:
            return
        connection = self._session_owners.get(msg.chat_id)
        if connection is None:
            self.logger.debug("desktop: no client owns session {}; dropping message", msg.chat_id)
            return
        await connection.send_frame(
            event_frame(
                "message",
                {
                    "session_id": msg.chat_id,
                    "turn_id": optional_text(
                        msg.metadata.get(TURN_ID_METADATA_KEY)
                    ) or self._active_turns.get(msg.chat_id),
                    "message_id": f"msg-{uuid.uuid4().hex[:12]}",
                    "role": "assistant",
                    "text": msg.content,
                    "ts": _now_ms(),
                },
            )
        )

    async def send_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
        *,
        stream_id: str | None = None,
        stream_end: bool = False,
        resuming: bool = False,
        merge_next: bool = False,
        turn_id: str | None = None,
    ) -> None:
        """Stream assistant text; the accumulated segment ends as one message.

        Buffers are keyed by session rather than ``stream_id``: the protocol
        guarantees one active turn per session, and keying by session keeps
        reasoning deltas (which may not carry a stream id) attached to the
        same user-visible message.
        """
        turn_id = turn_id or self._active_turns.get(chat_id)
        connection = self._session_owners.get(chat_id)
        state = self._outbound_streams.get(chat_id)
        if delta:
            state = self._ensure_stream(chat_id)
            state.text += delta
            if connection is not None:
                await connection.send_frame(
                    event_frame(
                        "delta",
                        {
                            "session_id": chat_id,
                            "turn_id": turn_id,
                            "message_id": state.message_id,
                            "text_delta": delta,
                        },
                    )
                )
        if not stream_end:
            return
        if resuming:
            if merge_next:
                # The same user-visible message continues in a later segment.
                return
            # A recovery boundary that will regenerate this text: tell the
            # client to discard what it already rendered, then drop the buffer.
            if state is not None and state.text and connection is not None:
                await connection.send_frame(
                    event_frame(
                        "message",
                        {
                            "session_id": chat_id,
                            "turn_id": turn_id,
                            "message_id": state.message_id,
                            "role": "assistant",
                            "text": "",
                            "ts": _now_ms(),
                            "replaced": True,
                        },
                    )
                )
            self._outbound_streams.pop(chat_id, None)
            return
        if state is None:
            return
        if connection is not None:
            await self._flush_stream(chat_id, state, connection, turn_id=turn_id)
        self._outbound_streams.pop(chat_id, None)
        return

    async def send_reasoning_delta(
        self,
        chat_id: str,
        delta: str,
        metadata: dict[str, Any] | None = None,
        *,
        stream_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        if not delta:
            return
        turn_id = turn_id or self._active_turns.get(chat_id)
        connection = self._session_owners.get(chat_id)
        if connection is None:
            return
        state = self._ensure_stream(chat_id)
        await connection.send_frame(
            event_frame(
                "reasoning_delta",
                {
                    "session_id": chat_id,
                    "turn_id": turn_id,
                    "message_id": state.message_id,
                    "text_delta": delta,
                },
            )
        )

    async def send_reasoning_end(
        self,
        chat_id: str,
        metadata: dict[str, Any] | None = None,
        *,
        stream_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        turn_id = turn_id or self._active_turns.get(chat_id)
        connection = self._session_owners.get(chat_id)
        state = self._outbound_streams.get(chat_id)
        if connection is None or state is None:
            return
        await connection.send_frame(
            event_frame(
                "reasoning_end",
                {
                    "session_id": chat_id,
                    "turn_id": turn_id,
                    "message_id": state.message_id,
                },
            )
        )

    async def send_file_edit_events(
        self,
        chat_id: str,
        edits: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        *,
        turn_id: str | None = None,
    ) -> None:
        turn_id = turn_id or self._active_turns.get(chat_id)
        connection = self._session_owners.get(chat_id)
        if connection is None:
            return
        for edit in edits:
            path = optional_text(edit.get("path"))
            if path is None:
                continue
            phase = optional_text(edit.get("phase")) or "start"
            if phase not in {"start", "end", "error"}:
                phase = "start"
            await connection.send_frame(
                event_frame(
                    "file_edit",
                    {
                        "session_id": chat_id,
                        "turn_id": turn_id,
                        "path": path,
                        "phase": phase,
                        "ok": True if phase == "end" else None,
                        "diff_preview": diff_preview(edit),
                    },
                )
            )

    def _ensure_stream(self, chat_id: str) -> _StreamState:
        state = self._outbound_streams.get(chat_id)
        if state is None:
            state = _StreamState(message_id=f"msg-{uuid.uuid4().hex[:12]}")
            self._outbound_streams[chat_id] = state
        return state

    async def _flush_stream(
        self,
        chat_id: str,
        state: _StreamState,
        connection: _Connection,
        *,
        turn_id: str | None = None,
    ) -> None:
        turn_id = turn_id or self._active_turns.get(chat_id)
        if not state.text:
            return
        await connection.send_frame(
            event_frame(
                "message",
                {
                    "session_id": chat_id,
                    "turn_id": turn_id,
                    "message_id": state.message_id,
                    "role": "assistant",
                    "text": state.text,
                    "ts": _now_ms(),
                },
            )
        )

    async def _send_progress_line(
        self,
        chat_id: str,
        text: str,
        tool_hint: bool,
        *,
        turn_id: str | None = None,
    ) -> None:
        turn_id = turn_id or self._active_turns.get(chat_id)
        connection = self._session_owners.get(chat_id)
        if connection is None:
            return
        await connection.send_frame(
            event_frame(
                "progress",
                {
                    "session_id": chat_id,
                    "turn_id": turn_id,
                    "kind": "tool_hint" if tool_hint else "progress",
                    "text": text,
                },
            )
        )

    async def _send_tool_events(
        self,
        chat_id: str,
        events: list[dict[str, Any]],
        *,
        turn_id: str | None = None,
    ) -> None:
        turn_id = turn_id or self._active_turns.get(chat_id)
        connection = self._session_owners.get(chat_id)
        if connection is None:
            return
        for payload in events:
            call_id = optional_text(payload.get("call_id"))
            name = optional_text(payload.get("name"))
            phase = optional_text(payload.get("phase"))
            if call_id is None or name is None or phase not in {"start", "end", "error"}:
                continue
            ok = True if phase == "end" else (False if phase == "error" else None)
            await connection.send_frame(
                event_frame(
                    "tool",
                    {
                        "session_id": chat_id,
                        "turn_id": turn_id,
                        "tool_call_id": call_id,
                        "name": name,
                        "phase": phase,
                        "summary": tool_summary(payload),
                        "args_preview": preview_text(payload.get("arguments")),
                        "ok": ok,
                    },
                )
            )

    # -- turn lifecycle ---------------------------------------------------------

    async def _on_turn_end_event(self, session_key: str, event: TurnEndEvent) -> None:
        """Render the ordered terminal signal as one ``turn.end`` frame."""
        active_turn = self._active_turns.pop(session_key, None)
        turn_id = event.turn_id or active_turn
        if turn_id is None:
            return
        payload: dict[str, Any] = {
            "session_id": session_key,
            "turn_id": turn_id,
            "status": event.status,
        }
        if event.status == "error" and event.error:
            payload["error"] = event.error
        await self._emit_to_session(session_key, "turn.end", payload)

    async def _on_turn_status_changed(self, event: TurnRunStatusChanged) -> None:
        """Clean up a finished run; the ordered terminal event owns turn.end."""
        if event.status != "idle":
            return
        session_key = event.context.session_key
        turn_id = self._active_turns.pop(session_key, None)
        if turn_id is None:
            return
        # Commit any pending stream text so a late flush still lands before
        # the ordered turn.end frame, and release the session's busy state.
        state = self._outbound_streams.pop(session_key, None)
        connection = self._session_owners.get(session_key)
        if state is not None and connection is not None:
            with contextlib.suppress(Exception):
                await self._flush_stream(session_key, state, connection, turn_id=turn_id)

    async def _on_session_turn_persisted(self, event: SessionTurnPersisted) -> None:
        """Push a live session-list update once a turn is durably stored."""
        session_key = event.context.session_key
        if session_key not in self._session_owners:
            return
        payload = self._session_update_payload(session_key)
        if payload is None:
            return
        await self._emit_to_session(session_key, "session.updated", payload)

    def _session_update_payload(self, session_key: str) -> dict[str, Any] | None:
        manager = self._services.session_manager
        if manager is None:
            return None
        try:
            record = manager.read_session_metadata(session_key)
        except Exception:
            self.logger.exception(
                "desktop: failed to read session metadata for {}", session_key,
            )
            return None
        if not isinstance(record, Mapping):
            return None
        payload: dict[str, Any] = {
            "session_id": session_key,
            "updated_at": epoch_ms(record.get("updated_at")) or _now_ms(),
        }
        metadata = record.get("metadata")
        if isinstance(metadata, Mapping):
            metadata_map = cast(Mapping[str, Any], metadata)
            title = metadata_map.get("title")
            if isinstance(title, str) and title.strip():
                payload["title"] = title.strip()
        message_count = record.get("message_count")
        if (
            isinstance(message_count, int)
            and not isinstance(message_count, bool)
            and message_count >= 0
        ):
            payload["message_count"] = message_count
        return payload

    async def _emit_to_session(
        self, session_key: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        connection = self._session_owners.get(session_key)
        if connection is None:
            return
        await connection.send_frame(event_frame(event_type, payload))

    # -- helpers ------------------------------------------------------------------

    def _upload_limit_bytes(self) -> int:
        """The accepted decoded size for one upload, in bytes."""
        return max(0, int(self.config.max_upload_mb)) * 1024 * 1024

    def _fetch_limit_bytes(self) -> int:
        """The largest stored file media.fetch will return, in bytes."""
        return max(0, int(self.config.max_fetch_mb)) * 1024 * 1024

    def _upload_frame_max_bytes(self) -> int:
        """Frame size that admits a capped base64 upload plus its JSON envelope."""
        encoded = 4 * ((self._upload_limit_bytes() + 2) // 3)
        return max(FRAME_MAX_BYTES, encoded + UPLOAD_FRAME_HEADROOM_BYTES)

    def _prune_uploads(self) -> None:
        """Drop expired uploads and delete their backing files (lazy)."""
        if not self._uploads:
            return
        cutoff = time.monotonic() - UPLOAD_TTL_S
        expired = [
            record for record in self._uploads.values() if record.created_at <= cutoff
        ]
        for record in expired:
            self._uploads.pop(record.attachment_id, None)
            _unlink_upload(record.path)

    def _release_uploads(self, connection: _Connection | None = None) -> None:
        """Delete pending uploads on shutdown, or one connection's on disconnect.

        Consumed uploads are no longer tracked: their files stay on disk for the
        turn that references them.
        """
        if connection is None:
            released = list(self._uploads.values())
            self._uploads.clear()
        else:
            released = [
                record for record in self._uploads.values() if record.owner is connection
            ]
            for record in released:
                self._uploads.pop(record.attachment_id, None)
        for record in released:
            _unlink_upload(record.path)

    async def _attachment_content(
        self, text: str, attachments: list[_UploadRecord]
    ) -> str:
        """Build the model-facing content line for a turn's attachments.

        Empty text falls back to one placeholder per attachment; audio
        attachments contribute a best-effort transcription, mirroring the
        Telegram channel.
        """
        parts: list[str] = (
            [text]
            if text
            else [f"[{record.kind}: {record.name}]" for record in attachments]
        )
        for record in attachments:
            if record.kind != "audio":
                continue
            transcription = await self._transcribe_attachment(record.path)
            if transcription:
                parts.append(f"[transcription: {transcription}]")
        return "\n".join(parts)

    async def _transcribe_attachment(self, path: Path) -> str:
        """Transcribe an audio attachment; a failure only loses the transcript."""
        try:
            return await self.transcribe_audio(path)
        except Exception:
            self.logger.exception("desktop: failed to transcribe {}", path)
            return ""

    def _session_exists(self, session_key: str) -> bool:
        manager = self._services.session_manager
        if manager is None:
            # Without services the agent decides; unknown keys become new files.
            return True
        try:
            return manager.read_session_metadata(session_key) is not None
        except Exception:
            self.logger.exception("desktop: session lookup failed for {}", session_key)
            return False


def _cron_job(job: object) -> dict[str, Any]:
    state = getattr(job, "state", None)
    payload = getattr(job, "payload", None)
    name = optional_text(getattr(job, "name", None))
    job_id = optional_text(getattr(job, "id", None))
    return {
        "id": job_id or "?",
        "name": name,
        "schedule": schedule_text(getattr(job, "schedule", None)),
        "enabled": bool(getattr(job, "enabled", True)),
        "next_run_at": optional_ms(getattr(state, "next_run_at_ms", None)),
        "last_run_at": optional_ms(getattr(state, "last_run_at_ms", None)),
        "last_status": optional_text(getattr(state, "last_status", None)),
        "payload_preview": preview_text(getattr(payload, "message", None), 120),
    }


_EXTENSION_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


def _parse_attachment_ids(value: object) -> list[str] | None:
    """Validate a ``chat.send`` attachments list; ``None`` marks a bad shape."""
    if value is None:
        return []
    if not isinstance(value, list):
        return None
    attachment_ids: list[str] = []
    for entry in cast(list[object], value):
        attachment_id = optional_text(entry)
        if attachment_id is None:
            return None
        attachment_ids.append(attachment_id)
    return attachment_ids


def _upload_duration_ms(value: object) -> int | None:
    """Normalize an optional media duration from an upload request."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    duration = int(value)
    return duration if duration >= 0 else None


def _upload_name(value: object) -> str:
    """Reduce a client-supplied filename to a harmless display basename."""
    name = optional_text(value) or "upload"
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    return safe_filename(name)[:255] or "upload"


def _upload_extension(name: str, mime: str) -> str:
    """Pick a safe extension for the stored file; client names are untrusted."""
    suffix = Path(name).suffix.lower()
    if _is_safe_extension(suffix):
        return suffix
    guessed = (mimetypes.guess_extension(mime, strict=False) or "").lower()
    return guessed if _is_safe_extension(guessed) else ""


def _is_safe_extension(extension: str) -> bool:
    return (
        2 <= len(extension) <= 11
        and extension.startswith(".")
        and all(char in _EXTENSION_ALPHABET for char in extension[1:])
    )


def _attachment_payload(record: _UploadRecord) -> dict[str, Any]:
    """The wire projection shared by the upload result and the user-turn echo."""
    payload: dict[str, Any] = {
        "attachment_id": record.attachment_id,
        "name": record.name,
        "mime": record.mime,
        "kind": record.kind,
        "size": record.size,
    }
    if record.duration_ms is not None:
        payload["duration_ms"] = record.duration_ms
    return payload


def _persisted_attachment_payload(record: _UploadRecord) -> dict[str, Any]:
    """The transcript projection: public fields plus the private stored path.

    ``media.fetch`` resolves ids through this record; public projections drop
    the underscore-prefixed key, so it never reaches a frame or the model.
    """
    return _attachment_payload(record) | {ATTACHMENT_PATH_KEY: str(record.path)}


def _stored_attachment(messages: object, attachment_id: str) -> dict[str, Any] | None:
    """Find the persisted attachment record for an id in a session transcript."""
    if not isinstance(messages, list):
        return None
    for raw_message in cast(list[object], messages):
        if not isinstance(raw_message, Mapping):
            continue
        records = cast(Mapping[str, Any], raw_message).get(ATTACHMENTS_METADATA_KEY)
        if not isinstance(records, list):
            continue
        for raw_record in cast(list[object], records):
            if not isinstance(raw_record, Mapping):
                continue
            record = cast(Mapping[str, Any], raw_record)
            if record.get("attachment_id") == attachment_id:
                return dict(record)
    return None


def _stored_attachment_path(stored: Mapping[str, Any]) -> Path | None:
    """Resolve a record's private path; ``None`` when absent or unresolvable."""
    raw_path = stored.get(ATTACHMENT_PATH_KEY)
    if not isinstance(raw_path, str) or not raw_path:
        return None
    try:
        return Path(raw_path).resolve()
    except OSError:
        return None


def _stored_attachment_response(stored: Mapping[str, Any], data: str) -> dict[str, Any]:
    """The media.fetch result: the public record plus the base64 bytes."""
    payload = dict(public_attachment(stored))
    if payload.get("duration_ms") is None:
        payload.pop("duration_ms", None)
    payload["data"] = data
    return payload


def _unlink_upload(path: Path) -> None:
    """Best-effort deletion of a pruned upload's backing file."""
    with contextlib.suppress(OSError):
        path.unlink()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _remote_host(connection: ServerConnection) -> str | None:
    remote = getattr(connection, "remote_address", None)
    if isinstance(remote, tuple) and remote and isinstance(remote[0], str):
        return remote[0]
    return None


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", "localhost.localdomain"}


def _clamp_int(value: object, default: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(low, min(high, int(value)))
