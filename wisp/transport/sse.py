"""SSE transport for Wisp.

Replaces: ad-hoc SSE handling in server.py.
Clean separation: transport owns the wire protocol, runtime owns the logic.

Design:
  - Accepts HTTP connections and establishes SSE streams
  - Routes incoming messages to runtime.run_turn()
  - Streams events back as SSE formatted data
  - Buffers events for reconnection support
  - Handles disconnections gracefully
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SSETransport:
    """SSE transport layer."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self._sessions: dict[str, dict] = {}
        self._event_buffers: dict[str, list[tuple[int, dict]]] = {}
        self._event_counter = 0

    async def connect(self, response: Any, session_id: str, model: str, workspace: str) -> None:
        """Handle a new SSE connection."""
        session = await self.runtime.get_or_create_session(
            session_id=session_id,
            model=model,
            workspace=workspace,
        )

        self._sessions[session_id] = {
            "response": response,
            "session": session,
        }

        if session_id not in self._event_buffers:
            self._event_buffers[session_id] = []

        # Send SSE headers
        await response.send("HTTP/1.1 200 OK\r\n")
        await response.send("Content-Type: text/event-stream\r\n")
        await response.send("Cache-Control: no-cache\r\n")
        await response.send("Connection: keep-alive\r\n")
        await response.send("\r\n")

        # Send ready event
        await self._send_event(response, {"type": "ready", "session_id": session_id})

    async def receive_message(self, response: Any, message: dict) -> None:
        """Handle an incoming message for an SSE connection."""
        # Find session by response
        session_id = None
        for sid, data in self._sessions.items():
            if data["response"] is response:
                session_id = sid
                break

        if session_id is None:
            await self._send_event(response, {"type": "error", "message": "Not connected"})
            return

        session = self._sessions[session_id]["session"]
        msg_type = message.get("type")

        if msg_type == "user":
            prompt = message.get("text", "")
            try:
                async for event in self.runtime.run_turn(session, prompt):
                    await self._send_event(response, event)
                    # Buffer for reconnection
                    self._event_counter += 1
                    self._event_buffers[session_id].append((self._event_counter, event))
                    # Keep buffer size reasonable
                    if len(self._event_buffers[session_id]) > 100:
                        self._event_buffers[session_id] = self._event_buffers[session_id][-100:]
            except Exception as exc:
                logger.exception("Error during turn")
                await self._send_event(response, {"type": "error", "message": str(exc)})
        else:
            await self._send_event(response, {"type": "error", "message": f"Unknown message type: {msg_type}"})

    async def reconnect(self, response: Any, session_id: str, last_event_id: str) -> None:
        """Handle reconnection with last-event-id."""
        # Send headers
        await response.send("HTTP/1.1 200 OK\r\n")
        await response.send("Content-Type: text/event-stream\r\n")
        await response.send("Cache-Control: no-cache\r\n")
        await response.send("Connection: keep-alive\r\n")
        await response.send("\r\n")

        # Send buffered events after last_event_id
        if session_id in self._event_buffers:
            last_id = int(last_event_id) if last_event_id.isdigit() else 0
            for event_id, event in self._event_buffers[session_id]:
                if event_id > last_id:
                    await self._send_event(response, event, event_id=event_id)

    async def _send_event(self, response: Any, event: dict, event_id: int | None = None) -> None:
        """Send an event in SSE format."""
        lines = []
        if event_id is not None:
            lines.append(f"id: {event_id}")
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
        await response.send("\n".join(lines) + "\n")
