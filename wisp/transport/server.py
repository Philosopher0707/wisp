"""Backward-compatibility shim — re-exports from new transports.

The new server transports are wisp.transport.websocket.WebSocketTransport
and wisp.transport.sse.SSETransport (both implement Transport ABC).
This module preserves imports for code still using the old ServerTransport.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from wisp.transport.websocket import WebSocketTransport as _WebSocketTransport
from wisp.core.events import (
    AgentEvent,
    TYPE_CONTENT,
    TYPE_THINKING,
    TYPE_TOOL_CALL,
    TYPE_TOOL_RESULT,
    TYPE_ERROR,
    TYPE_SYSTEM,
    TYPE_APPROVAL_REQUEST,
    TYPE_DONE,
    TYPE_STEERING_PAUSED,
    TYPE_STEERING_INJECT,
    TYPE_STEERING_RESUMED,
)

__all__ = ["ServerTransport", "PendingApproval"]


def _redact_sensitive_tool_args(args: dict) -> dict:
    """Redact known sensitive fields from tool arguments before sending to client.

    Keys matched (case-insensitive): api_key, token, password, secret,
    credential, auth, bearer, authorization, client_secret, ssh_key,
    private_key, access_token, refresh_token.
    """
    if not isinstance(args, dict):
        return args
    sensitive_patterns = {
        "api_key", "token", "password", "secret", "credential", "auth",
        "bearer", "authorization", "client_secret", "ssh_key", "private_key",
        "access_token", "refresh_token",
    }
    redacted = {}
    for key, value in args.items():
        key_lower = key.lower().replace("-", "_")
        if any(p in key_lower for p in sensitive_patterns):
            if isinstance(value, str) and len(value) > 4:
                redacted[key] = value[:4] + "***"
            else:
                redacted[key] = "***"
        else:
            redacted[key] = value
    return redacted


class PendingApproval:
    """Represents a tool call waiting for client approval (async-aware)."""

    def __init__(self, call_id: str, name: str, args: dict):
        self.call_id = call_id
        self.name = name
        self.arguments = args
        self.event = asyncio.Event()
        self.approved: bool = False
        self.denied_reason: Optional[str] = None


class ServerTransport(_WebSocketTransport):
    """Backward-compatible ServerTransport with old API surface."""

    def __init__(self, core, send_callback=None):
        # Pass a dummy runtime to satisfy the ABC constructor
        super().__init__(runtime=core)
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
        if self._send_callback is not None:
            try:
                await self._send_callback(msg)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("Failed to send message to client: %s", e)

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
            # Approval requests are handled inline by the approval handler
            # callback passed to core.run(); we must NOT generate a separate
            # call_id here to avoid counter skew and client confusion.
            return None

        if etype == TYPE_STEERING_PAUSED:
            return {
                "type": "steering_paused",
                "reason": event.data.get("reason", ""),
            }

        if etype == TYPE_STEERING_INJECT:
            return {
                "type": "steering_inject",
                "text": event.data.get("text", ""),
            }

        if etype == TYPE_STEERING_RESUMED:
            return {
                "type": "steering_resumed",
            }

        if etype == TYPE_DONE:
            return {
                "type": "done",
                "session_id": event.data.get("session_id", ""),
                "turns": event.data.get("turns", 0),
                "reason": event.data.get("reason", "natural"),
            }

        return None

    async def _request_approval(self, name: str, args: dict, reason: str) -> tuple[bool, Optional[dict]]:
        """Send an approval request to the client and await the response."""
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

    async def approve_tool(self, call_id: str, approved: bool, reason: Optional[str] = None) -> bool:
        """Called by the WebSocket handler when a client approves/denies a tool."""
        async with self._approval_lock:
            pa = self._pending_approvals.get(call_id)
            if pa is None:
                import logging
                logging.getLogger(__name__).warning("Approval for unknown call_id: %s", call_id)
                return False
            pa.approved = approved
            pa.denied_reason = reason
            pa.event.set()
            return True

    def interrupt(self) -> None:
        """Interrupt the current run."""
        self._interrupted = True
        if hasattr(self, "core") and hasattr(self.core, "_interrupted"):
            self.core._interrupted = True
        # Unpause so stop works while paused
        if hasattr(self, "core") and hasattr(self.core, "_paused") and not self.core._paused.is_set():
            self.core._paused.set()

    def pause(self) -> None:
        """Pause agent execution at next checkpoint."""
        if hasattr(self, "core") and hasattr(self.core, "pause"):
            self.core.pause()

    def resume(self, injected_text: Optional[str] = None) -> None:
        """Resume agent execution, optionally with steering feedback."""
        if hasattr(self, "core") and hasattr(self.core, "resume"):
            self.core.resume(injected_text)

    async def run(self, prompt: str, images: Optional[list[str]] = None) -> None:
        """Run one prompt and stream all events to the WebSocket client."""
        if hasattr(self, "core") and hasattr(self.core, "run"):
            async for event in self.core.run(
                prompt, approval_handler=self._request_approval, images=images
            ):
                if self._interrupted:
                    break
                msg = self._event_to_json(event)
                if msg is not None:
                    await self._send(msg)
