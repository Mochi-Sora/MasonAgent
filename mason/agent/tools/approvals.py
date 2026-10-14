"""Human-in-the-loop tool approval round trips.

A tool call on the configured approval list publishes an
:class:`~mason.bus.outbound_events.ApprovalRequestEvent` to its session's
channel, then suspends the call until a connected client resolves it through
:func:`resolve_tool_approval`.  Unanswered requests deny on their deadline, and
channels that cannot render approvals (no ``TOOL_APPROVALS`` capability) deny
immediately -- the gate always fails closed.

The broker is process-wide because the gateway runs the agent loop and its
channels in one process, and the decision arrives from whichever component
owns the client connection.
"""

from __future__ import annotations

import asyncio
import secrets

#: The desktop protocol's id prefix, kept short for display.
_APPROVAL_ID_PREFIX = "approval"


class ToolApprovalDeniedError(Exception):
    """A gated tool call was refused; the runner reports it to the model."""


class ToolApprovalBroker:
    """Registry of tool calls awaiting a human decision."""

    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future[bool]] = {}

    def request(self) -> str:
        """Open one approval round trip and return its id."""
        approval_id = f"{_APPROVAL_ID_PREFIX}-{secrets.token_hex(6)}"
        self._waiters[approval_id] = asyncio.get_running_loop().create_future()
        return approval_id

    async def wait(self, approval_id: str, *, timeout_s: float) -> bool:
        """Wait for the decision; a timeout or a missing waiter denies."""
        future = self._waiters.get(approval_id)
        if future is None:
            return False
        try:
            return await asyncio.wait_for(future, timeout_s)
        except asyncio.TimeoutError:
            return False
        finally:
            self._waiters.pop(approval_id, None)

    def resolve(self, approval_id: str, *, approved: bool) -> bool:
        """Deliver a decision; returns whether a waiting call consumed it."""
        future = self._waiters.get(approval_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    def discard(self, approval_id: str) -> None:
        """Drop a waiter that never reached its wait (publish failure)."""
        self._waiters.pop(approval_id, None)

    def pending(self) -> frozenset[str]:
        """Ids of approvals still awaiting a decision."""
        return frozenset(self._waiters)


#: Process-wide broker shared by the agent loop and the channels.
BROKER = ToolApprovalBroker()


def resolve_tool_approval(approval_id: str, *, approved: bool) -> bool:
    """Resolve one pending tool approval (a client's approve/deny decision)."""
    return BROKER.resolve(approval_id, approved=approved)


def pending_tool_approvals() -> frozenset[str]:
    """Ids of tool approvals still awaiting a decision."""
    return BROKER.pending()
