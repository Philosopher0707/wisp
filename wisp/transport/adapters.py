"""Transport adapters — wrap legacy transports for Transport ABC compatibility.

Provides:
  - CLITransportAdapter: wraps old wisp.transport.cli.CLITransport
  - ServerTransportAdapter: wraps old wisp.transport.server.ServerTransport

These adapters allow legacy transports to be used with the new
AgentRuntime and CompositionRoot architecture.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import Transport

logger = logging.getLogger(__name__)


class CLITransportAdapter(Transport):
    """Adapter for legacy CLITransport.

    Wraps the old CLI transport (which owns the core and drives the loop)
    to implement the Transport ABC (which is driven by the runtime).

    Note: The old CLITransport is a "driver" transport — it owns the core
    and drives the REPL loop. This adapter is a "driven" transport that
    delegates to the old transport for rendering but doesn't drive the loop.
    """

    def __init__(self, core: Any):
        self._core = core
        self._started = False

    def start(self) -> None:
        """Start the adapter."""
        self._started = True
        logger.debug("CLITransportAdapter started")

    def stop(self) -> None:
        """Stop the adapter."""
        self._started = False
        logger.debug("CLITransportAdapter stopped")

    async def send(self, event: dict) -> None:
        """Send an event to the CLI.

        The old CLITransport doesn't have a send() method — it renders
        events directly from the core's event stream. This adapter logs
        the event for debugging.
        """
        event_type = event.get("type", "unknown")
        text = event.get("text", "")
        logger.debug("[CLI Adapter] %s: %s", event_type, text[:100])

    async def recv(self) -> str | None:
        """Receive a prompt from the CLI.

        The old CLITransport handles input directly. This adapter returns
        None since the old transport drives its own input loop.
        """
        return None

    async def approve(self, tool_call: dict) -> bool:
        """DO NOT auto-approve tool calls through this adapter.

        The old CLITransport has its own approval mechanism.
        Returning False lets the caller fall through to the real
        approval handler instead of bypassing it.
        """
        return False


class ServerTransportAdapter(Transport):
    """Adapter for legacy ServerTransport.

    Wraps the old ServerTransport (which bridges WebSocket clients)
    to implement the Transport ABC.
    """

    def __init__(self, core: Any, send_fn: Any):
        self._core = core
        self._send_fn = send_fn
        self._started = False

    def start(self) -> None:
        """Start the adapter."""
        self._started = True
        logger.debug("ServerTransportAdapter started")

    def stop(self) -> None:
        """Stop the adapter."""
        self._started = False
        logger.debug("ServerTransportAdapter stopped")

    async def send(self, event: dict) -> None:
        """Send an event to the WebSocket client."""
        if self._send_fn is not None:
            try:
                await self._send_fn(event)
            except Exception as exc:
                logger.warning("ServerTransportAdapter send() failed: %s", exc)

    async def recv(self) -> str | None:
        """Receive a prompt from the WebSocket client.

        The old ServerTransport handles WebSocket messages directly.
        This adapter returns None since the old transport drives its own loop.
        """
        return None

    async def approve(self, tool_call: dict) -> bool:
        """DO NOT auto-approve tool calls through this adapter.

        The old ServerTransport has its own approval mechanism via
        PendingApproval. This adapter previously auto-approved all calls,
        which bypassed the real approval mechanism. We now return False
        so the caller (e.g. AgentRuntime) will fall through to the
        ServerTransport's own approval handler instead.
        """
        return False
