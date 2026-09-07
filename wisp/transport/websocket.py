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
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from wisp.approval_state import SessionPolicy

from .base import Transport

logger = logging.getLogger(__name__)

# Default timeout for approval responses from the WebSocket client
_APPROVAL_TIMEOUT = 60.0

# Active (session_id, ws) for the turn currently executing on this task.
# stream_turn() sets it; approve() reads it so concurrent turns on distinct
# connections route approvals to their own client instead of the last-writer
# _current_ws. When unset, approve() falls back to the legacy
# (self._session_id, self._current_ws) pair.
_active_turn: ContextVar[tuple[str | None, Any] | None] = ContextVar(
    "wisp_ws_active_turn", default=None
)


class WebSocketTransport(Transport):
    """WebSocket transport layer."""

    def __init__(self, runtime: Any):
        self.runtime = runtime
        self._connections: dict[int, dict] = {}
        self._counter = 0
        self._current_ws: Any = None
        self._session_id: str | None = None
        self._session_ws: dict[str, Any] = {}
        self._approvals: dict[str, dict] = {}
        self._approval_counter = 0

    @staticmethod
    @contextmanager
    def _turn_context(sid: str | None, ws: Any):
        """Mark (sid, ws) as the active turn on this task (ContextVar)."""
        token = _active_turn.set((sid, ws))
        try:
            yield
        finally:
            _active_turn.reset(token)

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

        With no connected client there is no human to approve: deny
        unless WISP_WS_AUTO_APPROVE is explicitly opted in.
        """
        import os

        ctx = _active_turn.get()
        if ctx is not None:
            sid, ws = ctx
        else:
            sid, ws = self._session_id, self._current_ws

        if ws is None:
            if os.environ.get("WISP_WS_AUTO_APPROVE", "").strip().lower() == "true":
                logger.warning(
                    "Auto-approving tool %s with no client connected "
                    "(WISP_WS_AUTO_APPROVE=true)", tool_call.get("name", "unknown"),
                )
                return True
            logger.warning(
                "Denying tool %s: no client connected to approve it",
                tool_call.get("name", "unknown"),
            )
            return False

        # Session memory short-circuits the prompt entirely — same
        # precedence as CLITransport, applied server-side so memory
        # belongs to the session, not whichever client is attached.
        name = str(tool_call.get("name", "unknown"))
        if sid:
            state = self.runtime.approval_state(sid)
            policy = state.session_policy
            if policy is SessionPolicy.AUTO:
                return True
            if policy is SessionPolicy.BLOCK:
                return False
            if name in state.allowed_tools:
                return True
            if name in state.denied_tools:
                return False

        self._approval_counter += 1
        tool_id = tool_call.get("id") or f"{name}:{self._approval_counter}"
        approval_id = f"{sid}:{tool_id}"

        # Re-entrant guard is per-KEY: the same approval id unresolved
        # denies, but concurrent DISTINCT approvals proceed.
        existing = self._approvals.get(approval_id)
        if existing is not None and not existing["future"].done():
            return False

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._approvals[approval_id] = {
            "future": future,
            "ws": ws,
            "sid": sid,
            "tool_name": name,
        }
        try:
            await ws.send_json({
                "type": "approval_request",
                "approval_id": approval_id,
                "tool_call": tool_call,
            })
            return await asyncio.wait_for(future, timeout=_APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Approval timed out for tool %s", tool_call.get("name", "unknown"))
            return False
        except Exception:
            logger.exception("Approval request failed")
            return False
        finally:
            self._approvals.pop(approval_id, None)

    def resolve_approval(self, approved: bool, approval_id: str | None = None) -> bool:
        """Resolve a pending approval request.

        Called by the WebSocket route handler when the client sends
        a tool_approval message. With approval_id, resolves that entry
        (falls back to single-unresolved entry when unknown, else no-op).
        Without it, resolves iff exactly one entry is unresolved
        (back-compat), else no-op. Returns True iff an entry resolved.
        """
        if approval_id is not None:
            entry = self._approvals.get(approval_id)
            if entry is not None and not entry["future"].done():
                entry["future"].set_result(approved)
                return True
            # Unknown id (old-protocol client or non-echoed id): fall back
            # to single-unresolved-entry behavior so the gate does not
            # stay shut until the 60s timeout.
            pending = [
                e for e in self._approvals.values()
                if not e["future"].done()
            ]
            if len(pending) == 1:
                pending[0]["future"].set_result(approved)
                return True
            return False
        pending = [
            entry for entry in self._approvals.values()
            if not entry["future"].done()
        ]
        if len(pending) == 1:
            pending[0]["future"].set_result(approved)
            return True
        return False

    def resolve_decision(self, key: str, approval_id: str | None = None) -> bool:
        """Fold a y/Y/n/N/a/d/c decision into session memory and resolve.

        Returns False when nothing was pending. The verdict for this
        call comes from runtime.apply_approval_decision; 'c' cancels by
        resolving False (the turn unwinds at the gate). Uses the ENTRY's
        sid/tool so concurrent sessions fold into their own memory.
        """
        if approval_id is not None:
            entry = self._approvals.get(approval_id)
            if entry is None or entry["future"].done():
                # Unknown id: fall back to single-unresolved entry so
                # old-protocol clients still resolve the gate.
                pending = [
                    e for e in self._approvals.values()
                    if not e["future"].done()
                ]
                if len(pending) != 1:
                    return False
                entry = pending[0]
            sid = entry["sid"] or ""
            tool = entry["tool_name"] or "unknown"
            approved = self.runtime.apply_approval_decision(sid, tool, key)
            entry["future"].set_result(approved)
            return True
        pending = [
            entry for entry in self._approvals.values()
            if not entry["future"].done()
        ]
        if len(pending) != 1:
            return False
        entry = pending[0]
        sid = entry["sid"] or ""
        tool = entry["tool_name"] or "unknown"
        approved = self.runtime.apply_approval_decision(sid, tool, key)
        entry["future"].set_result(approved)
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
        self._session_id = session_id
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
        self._session_ws[session_id] = ws

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
            await self.stream_turn(ws, session, prompt)
        elif msg_type == "tool_approval":
            approved = message.get("approved", False)
            self.resolve_approval(approved)
        else:
            await ws.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})

    def session_for(self, ws: Any) -> dict[str, Any] | None:
        """Session attached to *ws*, or None when unhandled."""
        conn_id = getattr(ws, "_wisp_conn_id", None)
        conn = self._connections.get(conn_id) if conn_id is not None else None
        return conn["session"] if conn else None

    async def stream_turn(self, ws: Any, session: dict[str, Any], prompt: str) -> None:
        """Run one turn for *ws*, streaming events as they arrive.

        Split out of receive_message so callers can run it as its OWN task:
        a connection that executes turns inline cannot read approval or
        interrupt frames while a turn runs — the approval future's only
        resolvers live on the reader loop, so interactive approval
        deadlocked until timeout and auto-denied.
        """
        if isinstance(session, dict):
            sid = session.get("id", self._session_id)
        else:
            sid = self._session_id
        with self._turn_context(sid, ws):
            try:
                async for event in self.runtime.run_turn(
                    session, prompt, approval_handler=self.approve,
                ):
                    await ws.send_json(event)
            except Exception as exc:
                logger.exception("Error during turn")
                await ws.send_json({"type": "error", "message": str(exc)})

    async def disconnect(self, ws: Any) -> None:
        """Handle WebSocket disconnection."""
        conn_id = getattr(ws, "_wisp_conn_id", None)
        if conn_id is not None:
            self._connections.pop(conn_id, None)
        # Fail closed: deny every pending approval belonging to this ws
        # immediately so turns do not hang to the 60s timeout.
        for key in [
            key for key, entry in self._approvals.items()
            if entry["ws"] is ws
        ]:
            entry = self._approvals.pop(key)
            if not entry["future"].done():
                logger.warning(
                    "Denying approval %s: client disconnected", key,
                )
                entry["future"].set_result(False)
        for sid, mapped in list(self._session_ws.items()):
            if mapped is ws:
                del self._session_ws[sid]
        if ws is self._current_ws:
            self._current_ws = None
        if not ws.closed:
            await ws.close()
