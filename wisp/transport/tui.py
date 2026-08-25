"""TUI transport for Wisp.

Implements Transport ABC for the Textual-based terminal UI.
Bridges the local AgentRuntime to the Textual app with the same
approval contract as the CLI: y/Y/a/n/N/d/c, session-scoped tool
memory, and honest cancel (c unwinds the turn).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from textual.message import Message

from wisp.approval_state import ApprovalSessionState, SessionPolicy
from wisp.infra.security import redact_sensitive_tool_args

from .base import Transport

logger = logging.getLogger(__name__)

_APPROVAL_KEYS = {"y", "Y", "a", "n", "N", "d", "c"}


class ApprovalRequested(Message):
    """Posted to the TUI app when a tool call needs user approval."""

    def __init__(self, tool_name: str, args_text: str) -> None:
        self.tool_name = tool_name
        self.args_text = args_text
        super().__init__()


class TUIApprovalController:
    """Approval gate matching CLITransport semantics, TUI-keyed.

    Session memory lives in ApprovalSessionState so y/Y/a/n/N behave
    identically across CLI and TUI. ``resolve`` is called from the UI
    thread; ``approve`` runs inside the turn.
    """

    def __init__(self, notify=None, state: ApprovalSessionState | None = None) -> None:
        # External state lets WS and local paths share one session memory.
        self.state = state or ApprovalSessionState()
        self._notify = notify  # callable(ApprovalRequested) or None
        self._event = asyncio.Event()
        self._result: str | None = None

    def _preview(self, tool_call: dict) -> tuple[str, str]:
        name = str(tool_call.get("name", "unknown"))
        args = redact_sensitive_tool_args(tool_call.get("arguments", {}) or {})
        args_text = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:3])
        if len(args) > 3:
            args_text += ", ..."
        return name, args_text

    async def approve(self, tool_call: dict) -> bool:
        name, _ = self._preview(tool_call)
        # Session memory first — same precedence as CLITransport.approve:
        # policy short-circuits before per-tool sets; should_ask's BLOCK
        # branch intentionally never reaches the prompt path.
        if self.state.session_policy is SessionPolicy.AUTO:
            return True
        if self.state.session_policy is SessionPolicy.BLOCK:
            return False
        if name in self.state.allowed_tools:
            return True
        if name in self.state.denied_tools:
            return False

        _, args_text = self._preview(tool_call)
        self._result = None
        self._event.clear()
        if self._notify is not None:
            try:
                self._notify(ApprovalRequested(name, args_text))
            except Exception:
                logger.warning("approval notify failed", exc_info=True)
        await self._event.wait()
        key = self._result or "n"
        if key == "y":
            return True
        if key == "Y":
            self.state.allow_tool(name)
            return True
        if key == "a":
            self.state.set_auto()
            return True
        if key == "N":
            self.state.deny_tool(name)
            return False
        if key == "d":
            self.state.set_block()
            return False
        if key == "c":
            raise asyncio.CancelledError(
                f"User cancelled the turn at the approval prompt for {name}"
            )
        return False  # unknown key denies once, like the CLI

    def resolve(self, key: str) -> bool:
        """Deliver a key press; returns False if no approval was pending."""
        key = str(key).strip()
        if key not in _APPROVAL_KEYS:
            return False
        self._result = key
        self._event.set()
        return True


class TUITransport(Transport):
    """TUI transport layer — bridges agent runtime to Textual app."""

    def __init__(self, app: Any | None = None):
        self._app = app
        self._started = False
        self._pending_prompt: str | None = None
        self._prompt_event = asyncio.Event()
        # Approval state — set by TUI when user presses y/n/a/d
        self._pending_approval: dict | None = None
        self._approval_event = asyncio.Event()
        self._approval_result: bool = False

    # ── Transport ABC implementation ────────────────────────────────

    async def send(self, event: dict) -> None:
        """Send an event to the TUI.

        Posts the event to the TUI's message queue for rendering.
        """
        if self._started and self._app is not None:
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

        Waits for the user to press y/n/a/d keybindings in the
        WorkspaceScreen. Uses an asyncio.Event to bridge between
        the agent runtime and the TUI event loop.
        """
        if self._started and self._app is not None:
            try:
                tool_name = tool_call.get("name", "?")
                args = tool_call.get("arguments", {})
                logger.info("TUI approval requested for: %s", tool_name)

                # Post a custom message to the TUI so the workspace screen
                # can show the pending approval request in the status bar
                self._pending_approval = tool_call
                self._approval_event.clear()
                self._approval_result = False

                if hasattr(self._app, "post_message"):
                    from wisp.tui.screens.workspace import ApprovalRequested
                    self._app.post_message(ApprovalRequested(tool_name, str(args)))

                # Wait for the user to respond (y/n/a/d)
                await self._approval_event.wait()
                self._pending_approval = None
                return self._approval_result
            except Exception as exc:
                logger.warning("TUITransport approve() failed: %s", exc)
                return False
        # No TUI app available → deny the tool call (security default)
        return False

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

    def set_approval(self, approved: bool) -> None:
        """Called by the TUI when user approves or denies a tool call."""
        self._approval_result = approved
        self._approval_event.set()

    def submit_prompt(self, prompt: str) -> None:
        """Called by the TUI when user submits input."""
        self._pending_prompt = prompt
        self._prompt_event.set()
