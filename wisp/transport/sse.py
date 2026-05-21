"""SSE transport for Wisp.

Provides an asyncio.Queue-based event stream that can be consumed by
FastAPI's StreamingResponse. The raw HTTP protocol is handled by the
framework, not by this class.

Usage in a FastAPI endpoint::

    from fastapi import Request
    from fastapi.responses import StreamingResponse

    @router.get("/api/sse")
    async def sse_endpoint(request: Request):
        transport = SSETransport(request.app.state.root.runtime)
        transport.start()

        async def event_stream():
            async for event in transport.event_stream():
                yield f"data: {json.dumps(event)}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class SSETransport:
    """Queue-based SSE event transport.

    Does NOT inherit from Transport ABC because SSE requires
    framework-level StreamingResponse support.
    """

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
        self._sessions: dict[str, dict] = {}
        self._event_counter = 0
        self._started = False

    def start(self) -> None:
        """Start the transport."""
        self._started = True
        logger.debug("SSETransport started")

    def stop(self) -> None:
        """Stop the transport."""
        self._started = False
        logger.debug("SSETransport stopped")

    async def send(self, event: dict) -> None:
        """Queue an event for streaming."""
        if self._started:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("SSE queue full — dropping event")

    async def event_stream(self):
        """Async generator yielding events for StreamingResponse."""
        while self._started:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                yield event
            except asyncio.TimeoutError:
                # Send keep-alive comment to prevent proxy timeouts
                yield {"type": "_keepalive"}

    async def handle_turn(self, session_id: str, model: str, workspace: str, prompt: str) -> None:
        """Run a turn and stream events into the queue."""
        session = await self.runtime.get_or_create_session(
            session_id=session_id,
            model=model,
            workspace=workspace,
        )

        await self._queue.put({"type": "ready", "session_id": session_id})

        try:
            async for event in self.runtime.run_turn(session, prompt):
                await self._queue.put(event)
        except Exception as exc:
            logger.exception("Error during turn")
            await self._queue.put({"type": "error", "message": str(exc)})

    def format_sse(self, event: dict) -> str:
        """Format a dict as an SSE data line."""
        if event.get("type") == "_keepalive":
            return ":\n\n"
        return f"data: {json.dumps(event)}\n\n"
