"""Multi transport — broadcasts events to multiple transports.

Implements Transport ABC by forwarding send/recv/approve/start/stop
to a list of child transports. Useful for:
  - Logging to file while streaming to WebSocket
  - Collecting events in memory while printing to CLI
  - Audit trails with multiple outputs
"""

from __future__ import annotations

import logging
from typing import Any

from .base import Transport

logger = logging.getLogger(__name__)


class MultiTransport(Transport):
    """Transport that broadcasts to multiple child transports.

    Example:
        transport = MultiTransport([
            CLITransport(runtime),
            FileTransport("/tmp/agent.log"),
            HeadlessTransport(),
        ])
    """

    def __init__(self, transports: list[Transport]):
        self.transports = list(transports)
        self._started = False

    def start(self) -> None:
        """Start all child transports."""
        self._started = True
        for t in self.transports:
            try:
                t.start()
            except Exception as exc:
                logger.warning("Failed to start transport %s: %s", t.__class__.__name__, exc)

    def stop(self) -> None:
        """Stop all child transports."""
        self._started = False
        for t in self.transports:
            try:
                t.stop()
            except Exception as exc:
                logger.warning("Failed to stop transport %s: %s", t.__class__.__name__, exc)

    async def send(self, event: dict) -> None:
        """Broadcast event to all child transports."""
        for t in self.transports:
            try:
                await t.send(event)
            except Exception as exc:
                logger.warning("Send failed on %s: %s", t.__class__.__name__, exc)

    async def recv(self) -> str | None:
        """Receive from the first transport that returns a value.

        Tries each transport in order until one returns non-None.
        """
        for t in self.transports:
            try:
                result = await t.recv()
                if result is not None:
                    return result
            except Exception as exc:
                logger.warning("Recv failed on %s: %s", t.__class__.__name__, exc)
        return None

    async def approve(self, tool_call: dict) -> bool:
        """Approve if ANY child transport approves.

        Returns True if at least one transport approves.
        This is useful when one transport is interactive (CLI)
        and others are passive (file logging).
        """
        for t in self.transports:
            try:
                if await t.approve(tool_call):
                    return True
            except Exception as exc:
                logger.warning("Approve failed on %s: %s", t.__class__.__name__, exc)
        return False
