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
from wisp.core.events import normalize_event

logger = logging.getLogger(__name__)


class HeadlessTransport(Transport):
    """Transport that collects events into memory.

    No user interaction — by default it does NOT auto-approve tool calls.
    Set ``auto_approve=True`` only when the caller explicitly opts in.
    Events are stored in ``.events`` for inspection.
    """

    def __init__(self, *, auto_approve: bool = False):
        self.events: list[dict] = []
        self._started = False
        self._auto_approve = auto_approve

    def start(self) -> None:
        self._started = True
        self.events = []
        logger.debug("HeadlessTransport started (auto_approve=%s)", self._auto_approve)

    def stop(self) -> None:
        self._started = False
        logger.debug("HeadlessTransport stopped")

    async def send(self, event: dict | Any) -> None:
        """Store event in memory.

        Normalizes any event format (AgentEvent, canonical dict, flat dict)
        to a consistent flat dict for easy access.
        """
        normalized = normalize_event(event)
        # Flatten to simple dict for easy access
        flat = dict(normalized.data)
        flat["type"] = str(normalized.type)
        flat["timestamp"] = normalized.timestamp
        self.events.append(flat)

    async def recv(self) -> str | None:
        """Headless transport does not receive user input."""
        return None

    async def approve(self, tool_call: dict) -> bool:
        """Respect the configured ``auto_approve`` flag.

        By default returns ``False`` so dangerous tools are not silently
        executed in headless / programmatic contexts.
        """
        if not self._auto_approve:
            logger.warning(
                "HeadlessTransport denied tool '%s' — auto_approve=False. "
                "Pass auto_approve=True only if you fully trust the prompt.",
                tool_call.get("name", "unknown"),
            )
        return self._auto_approve

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
            # Deltas concatenate with NO separator: some providers stream
            # per-character chunks, and "\n".join exploded them into one
            # char per line (live stealth/ox-alpha E2E, 2026-08-26).
            "content": "".join(content_parts),
            "thinking": "".join(thinking_parts),
            "tool_calls": tool_calls,
            "errors": errors,
            "iterations": iterations,
        }
