"""Headless transport — collects events into memory for programmatic access.

Implements Transport ABC without any I/O. Useful for:
  - Headless mode (wisp print)
  - Background runs
  - Testing
"""

from __future__ import annotations

import logging
from typing import Any

from .base import Transport

logger = logging.getLogger(__name__)


class HeadlessTransport(Transport):
    """Transport that collects events into memory.

    No user interaction — auto-approves all tool calls.
    Events are stored in `.events` for inspection.
    """

    def __init__(self):
        self.events: list[dict] = []
        self._started = False

    def start(self) -> None:
        self._started = True
        self.events = []
        logger.debug("HeadlessTransport started")

    def stop(self) -> None:
        self._started = False
        logger.debug("HeadlessTransport stopped")

    async def send(self, event: dict) -> None:
        """Store event in memory."""
        self.events.append(dict(event))

    async def recv(self) -> str | None:
        """Headless transport does not receive user input."""
        return None

    async def approve(self, tool_call: dict) -> bool:
        """Auto-approve all tool calls in headless mode."""
        return True

    def collect_result(self) -> dict:
        """Build a result dict from collected events.

        Returns:
            Dict with keys: content, thinking, tool_calls, errors, iterations
        """
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[dict] = []
        errors: list[dict] = []
        iterations = 0

        for event in self.events:
            etype = event.get("type")
            if etype == "content":
                content_parts.append(event.get("text", ""))
            elif etype == "thinking":
                thinking_parts.append(event.get("text", ""))
            elif etype == "tool_call":
                tool_calls.append({
                    "name": event.get("name", ""),
                    "args": event.get("arguments", {}),
                    "result": "",
                })
            elif etype == "tool_result":
                name = event.get("name", "")
                for tc in reversed(tool_calls):
                    if tc["name"] == name and not tc.get("result"):
                        tc["result"] = event.get("result", "")
                        tc["duration_ms"] = event.get("duration_ms")
                        break
            elif etype == "error":
                errors.append({
                    "message": event.get("message", ""),
                    "recoverable": event.get("recoverable", True),
                })
            elif etype == "done":
                iterations = event.get("turns", 0)

        return {
            "ok": len(errors) == 0,
            "content": "\n".join(content_parts),
            "thinking": "\n".join(thinking_parts) if thinking_parts else "",
            "tool_calls": tool_calls,
            "errors": errors,
            "iterations": iterations,
        }
