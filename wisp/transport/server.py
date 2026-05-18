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
    TYPE_STEERING_PAUSED,
    TYPE_STEERING_INJECT,
    TYPE_STEERING_RESUMED,
)

logger = logging.getLogger(__name__)


def create_swarm_progress_callback(send_fn):
    """Return an async callback that maps OrchestratorEvent → WS messages.

    The send_fn is an async callable that takes a dict and sends it to
    the WebSocket client. Use this as the progress_callback when running
    a SwarmOrchestrator from a WebSocket handler.
    """
    from wisp.multi_agent.task import OrchestratorEvent as SwarmEvent

    async def _on_event(evt: SwarmEvent) -> None:
        msg = evt.to_ws_message()
        if msg is not None:
            await send_fn(msg)

    return _on_event


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
        self._call_id_lock = asyncio.Lock()
        self._call_counter = 0
        self._interrupted = False

    async def _next_call_id(self) -> str:
        async with self._call_id_lock:
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
            payload = {
                "type": "tool_result",
                "name": event.data.get("name", ""),
                "result": event.data.get("result", ""),
                "duration_ms": event.data.get("duration_ms"),
            }
            if event.data.get("auto_approved"):
                payload["auto_approved"] = True
            return payload

        if etype == TYPE_ERROR:
            return {
                "type": "error",
                "message": event.data.get("message", ""),
                "recoverable": event.data.get("recoverable", True),
            }

        if etype == TYPE_SYSTEM:
            return {
                "type": "status",
                "message": event.data.get("message", ""),
                "level": event.data.get("level", "info"),
            }

        if etype == TYPE_APPROVAL_REQUEST:
            # Approval requests are handled inline by the approval_handler
            # callback passed to core.run(); we must NOT generate a separate
            # call_id here to avoid counter skew and client confusion.
            return None

        if etype == TYPE_DONE:
            return {"type": "done", "session_id": event.data.get("session_id", ""), "turns": event.data.get("turns", 0), "reason": event.data.get("reason", "natural")}

        if etype == TYPE_STEERING_PAUSED:
            return {"type": "steering_paused", "reason": event.data.get("reason", "")}

        if etype == TYPE_STEERING_INJECT:
            return {"type": "steering_inject", "text": event.data.get("text", "")}

        if etype == TYPE_STEERING_RESUMED:
            return {"type": "steering_resumed"}

        return None

    async def _request_approval(self, name: str, args: dict, reason: str) -> tuple[bool, Optional[dict]]:
        """Send an approval request to the client and await the response.

        This is the canonical approval handler passed to WispAgentCore.run().
        Extracted as a method so it can be unit-tested independently.
        """
        call_id = await self._next_call_id()
        await self._send({
            "type": "tool_approval_request",
            "call_id": call_id,
            "name": name,
            "arguments": args,
            "reason": reason,
        })
        pa = PendingApproval(call_id, name, args)
        async with self._approval_lock:
            self._pending_approvals[call_id] = pa

        try:
            await asyncio.wait_for(pa.event.wait(), timeout=300)
        except asyncio.TimeoutError:
            return (False, None)
        finally:
            async with self._approval_lock:
                self._pending_approvals.pop(call_id, None)
        return (pa.approved, None)

    async def run(self, prompt: str, images: Optional[list[str]] = None) -> None:
        """Run one prompt and stream all events to the WebSocket client."""
        async for event in self.core.run(
            prompt, approval_handler=self._request_approval, images=images
        ):
            if self._interrupted:
                break
            msg = self._event_to_json(event)
            if msg is not None:
                await self._send(msg)

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
        # Unpause so stop works while paused
        if not self.core._paused.is_set():
            self.core._paused.set()

    def pause(self) -> None:
        """Pause agent execution at next checkpoint."""
        self.core.pause()

    def resume(self, injected_text: Optional[str] = None) -> None:
        """Resume agent execution, optionally with steering feedback."""
        self.core.resume(injected_text)
