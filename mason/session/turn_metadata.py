"""Metadata keys that identify one turn across ingress surfaces.

These keys started life in the WebUI protocol and are still used by recovery,
triggers, and cron deliveries to identify the turn a message belongs to, so they
live here rather than in any single channel.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

WEBUI_TURN_METADATA_KEY = "webui_turn_id"
WEBUI_SYSTEM_COMMAND_TURN_PREFIX = "webui-system:"
WEBSOCKET_TURN_OWNER_METADATA_KEY = "_websocket_turn_owner"
WEBUI_MESSAGE_SOURCE_METADATA_KEY = "_webui_message_source"

#: Caller-supplied turn id that the framework adopts for the whole turn.
TURN_ID_METADATA_KEY = "turn_id"


def supplied_turn_id(metadata: Mapping[str, Any] | None) -> str | None:
    """Return the caller-supplied turn id, if one is present.

    Surfaces that must hand out a correlation id before the framework admits
    the turn (the desktop protocol's ``chat.send`` acknowledgement) put it on
    the inbound metadata; the loop adopts it so every event and reply for the
    turn carries the id the client already knows.
    """
    if not metadata:
        return None
    value = metadata.get(TURN_ID_METADATA_KEY)
    if isinstance(value, str) and value.strip():
        return value
    return None
