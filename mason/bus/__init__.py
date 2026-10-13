"""Message bus module for decoupled channel-agent communication."""

from mason.bus.events import InboundMessage, OutboundMessage
from mason.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
