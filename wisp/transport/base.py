"""Transport ABC — the interface all UIs implement.

Decouples the core from any specific transport (CLI, TUI, WebSocket, SSE).

Design:
  - send(event): send an event to the user
  - recv(): receive a prompt from the user
  - approve(tool_call): ask user to approve a tool call
  - start()/stop(): lifecycle management
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Transport(ABC):
    """Abstract base class for all transports."""

    @abstractmethod
    async def send(self, event: dict[str, Any]) -> None:
        """Send an event to the user.

        Events are standardized dictionaries:
          - {"type": "content", "text": "..."}
          - {"type": "tool_call", "name": "...", "arguments": {...}}
          - {"type": "tool_result", "name": "...", "result": "..."}
          - {"type": "error", "message": "...", "recoverable": True}
          - {"type": "done"}
        """
        ...

    @abstractmethod
    async def recv(self) -> str | None:
        """Receive a prompt from the user.

        Returns the user's input text, or None if the transport
        has been closed/disconnected.
        """
        ...

    @abstractmethod
    async def approve(self, tool_call: dict[str, Any]) -> bool:
        """Ask the user to approve a tool call.

        Returns True if approved, False if rejected.
        For transports that don't support interactive approval
        (like SSE), this may always return True or use a policy.
        """
        ...

    @abstractmethod
    def start(self) -> None:
        """Start the transport. Called before the first turn."""
        ...

    @abstractmethod
    def stop(self) -> None:
        """Stop the transport. Called after the last turn."""
        ...
