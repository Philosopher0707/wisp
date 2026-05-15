"""Backward-compatible WispAgent — thin wrapper around WispAgentCore + CLITransport.

All I/O code lives in wisp.transport.cli. This module re-exports helpers
that external code depends on and provides the synchronous run()/repl() API.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

# Re-export helpers from transport layer for backward compatibility
from wisp.transport.cli import (
    _is_interactive,
    _input_line,
    _args_preview,
)

from wisp.core.agent import WispAgentCore
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
        super().__init__(config=config, session=session, agent_id=agent_id, role=role)

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
        """Backward compat: run one turn via core events, return raw response dict.

        No terminal output — used by orchestrator for programmatic access.
        Delegates directly to the sync core method to avoid thread overhead.
        """
        return super()._run_turn_streaming(system)

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
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No loop running — safe to use asyncio.run()
            return asyncio.run(coro)

        # Already inside a running loop — need a dedicated thread
        import threading

        result: dict[str, object] = {}

        def _target():
            nloop = asyncio.new_event_loop()
            try:
                result["value"] = nloop.run_until_complete(coro)
            except Exception as exc:
                result["error"] = exc
            finally:
                nloop.close()

        t = threading.Thread(target=_target)
        t.start()
        t.join()

        if "error" in result:
            raise result["error"]  # type: ignore[misc]
        return result.get("value")
