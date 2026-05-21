"""Async WebSocket client for the Wisp TUI.

Dispatches incoming messages to registered callbacks and provides
send methods for user actions. Runs a background connection loop
with automatic reconnect via asyncio tasks.
"""

from __future__ import annotations

import asyncio
import errno
import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

TokenCallback = Callable[[str, str], Awaitable[None]]
ToolCallCallback = Callable[[str, dict[str, Any]], Awaitable[None]]
ToolResultCallback = Callable[[str, str, float], Awaitable[None]]
ApprovalCallback = Callable[[str, str, dict[str, Any], str], Awaitable[None]]
CompleteCallback = Callable[[str], Awaitable[None]]
ErrorMessageCallback = Callable[[str], Awaitable[None]]
StatusCallback = Callable[[str, str], Awaitable[None]]


class WispWSClient:
    """WebSocket bridge between the TUI and the Wisp backend server."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self._ws: Any = None
        self._running = False
        self._connect_task: asyncio.Task[None] | None = None

        self.on_token: TokenCallback | None = None
        self.on_tool_call: ToolCallCallback | None = None
        self.on_tool_result: ToolResultCallback | None = None
        self.on_approval_request: ApprovalCallback | None = None
        self.on_complete: CompleteCallback | None = None
        self.on_error: ErrorMessageCallback | None = None
        self.on_status: StatusCallback | None = None

    async def connect(self) -> None:
        """Start the background connection loop."""
        self._running = True
        self._connect_task = asyncio.create_task(self._connect_loop())

    async def _connect_loop(self) -> None:
        """Reconnect loop. Reports first failure once, then retries silently."""
        _did_report = False
        _attempts = 0
        _max_attempts = 20
        _base_delay = 3.0
        while self._running and _attempts < _max_attempts:
            try:
                import websockets
                ws_url = self.server_url.replace("http://", "ws://").replace("https://", "wss://")
                ws_url = f"{ws_url}/ws/agent"
                async with websockets.connect(ws_url) as ws:
                    self._ws = ws
                    if self.on_status:
                        await self.on_status("Connected", "info")
                    _did_report = False
                    _attempts = 0
                    await self._listen(ws)
            except ConnectionRefusedError:
                logger.debug("WebSocket connection refused — server not running")
                if self._running and self.on_status and not _did_report:
                    await self.on_status("Server not running — run `wisp server` to start", "warning")
                    _did_report = True
                _attempts += 1
                delay = min(_base_delay * (1.5 ** _attempts), 60.0)
                await asyncio.sleep(delay)
            except OSError as e:
                err_name = errno.errorcode.get(e.errno, f"errno {e.errno}") if e.errno else "?"
                logger.debug("WebSocket OS error: %s: %s", err_name, e.strerror or str(e))
                if self._running and self.on_status and not _did_report:
                    await self.on_status("Server not running — run `wisp server` to start", "warning")
                    _did_report = True
                _attempts += 1
                delay = min(_base_delay * (1.5 ** _attempts), 60.0)
                await asyncio.sleep(delay)
            except Exception as e:
                logger.debug("WebSocket error: %s: %s", type(e).__name__, e)
                if self._running and self.on_status and not _did_report:
                    await self.on_status("Server not running — run `wisp server` to start", "warning")
                    _did_report = True
                _attempts += 1
                delay = min(_base_delay * (1.5 ** _attempts), 60.0)
                await asyncio.sleep(delay)
            finally:
                self._ws = None
        if _attempts >= _max_attempts and self.on_status:
            await self.on_status("Disconnected — max reconnect attempts reached", "error")

    async def _listen(self, ws: Any) -> None:
        """Read loop for incoming messages."""
        try:
            async for raw in ws:
                await self._dispatch(raw)
        except Exception:
            pass

    async def _dispatch(self, raw: str) -> None:
        """Parse and dispatch a single JSON message."""
        try:
            msg = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return

        msg_type = msg.get("type", "")

        if msg_type == "token":
            if self.on_token:
                await self.on_token(msg.get("phase", "content"), msg.get("text", ""))
        elif msg_type == "tool_call":
            if self.on_tool_call:
                await self.on_tool_call(msg.get("name", ""), msg.get("arguments", {}))
        elif msg_type == "tool_result":
            if self.on_tool_result:
                await self.on_tool_result(
                    msg.get("name", ""),
                    msg.get("result", ""),
                    msg.get("duration_ms", 0),
                )
        elif msg_type == "tool_approval_request":
            if self.on_approval_request:
                await self.on_approval_request(
                    msg.get("call_id", ""),
                    msg.get("name", ""),
                    msg.get("arguments", {}),
                    msg.get("reason", ""),
                )
        elif msg_type == "done":
            if self.on_complete:
                await self.on_complete(msg.get("session_id", ""))
        elif msg_type == "error":
            if self.on_error:
                await self.on_error(msg.get("message", "Unknown error"))
        elif msg_type == "status":
            if self.on_status:
                await self.on_status(msg.get("message", ""), msg.get("level", "info"))

    async def send_prompt(self, content: str, session_id: str | None = None, model: str | None = None) -> None:
        if self._ws:
            await self._ws.send(json.dumps({
                "type": "prompt",
                "content": content,
                "session_id": session_id,
                "model": model,
            }))

    async def approve_tool(self, call_id: str, approved: bool) -> None:
        if self._ws:
            await self._ws.send(json.dumps({
                "type": "tool_approval",
                "id": call_id,
                "approved": approved,
            }))

    async def interrupt(self) -> None:
        if self._ws:
            await self._ws.send(json.dumps({"type": "interrupt"}))

    async def new_session(self) -> None:
        if self._ws:
            await self._ws.send(json.dumps({"type": "new_session"}))

    async def disconnect(self) -> None:
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._connect_task:
            self._connect_task.cancel()
            try:
                await self._connect_task
            except asyncio.CancelledError:
                pass
