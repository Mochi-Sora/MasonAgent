"""Capabilities channels declare to routing and delivery policy.

Delivery code asks by channel name instead of hardcoding names: a channel opts
into behavior through its :class:`~mason.channels.plugin.ChannelPlugin`
manifest (``capabilities``) or its :class:`~mason.channels.base.BaseChannel`
class attribute, and the manager registers both when the channel is built.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Render interactive operation notices: retry status, recovery state, and
#: response-source attribution (see :mod:`mason.bus.notification_delivery`).
INTERACTIVE_NOTIFICATIONS = "interactive_notifications"

#: Accept idle session notices (for example the context-compaction lifecycle)
#: addressed by the session key when the session has no saved delivery route.
SESSION_NOTIFICATIONS = "session_notifications"

#: Answer tool-approval requests: render the approval prompt and deliver the
#: user's approve/deny decision (see :mod:`mason.agent.tools.approvals`).
TOOL_APPROVALS = "tool_approvals"

#: Opt-ins for clients that are not managed channel plugins.
_DEFAULT_DECLARATIONS: dict[str, frozenset[str]] = {
    # The terminal CLI renders session notices itself.
    "cli": frozenset({SESSION_NOTIFICATIONS}),
    # The legacy websocket client was the original interactive audience.
    "websocket": frozenset({INTERACTIVE_NOTIFICATIONS, SESSION_NOTIFICATIONS}),
}

_declared: dict[str, frozenset[str]] = {}


def declare_channel_capabilities(
    name: str, capabilities: Iterable[str],
) -> frozenset[str]:
    """Add capabilities for one channel name; returns the merged set."""
    if not name:
        return frozenset()
    merged = channel_capabilities(name) | {
        capability for capability in capabilities if capability
    }
    _declared[name] = merged
    return merged


def channel_capabilities(name: str) -> frozenset[str]:
    """Return the capabilities declared for *name* (built-in opt-ins included)."""
    return _DEFAULT_DECLARATIONS.get(name, frozenset()) | _declared.get(name, frozenset())


def channel_supports(name: str, capability: str) -> bool:
    """Return whether *name* declared *capability*."""
    return capability in channel_capabilities(name)
