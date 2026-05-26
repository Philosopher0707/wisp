"""WebSocket transport for Wisp.

Replaces: ad-hoc WebSocket handling in server.py.
Clean separation: transport owns the wire protocol, runtime owns the logic.

Design:
  - Accepts WebSocket connections
  - Associates each connection with a session
  - Routes incoming messages to runtime.run_turn()
  - Streams events back to the client
  - Handles disconnections gracefully
  - Implements bidirectional approval flow (Issue 8)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import Transport

logger = logging.getLogger(__name__)

# Default timeout for approval responses from the WebSocket client
_APPROVAL_TIMEOUT = 60.0


class WebSocketTransport(Transport):
    """WebSocket transport layer."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self._connections: dict[int, dict] = {}
        self._counter = 0
        self._current_ws: Any = None
        self._pending_approval: asyncio.Future | None = None

    # ── Transport ABC implementation ────────────────────────────────

    async def send(self, event: dict) -> None:
        """Send an event to the CURRENT active WebSocket connection."""
        if self._current_ws is not None:
            try:
                await self._current_ws.send_json(event)
            except Exception:
                pass
        else:
            # Fallback: broadcast to all connections (legacy compat)
            dead: list[int] = []
            for conn_id, conn in list(self._connections.items()):
                try:
                    await conn["ws"].send_json(event)
                except Exception:
                    dead.append(conn_id)
            for conn_id in dead:
                self._connections.pop(conn_id, None)

    async def recv(self) -> str | None:
        """Receive a prompt from the WebSocket.

        Note: WebSocket transport uses handle() + receive_message()
        for full lifecycle. This method is for compatibility with
        the Transport ABC.
        """
        return None  # WebSocket uses async message handlers

    async def approve(self, tool_call: dict) -> bool:
        """Request explicit approval via the WebSocket client.

        Sends an approval_request event and waits for the client
        to respond with a tool_approval message (handled by
        receive_message or resolve_approval).
        """
        if self._current_ws is None:
            return True  # fallback auto-approve when no active connection

        # If already waiting, deny to prevent re-entrant approval
        if self._pending_approval is not None and not self._pending_approval.done():
            return False

        self._pending_approval = asyncio.get_event_loop().create_future()
        try:
            await self._current_ws.send_json({
                "type": "approval_request",
                "tool_call": tool_call,
            })
            return await asyncio.wait_for(self._pending_approval, timeout=_APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Approval timed out for tool %s", tool_call.get("name", "unknown"))
            return False
        except Exception:
            logger.exception("Approval request failed")
            return False
        finally:
            self._pending_approval = None

    def resolve_approval(self, approved: bool) -> None:
        """Resolve a pending approval request.

        Called by the WebSocket route handler when the client sends
        a tool_approval message.
        """
        if self._pending_approval is not None and not self._pending_approval.done():
            self._pending_approval.set_result(approved)

    def start(self) -> None:
        """Start the transport."""
        logger.debug("WebSocketTransport started")

    def stop(self) -> None:
        """Stop the transport."""
        logger.debug("WebSocketTransport stopped")

    # ── WebSocket-specific methods ────────────────────────────────

    async def handle(self, ws: Any, session_id: str, model: str, workspace: str) -> None:
        """Handle a new WebSocket connection."""
        self._counter += 1
        conn_id = self._counter

        # Create or load session
        session = await self.runtime.get_or_create_session(
            session_id=session_id,
            model=model,
            workspace=workspace,
        )

        # Track connection
        self._connections[conn_id] = {
            "ws": ws,
            "session": session,
        }

        # Set current connection for targeted send/approve
        self._current_ws = ws

        # Send ready event
        await ws.send_json({"type": "ready", "session_id": session_id})

        # Store conn_id on ws for later lookup
        ws._wisp_conn_id = conn_id

    async def receive_message(self, ws: Any, message: dict) -> None:
        """Handle an incoming message from a WebSocket."""
        conn_id = getattr(ws, "_wisp_conn_id", None)
        if conn_id is None or conn_id not in self._connections:
            await ws.send_json({"type": "error", "message": "Not connected"})
            return

        conn = self._connections[conn_id]
        session = conn["session"]

        msg_type = message.get("type")
        if msg_type == "user":
            prompt = message.get("text", "")
            try:
                async for event in self.runtime.run_turn(session, prompt, approval_handler=self.approve):
                    await ws.send_json(event)
            except Exception as exc:
                logger.exception("Error during turn")
                await ws.send_json({"type": "error", "message": str(exc)})
        elif msg_type == "tool_approval":
            approved = message.get("approved", False)
            self.resolve_approval(approved)
        else:
            await ws.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})

    async def disconnect(self, ws: Any) -> None:
        """Handle WebSocket disconnection."""
        conn_id = getattr(ws, "_wisp_conn_id", None)
        if conn_id is not None:
            self._connections.pop(conn_id, None)
        if ws is self._current_ws:
            self._current_ws = None
        if not ws.closed:
            await ws.close()
