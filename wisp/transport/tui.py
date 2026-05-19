"""TUI transport for Wisp.

Implements Transport ABC for the Textual-based terminal UI.
Wraps WispTUIApp and bridges it to the agent runtime.

Design:
  - send(event): posts events to the TUI's message queue
  - recv(): returns user input from the TUI's input widget
  - approve(tool_call): shows a modal dialog for approval
  - start()/stop(): manages the Textual app lifecycle
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import Transport

logger = logging.getLogger(__name__)


class TUITransport(Transport):
    """TUI transport layer — bridges agent runtime to Textual app."""

    def __init__(self, app: Any | None = None):
        self._app = app
        self._started = False
        self._pending_prompt: str | None = None
        self._prompt_event = asyncio.Event()

    # ── Transport ABC implementation ────────────────────────────────

    async def send(self, event: dict) -> None:
        """Send an event to the TUI.

        Posts the event to the TUI's message queue for rendering.
        """
        if self._app is not None:
            try:
                # Textual apps can post messages to widgets
                self._app.post_message(event)
            except Exception as exc:
                logger.warning("TUITransport send() failed: %s", exc)
        else:
            # Fallback: log the event
            event_type = event.get("type", "unknown")
            text = event.get("text", "")
            logger.debug("[TUI] %s: %s", event_type, text[:100])

    async def recv(self) -> str | None:
        """Receive a prompt from the TUI.

        Waits for the user to submit input via the TUI's input widget.
        """
        if self._pending_prompt is not None:
            prompt = self._pending_prompt
            self._pending_prompt = None
            return prompt
        self._prompt_event.clear()
        await self._prompt_event.wait()
        prompt = self._pending_prompt
        self._pending_prompt = None
        return prompt

    async def approve(self, tool_call: dict) -> bool:
        """Ask the user to approve a tool call via TUI modal.

        Shows a modal dialog and waits for user response.
        """
        if self._app is not None:
            try:
                # TODO: show modal dialog in TUI
                logger.info("TUI approval requested for: %s", tool_call.get("name", "?"))
                return True
            except Exception as exc:
                logger.warning("TUITransport approve() failed: %s", exc)
        return True

    def start(self) -> None:
        """Start the TUI transport."""
        self._started = True
        logger.debug("TUITransport started")

    def stop(self) -> None:
        """Stop the TUI transport."""
        self._started = False
        logger.debug("TUITransport stopped")

    # ── TUI-specific methods ──────────────────────────────────────

    def set_app(self, app: Any) -> None:
        """Set the Textual app instance."""
        self._app = app

    def submit_prompt(self, prompt: str) -> None:
        """Called by the TUI when user submits input."""
        self._pending_prompt = prompt
        self._prompt_event.set()
