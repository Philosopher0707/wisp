"""WebSocket connection management.

Extracted from legacy server.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

GRACEFUL_SHUTDOWN_SECONDS = 10.0


class Connection:
    """Represents a single WebSocket connection."""

    def __init__(self, websocket: WebSocket, client_id: str):
        self.websocket = websocket
        self.client_id = client_id
        self.agent_task: asyncio.Task | None = None
        self.transport: Any = None
        self._run_lock = asyncio.Lock()
        self.swarm_task: asyncio.Task | None = None
        self.swarm_orchestrator: Any = None
        self.core: Any = None

    async def send(self, msg: dict):
        try:
            await self.websocket.send_text(json.dumps(msg))
        except Exception as e:
            logger.warning("Failed to send to %s: %s", self.client_id, e)

    async def stop_tasks(self, timeout: float = 2.0) -> None:
        """Gracefully stop any running agent/swarm tasks."""
        tasks_to_wait: list[asyncio.Task] = []

        if self.transport:
            try:
                self.transport.interrupt()
            except Exception:
                pass
        if self.agent_task and not self.agent_task.done():
            tasks_to_wait.append(self.agent_task)
        if self.swarm_task and not self.swarm_task.done():
            self.swarm_task.cancel()
            tasks_to_wait.append(self.swarm_task)

        if not tasks_to_wait:
            return

        logger.info(
            "Waiting up to %.1fs for tasks on client %s to finish...",
            timeout, self.client_id,
        )
        done, pending = await asyncio.wait(
            tasks_to_wait, timeout=timeout, return_when=asyncio.ALL_COMPLETED,
        )

        for t in pending:
            t.cancel()
            try:
                await asyncio.wait_for(t, timeout=1.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        if pending:
            logger.warning(
                "Force-cancelled %d task(s) for client %s during shutdown",
                len(pending), self.client_id,
            )


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, Connection] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, client_id: str) -> Connection:
        await websocket.accept()
        conn = Connection(websocket, client_id)

        async with self._lock:
            stale = self._connections.get(client_id)
            self._connections[client_id] = conn

        if stale is not None:
            logger.warning("Replacing stale connection for %s", client_id)
            await stale.stop_tasks(timeout=1.0)

        logger.info("Client %s connected", client_id)
        return conn

    async def disconnect(self, client_id: str):
        async with self._lock:
            conn = self._connections.pop(client_id, None)
        if conn:
            await conn.stop_tasks(timeout=2.0)
        logger.info("Client %s disconnected", client_id)

    async def shutdown_gracefully(self, timeout: float = GRACEFUL_SHUTDOWN_SECONDS) -> None:
        """Gracefully shut down all active WebSocket connections."""
        async with self._lock:
            connections = list(self._connections.values())
        if not connections:
            logger.info("No active connections; shutdown is immediate")
            return

        logger.info(
            "Graceful shutdown: stopping %d active connection(s)...", len(connections),
        )

        for conn in connections:
            await conn.stop_tasks(timeout=timeout)

        logger.info("Shutting down global MCP manager...")
        try:
            from wisp.mcp import shutdown_global_mcp_manager
            shutdown_global_mcp_manager()
        except Exception as e:
            logger.warning("MCP manager shutdown error: %s", e)

        logger.info("Shutting down session store...")
        try:
            from wisp.adapters import get_store
            store = get_store()
            if hasattr(store, "close"):
                store.close()
        except Exception as e:
            logger.warning("Session store shutdown error: %s", e)

        logger.info("Graceful shutdown complete")

    async def send(self, client_id: str, msg: dict):
        conn = self._connections.get(client_id)
        if conn:
            await conn.send(msg)


manager = ConnectionManager()
