"""Metadata keys that identify one turn across ingress surfaces.

These keys started life in the WebUI protocol and are still used by recovery,
triggers, and cron deliveries to identify the turn a message belongs to, so they
live here rather than in any single channel.
"""

WEBUI_TURN_METADATA_KEY = "webui_turn_id"
WEBUI_SYSTEM_COMMAND_TURN_PREFIX = "webui-system:"
WEBSOCKET_TURN_OWNER_METADATA_KEY = "_websocket_turn_owner"
WEBUI_MESSAGE_SOURCE_METADATA_KEY = "_webui_message_source"
