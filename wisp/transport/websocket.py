"""WebSocket transport for Wisp.

Replaces: ad-hoc WebSocket handling in server.py.
Clean separation: transport owns the wire protocol, runtime owns the logic.

Design:
  - Accepts WebSocket connections
  - Associates each connection with a session
  - Routes incoming messages to runtime.run_turn()
  - Streams events back to the client
  - Handles disconnections gracefully
"""

from __future__ import annotations

import logging
from typing import Any

from .base import Transport

logger = logging.getLogger(__name__)


class WebSocketTransport(Transport):
    """WebSocket transport layer."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self._connections: dict[int, dict] = {}
        self._counter = 0
        self._current_ws: Any = None

    # ── Transport ABC implementation ────────────────────────────────

    async def send(self, event: dict) -> None:
        """Send an event to the CURRENT active WebSocket connection.

        Targets the most recently used connection (set by handle()).
        For targeted sends to a specific connection, use send_to().
        """
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
        """WebSocket transport requires explicit approval.

        In a full implementation, this would send an approval request
        and wait for the client's response. For now, auto-approve.
        """
        return True

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
                async for event in self.runtime.run_turn(session, prompt):
                    await ws.send_json(event)
            except Exception as exc:
                logger.exception("Error during turn")
                await ws.send_json({"type": "error", "message": str(exc)})
        else:
            await ws.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})

    async def disconnect(self, ws: Any) -> None:
        """Handle WebSocket disconnection."""
        conn_id = getattr(ws, "_wisp_conn_id", None)
        if conn_id is not None:
            self._connections.pop(conn_id, None)
        if not ws.closed:
            await ws.close()
