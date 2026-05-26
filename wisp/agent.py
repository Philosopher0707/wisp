"""Backward-compatible WispAgent — thin wrapper around WispAgentCore + CLITransport.

All I/O code lives in wisp.transport.cli. This module re-exports helpers
that external code depends on and provides the synchronous run()/repl() API.
"""

from __future__ import annotations

import logging
from typing import Optional

from wisp.async_utils import run_sync_coro

# Re-export helpers from transport layer for backward compatibility

from wisp.transport.cli import (
    _is_interactive,  # noqa: F401
    _input_line,      # noqa: F401
    _args_preview,    # noqa: F401
)

from wisp.core.engine import WispAgentCore
from wisp.transport.cli import CLITransport

logger = logging.getLogger(__name__)

# ── WispAgent (backward-compatible wrapper) ──────────────────────────

class WispAgent(WispAgentCore):
    """The main agent — synchronous API, backward compatible with all existing code.

    Internally delegates logic to WispAgentCore and I/O to CLITransport.
    ServerAgent subclasses this. SubagentRunner is deprecated — use
    SubagentOrchestrator from wisp.multi_agent instead.
    """

    def __init__(
        self,
        config: Optional[object] = None,
        session: Optional[object] = None,
        agent_id: Optional[str] = None,
        role: Optional[str] = None,
    ):
        super().__init__(config=config)
        self.session = session
        self.agent_id = agent_id
        self.role = role
        self._interrupted = False
        self._active_skill: Optional[str] = None

    # ── Synchronous public API ─────────────────────────────────────

    def run(self, prompt: str, skill_name: Optional[str] = None, session_id: Optional[str] = None):
        """Execute the agent (single-shot mode) with streaming output."""
        transport = CLITransport(self)
        transport.run(prompt, skill_name=skill_name, session_id=session_id)

    def repl(self, skill_name: Optional[str] = None, session_id: Optional[str] = None):
        """Interactive REPL — continuous conversation until the user exits."""
        transport = CLITransport(self)
        transport.repl(skill_name, session_id)

    def _run_turn_streaming(self, system: str) -> dict:
        """Sync-over-async wrapper: run one turn via core.turn(), collect events, return response dict.

        Used by AcpSession and /continue for programmatic access without terminal output.
        Returns dict with 'message' key containing content, thinking, and tool_calls.
        """
        # Build a session dict from self.messages (backward compat)
        session_dict: dict = {
            "id": getattr(self, "agent_id", None) or "default",
            "model": getattr(self.config, "model", "unknown"),
            "workspace": getattr(self.config, "workspace", "."),
            "messages": list(getattr(self, "messages", [])),
        }

        # Derive prompt from last user message
        messages = session_dict.get("messages", [])
        prompt = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                prompt = msg.get("content", "")
                break

        events = run_sync_coro(self._collect_turn_events(session_dict, prompt))

        # Assemble response dict from collected events
        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_calls: list[dict] = []

        for event in events:
            etype = event.get("type", "")
            if etype == "content":
                content_parts.append(event.get("text", ""))
            elif etype == "thinking":
                thinking_parts.append(event.get("text", ""))
            elif etype == "tool_call":
                tc = {
                    "id": event.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": event.get("name", ""),
                        "arguments": event.get("arguments", {}),
                    },
                }
                tool_calls.append(tc)

        message = {"content": "".join(content_parts)}
        if thinking_parts:
            message["thinking"] = "".join(thinking_parts)
        if tool_calls:
            message["tool_calls"] = tool_calls

        return {"message": message}

    async def _collect_turn_events(self, session_dict: dict, prompt: str) -> list[dict]:
        """Collect all events from one turn into a list."""
        events: list[dict] = []
        async for event in self.turn(session_dict, prompt):
            events.append(event)
        return events

    def _execute_loop(self, system: str, workspace: str, auto_approve: bool = True):
        """Execute a single turn (used by /continue and programmatic callers).

        Creates a temporary transport, runs the async turn, and shuts down.
        """
        transport = CLITransport(self)
        self.config.auto_approve = auto_approve
        self._safe_run_sync(transport._execute_turn(system, workspace))

    # ── Helpers ────────────────────────────────────────────────────

    def _safe_run_sync(self, coro):
        """Run a coroutine, handling both standalone and nested event-loop contexts."""
        return run_sync_coro(coro)