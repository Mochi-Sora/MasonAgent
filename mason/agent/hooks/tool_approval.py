"""Agent hook that gates configured tools behind a human approval round trip."""

from __future__ import annotations

import json
import time
from typing import Any, cast

from mason.agent.hook import AgentHook, AgentHookContext
from mason.agent.tools.approvals import BROKER, ToolApprovalBroker, ToolApprovalDeniedError
from mason.bus.outbound_events import ApprovalRequestEvent
from mason.channels.capabilities import TOOL_APPROVALS, channel_supports
from mason.events import EventSink
from mason.providers.base import ToolCallRequest

#: Keys worth surfacing in the one-line prompt summary, most specific first.
_SUMMARY_KEYS = ("command", "path", "file_path", "url", "query", "pattern", "need")

_ARGS_PREVIEW_LIMIT = 512
_SUMMARY_LIMIT = 200


class ToolApprovalHook(AgentHook):
    """Suspend configured tool calls until a connected client decides."""

    def __init__(
        self,
        *,
        events: EventSink,
        channel: str,
        require: frozenset[str],
        timeout_s: float,
        broker: ToolApprovalBroker = BROKER,
    ) -> None:
        # A denial is a control signal: CompositeHook must not swallow it.
        super().__init__(reraise=True)
        self._publish = events.publish if events.accepts(ApprovalRequestEvent) else None
        self._channel = channel
        self._require = require
        self._timeout_s = timeout_s
        self._broker = broker

    async def before_execute_tool(
        self,
        context: AgentHookContext,
        tool_call: ToolCallRequest,
        tool: Any,
        params: Any,
    ) -> None:
        if tool_call.name not in self._require:
            return
        if self._publish is None or not channel_supports(self._channel, TOOL_APPROVALS):
            raise ToolApprovalDeniedError(
                f"Tool '{tool_call.name}' requires user approval, and channel "
                f"'{self._channel}' cannot ask for it."
            )
        approval_id = self._broker.request()
        try:
            await self._publish(ApprovalRequestEvent(
                approval_id=approval_id,
                tool=tool_call.name,
                summary=_summary(tool_call, params),
                args_preview=_args_preview(tool_call),
                deadline=time.time() + self._timeout_s,
            ))
        except Exception:
            self._broker.discard(approval_id)
            raise
        approved = await self._broker.wait(approval_id, timeout_s=self._timeout_s)
        if not approved:
            raise ToolApprovalDeniedError(
                f"Tool '{tool_call.name}' was denied (or the request timed out)."
            )


def _summary(tool_call: ToolCallRequest, params: Any) -> str:
    """A one-line description of what the tool would do, for the prompt."""
    target = ""
    if isinstance(params, dict):
        typed_params = cast(dict[str, Any], params)
        for key in _SUMMARY_KEYS:
            value = typed_params.get(key)
            if isinstance(value, str) and value.strip():
                target = value.strip()
                break
    if not target:
        return tool_call.name[:_SUMMARY_LIMIT]
    return f"{tool_call.name}: {target}"[:_SUMMARY_LIMIT]


def _args_preview(tool_call: ToolCallRequest) -> str | None:
    """A compact preview of the raw call arguments."""
    arguments = tool_call.arguments
    if arguments is None:
        return None
    if isinstance(arguments, str):
        text = arguments.strip()
    else:
        try:
            text = json.dumps(arguments, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(arguments)
    if not text or text == "{}":
        return None
    return text[:_ARGS_PREVIEW_LIMIT]
