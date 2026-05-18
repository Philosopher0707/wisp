"""WispAgentCore — stateless turn engine.

Replaces: the stateful WispAgentCore in wisp/core/agent.py.
All state is injected or passed as parameters.

Design:
  - Receives session dict, prompt, and dependencies
  - Builds system prompt from context
  - Streams events from provider
  - Parses tool calls, checks security, executes via extensions
  - Yields events for the transport to consume
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)


@dataclass
class WispAgentCore:
    """Stateless turn engine."""

    provider: Any
    security: Any
    extensions: Any
    telemetry: Any

    async def turn(self, session: dict, prompt: str) -> AsyncIterator[dict]:
        """Run one turn, yielding events."""
        # Build messages list
        messages = list(session.get("messages", []))
        messages.append({"role": "user", "content": prompt})

        # Build system prompt
        system_prompt = self._build_system_prompt(session)

        # Get tools from extensions
        tools = self.extensions.tools()

        # Stream events from provider
        for event in self.provider.generate_stream_events(
            system_prompt=system_prompt,
            messages=messages,
            tools=tools if tools else None,
        ):
            # Normalize event
            normalized = self._normalize_event(event)

            # Check security for tool calls
            if normalized.get("type") == "tool_call":
                action = self._make_action(normalized)
                context = self._make_context(session)
                decision = self.security.check(action, context)
                if not decision.allowed:
                    yield {
                        "type": "error",
                        "message": f"Blocked: {decision.reason}",
                        "recoverable": True,
                    }
                    continue

                # Check extensions
                ext_result = self.extensions.intercept(normalized)
                if ext_result.get("action") == "block":
                    yield {
                        "type": "error",
                        "message": f"Blocked: {ext_result.get('reason', 'by extension')}",
                        "recoverable": True,
                    }
                    continue

            yield normalized

    def _build_system_prompt(self, session: dict) -> str:
        """Build system prompt from session context."""
        parts = [
            "You are Wisp, a local-first coding agent.",
            f"Workspace: {session.get('workspace', '.')}",
        ]
        if session.get("compaction_history"):
            parts.append(f"Session compacted {len(session['compaction_history'])} times.")
        return "\n".join(parts)

    def _normalize_event(self, event: dict) -> dict:
        """Normalize provider event to standard format."""
        if isinstance(event, dict):
            return event
        # Handle object-style events
        result = {"type": getattr(event, "type", "unknown")}
        if hasattr(event, "__dict__"):
            result.update(event.__dict__)
        return result

    def _make_action(self, event: dict) -> Any:
        """Create Action from tool_call event."""
        from wisp.infra.security import Action
        return Action(
            name=event.get("name", ""),
            args=event.get("arguments", {}),
        )

    def _make_context(self, session: dict) -> Any:
        """Create Context from session."""
        from pathlib import Path
        from wisp.infra.security import Context
        return Context(workspace=Path(session.get("workspace", ".")))
