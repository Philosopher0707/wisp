"""Async server transport for Wisp — bridges WispAgentCore to WebSocket clients.

Consumes AgentEvent instances and serializes them to JSON for remote clients.
Handles async tool approval by sending requests to the client and awaiting responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Optional

from wisp.core.agent import WispAgentCore
from wisp.core.events import (
    AgentEvent,
    TYPE_CONTENT,
    TYPE_THINKING,
    TYPE_TOOL_CALL,
    TYPE_TOOL_RESULT,
    TYPE_ERROR,
    TYPE_DONE,
    TYPE_SYSTEM,
    TYPE_APPROVAL_REQUEST,
)

logger = logging.getLogger(__name__)


class PendingApproval:
    """Represents a tool call waiting for client approval (async-aware)."""

    def __init__(self, call_id: str, name: str, arguments: dict):
        self.call_id = call_id
        self.name = name
        self.arguments = arguments
        self.event = asyncio.Event()
        self.approved: bool = False
        self.denied_reason: Optional[str] = None


class ServerTransport:
    """Async WebSocket transport for WispAgentCore.

    Streams events as JSON messages to a remote client and handles
    asynchronous tool approval.
    """

    def __init__(self, core: WispAgentCore, send_callback):
        self.core = core
        self._send_callback = send_callback
        self._pending_approvals: dict[str, PendingApproval] = {}
        self._approval_lock = asyncio.Lock()
        self._call_counter = 0
        self._interrupted = False

    def _next_call_id(self) -> str:
        self._call_counter += 1
        return f"tc-{self._call_counter}"

    async def _send(self, msg: dict):
        """Send a message to the client via the async callback."""
        try:
            await self._send_callback(msg)
        except Exception as e:
            logger.warning("Failed to send message to client: %s", e)

    def _event_to_json(self, event: AgentEvent) -> Optional[dict]:
        """Convert an AgentEvent to a JSON-serializable dict for the client."""
        etype = event.type

        if etype == TYPE_CONTENT:
            return {"type": "token", "text": event.text, "phase": "content"}

        if etype == TYPE_THINKING:
            return {"type": "token", "text": event.text, "phase": "thinking"}

        if etype == TYPE_TOOL_CALL:
            return {
                "type": "tool_call",
                "name": event.data.get("name", ""),
                "arguments": event.data.get("arguments", {}),
            }

        if etype == TYPE_TOOL_RESULT:
            return {
                "type": "tool_result",
                "name": event.data.get("name", ""),
                "result": event.data.get("result", ""),
                "duration_ms": event.data.get("duration_ms"),
            }

        if etype == TYPE_ERROR:
            return {
                "type": "error",
                "message": event.data.get("message", ""),
                "recoverable": event.data.get("recoverable", True),
            }

        if etype == TYPE_SYSTEM:
            return {
                "type": "status",
                "message": event.text,
                "level": event.data.get("level", "info"),
            }

        if etype == TYPE_APPROVAL_REQUEST:
            call_id = self._next_call_id()
            return {
                "type": "tool_approval_request",
                "call_id": call_id,
                "name": event.data.get("name", ""),
                "arguments": event.data.get("arguments", {}),
                "reason": event.data.get("reason", ""),
            }

        if etype == TYPE_DONE:
            return {"type": "done", "session_id": event.data.get("session_id", "")}

        return None

    async def run(self, prompt: str) -> None:
        """Run one prompt and stream all events to the WebSocket client."""
        async for event in self.core.run(prompt):
            if self._interrupted:
                break
            msg = self._event_to_json(event)
            if msg is not None:
                await self._send(msg)

            if event.type == TYPE_APPROVAL_REQUEST:
                call_id = msg["call_id"] if msg else self._next_call_id()
                pa = PendingApproval(call_id, event.data.get("name", ""), event.data.get("arguments", {}))
                async with self._approval_lock:
                    self._pending_approvals[call_id] = pa

                # Wait for client approval (with 5-minute timeout)
                try:
                    await asyncio.wait_for(pa.event.wait(), timeout=300)
                except asyncio.TimeoutError:
                    await self._send({
                        "type": "tool_result",
                        "call_id": call_id,
                        "output": "[Approval timed out after 5 minutes]",
                        "error": "timeout",
                    })
                    continue

                async with self._approval_lock:
                    self._pending_approvals.pop(call_id, None)

                if not pa.approved:
                    reason = pa.denied_reason or "User denied"
                    await self._send({
                        "type": "tool_result",
                        "call_id": call_id,
                        "output": f"[{reason}]",
                        "error": "denied",
                    })

    async def approve_tool(self, call_id: str, approved: bool, reason: Optional[str] = None) -> bool:
        """Called by the WebSocket handler when a client approves/denies a tool."""
        async with self._approval_lock:
            pa = self._pending_approvals.get(call_id)
            if pa is None:
                logger.warning("Approval for unknown call_id: %s", call_id)
                return False
            pa.approved = approved
            pa.denied_reason = reason
            pa.event.set()
            return True

    def interrupt(self) -> None:
        """Interrupt the current run."""
        self._interrupted = True
        self.core._interrupted = True
